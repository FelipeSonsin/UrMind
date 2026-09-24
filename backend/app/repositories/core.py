"""Persistência do núcleo no PostgreSQL/PostGIS (SQLAlchemy 2 + psycopg 3).

Única camada do backend que emite SQL (§31.19). Captura, detecção e evento
precisam de PostGIS (ST_DWithin, KNN, ST_ClosestPoint) e transação real.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from geoalchemy2 import Geometry
from sqlalchemy import func, select, text, tuple_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.core import (
    ActionCatalog,
    AuditLog,
    Capture,
    DatasetVersion,
    Detection,
    Event,
    EventContext,
    ModelVersion,
    ResponsibilityRule,
    Review,
    RiskAssessment,
    RoadSegment,
)
from app.schemas.core import CaptureCreate, Coordinate, EventCreate, NearbyQuery, PhotoGatePolicy

if TYPE_CHECKING:
    from app.services.osm_import import RoadWay

# Cast sem typmod: ST_X/ST_Y só aceitam geometry, e "geometry(GEOMETRY,-1)"
# não é um tipo válido no PostGIS.
GEOM_CAST = Geometry(geometry_type=None, srid=-1)
logger = logging.getLogger(__name__)


def _promotion_quality_approved(model: ModelVersion) -> bool:
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    return (
        metrics.get("quality_classification") == "APPROVED"
        and isinstance(metrics.get("frozen_test_quality"), dict)
        and metrics["frozen_test_quality"].get("passed") is True
        and isinstance(metrics.get("benchmark"), dict)
        and metrics["benchmark"].get("passed") is True
        and isinstance(metrics.get("closure_artifact"), dict)
        and bool(metrics["closure_artifact"].get("path"))
        and bool(metrics["closure_artifact"].get("sha256"))
    )


def _point(coordinate: Coordinate):
    """Coordenada → geography(Point,4326). Ordem PostGIS é (lon, lat)."""
    return func.ST_SetSRID(func.ST_MakePoint(coordinate.longitude, coordinate.latitude), 4326).cast(
        Event.point.type
    )


class QuotaExceededError(RuntimeError):
    """The shared public image budget is exhausted."""


class QuotaUnavailableError(RuntimeError):
    """Admission persistence unavailable; API must fail closed."""


class PublicImageQuota:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def admit(
        self,
        stage: Literal["lookup", "download"],
        caller_hash: str,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        try:
            await self._admit(stage, caller_hash, resource_id)
        except SQLAlchemyError:
            raise QuotaUnavailableError("quota persistence unavailable") from None

    async def _admit(
        self,
        stage: Literal["lookup", "download"],
        caller_hash: str,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        if stage not in {"lookup", "download"} or not re.fullmatch("[0-9a-f]{64}", caller_hash):
            raise ValueError("invalid quota scope")
        if (stage == "download") != (resource_id is not None):
            raise ValueError("invalid quota resource")
        caller_limit, global_limit, resource_limit = (
            (120, 6000, None) if stage == "lookup" else (60, 60, 30)
        )
        async with self.sessions.begin() as session:
            await session.execute(text("set local statement_timeout = '5s'"))
            await session.execute(text("set local lock_timeout = '2s'"))
            # Stable two-int namespace; transaction locks are safe with pooling.
            await session.execute(
                text("select pg_advisory_xact_lock(197045, :stage_id)"),
                {"stage_id": 1 if stage == "lookup" else 2},
            )
            # Read time AFTER waiting for the lock, not transaction start time.
            now = await session.scalar(text("select clock_timestamp()"))
            params = {"stage": stage, "caller": caller_hash, "resource": resource_id, "now": now}
            await session.execute(
                text(
                    "delete from public.public_image_admissions where stage = :stage "
                    "and admitted_at <= cast(:now as timestamptz) - interval '60 seconds'"
                ),
                params,
            )
            counts = (
                (
                    await session.execute(
                        text(
                            "select count(*) as total, "
                            "count(*) filter (where caller_hash = :caller) as caller, "
                            "count(*) filter (where resource_id = cast(:resource as uuid)) as resource "
                            "from public.public_image_admissions where stage = :stage"
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            rejected = (
                counts["total"] >= global_limit
                or counts["caller"] >= caller_limit
                or (resource_limit is not None and counts["resource"] >= resource_limit)
            )
            if not rejected:
                await session.execute(
                    text(
                        "insert into public.public_image_admissions "
                        "(stage, caller_hash, resource_id, admitted_at) "
                        "values (:stage, :caller, :resource, :now)"
                    ),
                    params,
                )
        # Commit cleanup even for rejection; no partial multi-budget consumption.
        if rejected:
            raise QuotaExceededError("public image quota exhausted")


class GeocodingRepository:
    """Shared cache and one provider-wide lease, independent of Capture transactions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def cached(self, key: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            return await session.scalar(
                text(
                    "select payload from public.geocoding_cache where cache_key=:key and expires_at>clock_timestamp()"
                ),
                {"key": key},
            )

    async def reserve(self, token: uuid.UUID) -> bool:
        async with self.sessions() as session:
            reserved = await session.scalar(
                text("""insert into public.geocoding_leases(provider,token,expires_at)
                values ('nominatim',:token,clock_timestamp()+interval '30 seconds')
                on conflict(provider) do update set token=excluded.token,expires_at=excluded.expires_at
                where geocoding_leases.expires_at<=clock_timestamp() returning token"""),
                {"token": token},
            )
            await session.commit()
            return reserved == token

    async def cache(self, key: str, payload: dict[str, Any]) -> None:
        async with self.sessions() as session:
            await session.execute(
                text("delete from public.geocoding_cache where expires_at<=clock_timestamp()")
            )
            await session.execute(
                text("""insert into public.geocoding_cache(cache_key,payload,expires_at)
                values (:key,cast(:payload as jsonb),clock_timestamp()+interval '7 days')
                on conflict(cache_key) do update set payload=excluded.payload,expires_at=excluded.expires_at"""),
                {"key": key, "payload": json.dumps(payload)},
            )
            await session.commit()

    async def release(self, token: uuid.UUID) -> None:
        async with self.sessions() as session:
            await session.execute(
                text("""update public.geocoding_leases set expires_at=clock_timestamp()+interval '1 second'
                where provider='nominatim' and token=:token"""),
                {"token": token},
            )
            await session.commit()


class CaptureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def notify_evidence_change(self, parent: uuid.UUID) -> None:
        # Parent subscribers may not read another visitor's child Capture. Emit an
        # owner-visible parent update without exposing that visitor's identity.
        await self.session.execute(
            text("""update public.captures
            set quality=jsonb_set(coalesce(quality,'{}'::jsonb),'{evidence_updated_at}',to_jsonb(clock_timestamp()))
            where id=:parent"""),
            {"parent": parent},
        )

    async def nearby_reports(
        self, actor: str, coordinate: Coordinate, radius: float
    ) -> list[dict[str, Any]]:
        rows = await self.session.execute(
            text(
                """select c.id,c.public_id,c.protocol_code,
            ST_Distance(c.point,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography) distance_m
            from public.captures c where c.point is not null
            and c.source in ('pwa_photo','exif_upload')
            and not (c.quality ? 'additional_evidence')
            and coalesce(c.quality->'human_review'->>'status','') <> 'rejected'
            and ST_DWithin(c.point,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:radius)
            and (c.quality->>'uploaded_by'=:actor or exists(select 1 from public.events e
                where e.capture_id=c.id and """
                + PublicRepository._PUBLISHED
                + """))
            order by distance_m,c.created_at limit 10"""
            ),
            {
                "actor": actor,
                "lon": coordinate.longitude,
                "lat": coordinate.latitude,
                "radius": radius,
            },
        )
        return [dict(row) for row in rows.mappings()]

    async def has_privacy_consent(self, owner: str, version: str) -> bool:
        return bool(
            await self.session.scalar(
                text("""select exists(
            select 1 from public.capture_privacy_consents
            where owner_id=cast(:owner as uuid) and notice_version=:version)"""),
                {"owner": owner, "version": version},
            )
        )

    async def accept_privacy_notice(self, owner: str, version: str) -> None:
        await self.session.execute(
            text("""insert into public.capture_privacy_consents
            (owner_id,notice_version) values (cast(:owner as uuid),:version)
            on conflict(owner_id,notice_version) do nothing"""),
            {"owner": owner, "version": version},
        )

    async def acquire_photo_lease(self, owner: str, token: uuid.UUID) -> bool:
        await self.session.execute(
            text("delete from public.photo_admission_leases where expires_at<=clock_timestamp()")
        )
        row = await self.session.scalar(
            text("""insert into public.photo_admission_leases
            (owner_id,token,expires_at) values (cast(:owner as uuid),:token,clock_timestamp()+interval '5 minutes')
            on conflict(owner_id) do update set token=excluded.token,expires_at=excluded.expires_at
            where photo_admission_leases.expires_at<=clock_timestamp() returning token"""),
            {"owner": owner, "token": token},
        )
        return row == token

    async def release_photo_lease(self, owner: str, token: uuid.UUID) -> None:
        await self.session.execute(
            text("""delete from public.photo_admission_leases
            where owner_id=cast(:owner as uuid) and token=:token"""),
            {"owner": owner, "token": token},
        )

    async def report_markers(
        self,
        actor: str,
        can_review: bool = False,
        *,
        public: bool = False,
        limit: int = 500,
        include_unlocated: bool = False,
        after: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """One original-location marker per mobile Capture, never a fabricated Event."""
        rows = await self.session.execute(
            text(
                """
            select c.id, c.public_id, c.protocol_code, c.created_at,
                   ST_Y(c.point::geometry) latitude, ST_X(c.point::geometry) longitude,
                   c.source_location as location_source, c.accuracy_m, c.user_description,
                   c.quality->'location_conflict' as location_conflict,
                   c.quality->'photo_gate' as photo_gate,
                   c.quality->'address' as address,
                   c.quality->'human_review' as human_review,
                   c.quality->'additional_evidence' as additional_evidence,
                   (select count(distinct p.quality->>'uploaded_by') from public.captures p
                    where p.id=c.id or p.quality->'additional_evidence'->>'capture_id'=c.id::text) as reporters_count,
                   c.quality->'inference'->>'status' as processing_status,
                   c.quality->'inference'->>'model_status' as model_status,
                   e.id as event_id, e.public_id as event_public_id,
                   e.urmind_class, e.status as event_status,
                   exists(select 1 from public.reviews rv where rv.event_id=e.id) as has_review,
                   risk.severity, risk.priority_score
            from public.captures c
            left join lateral (
                select id, public_id, urmind_class, status, factors from public.events
                where capture_id = c.id or (
                    c.quality->'inference'->'event_ids' @> to_jsonb(events.id::text)
                    and exists(select 1 from public.captures owner_capture
                        where owner_capture.id=events.capture_id
                        and owner_capture.quality->>'uploaded_by'=c.quality->>'uploaded_by')
                )
                order by commit_order desc nulls last, created_at desc, id limit 1
            ) e on true
            left join lateral (
                select severity, priority_score from public.risk_assessments
                where event_id = e.id
                order by commit_order desc nulls last, assessment_sequence desc limit 1
            ) risk on true
            where c.source in ('pwa_photo', 'exif_upload')
              and (not (c.quality ? 'additional_evidence') or (:include_unlocated and not :public))
              and (c.point is not null or (:include_unlocated and not :public)
                or (not :public and e.status='confirmed'
                    and jsonb_typeof(c.quality->'human_review'->'corrected_location')='object'))
              and (:public or :reviewer or c.quality->>'uploaded_by' = :actor)
            and not (:public and exists(select 1 from public.events e where e.capture_id=c.id and
        """
                + PublicRepository._PUBLISHED
                + """))
            and (cast(:after_at as timestamptz) is null or
                 (c.created_at,c.id)<(cast(:after_at as timestamptz),cast(:after_id as uuid)))
            order by c.created_at desc, c.id desc limit :limit
        """
            ),
            {
                "actor": actor,
                "reviewer": can_review,
                "public": public,
                "limit": limit,
                "include_unlocated": include_unlocated,
                "after_at": after[0] if after else None,
                "after_id": after[1] if after else None,
            },
        )
        return [dict(row) for row in rows.mappings()]

    async def fill_missing_location(
        self, capture_id: uuid.UUID, actor: str, coordinate: Coordinate
    ) -> bool:
        """Atomic first location only. Updates trigger the existing inference queue."""
        result = await self.session.execute(
            update(Capture)
            .where(
                Capture.id == capture_id,
                Capture.point.is_(None),
                Capture.source.in_(["pwa_photo", "exif_upload"]),
                Capture.quality["uploaded_by"].astext == actor,
            )
            .values(
                point=_point(coordinate), source_location="manual", accuracy_m=coordinate.accuracy_m
            )
            .returning(Capture.id)
        )
        return result.scalar_one_or_none() is not None

    async def reserve_pending_address(
        self, capture_id: uuid.UUID | None = None
    ) -> dict[str, Any] | None:
        row = (
            (
                await self.session.execute(
                    text("""with candidate as (
            select id from public.captures where source in ('pwa_photo','exif_upload')
            and (cast(:capture_id as uuid) is null or id=cast(:capture_id as uuid))
            and (point is not null or jsonb_typeof(quality->'human_review'->'corrected_location')='object')
            and ((quality->'address'->>'status'='address_pending'
              and coalesce((quality->'address'->>'retry_at')::timestamptz,'-infinity')<=clock_timestamp())
              or (quality->'report_context'->>'status'='pending'
              and coalesce((quality->'report_context'->>'retry_at')::timestamptz,'-infinity')<=clock_timestamp()))
            order by created_at,id for update skip locked limit 1
        ) update public.captures c set quality=jsonb_set(
            jsonb_set(c.quality,'{address,retry_at}',
                to_jsonb(clock_timestamp()+interval '30 seconds'),true),
            '{report_context,retry_at}',
                to_jsonb(clock_timestamp()+interval '30 seconds'),true)
          from candidate where c.id=candidate.id returning c.id,
            c.quality->'address'->>'request_id' request_id,
            c.quality->'address'->>'status' address_status,
            c.quality->'report_context'->>'request_id' context_request_id,
            c.quality->'report_context'->>'status' context_status,
            coalesce((c.quality->'human_review'->'corrected_location'->>'latitude')::float,ST_Y(c.point::geometry)) latitude,
            coalesce((c.quality->'human_review'->'corrected_location'->>'longitude')::float,ST_X(c.point::geometry)) longitude,
            c.captured_at"""),
                    {"capture_id": capture_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def save_address(
        self, identifier: uuid.UUID, request_id: str, address: dict[str, Any]
    ) -> bool:
        # A reviewer may change the point while HTTP is pending. Do not publish the stale address.
        result = await self.session.execute(
            text("""update public.captures set quality=jsonb_set(quality,'{address}',cast(:address as jsonb))
            where id=:id and quality->'address'->>'request_id'=:request_id returning id"""),
            {"id": identifier, "request_id": request_id, "address": json.dumps(address)},
        )
        return result.scalar_one_or_none() is not None

    async def save_report_context(
        self, identifier: uuid.UUID, request_id: str, context: dict[str, Any]
    ) -> bool:
        result = await self.session.execute(
            text("""update public.captures set quality=jsonb_set(quality,'{report_context}',cast(:context as jsonb))
            where id=:id and quality->'report_context'->>'request_id'=:request_id returning id"""),
            {"id": identifier, "request_id": request_id, "context": json.dumps(context)},
        )
        return result.scalar_one_or_none() is not None

    async def create(self, payload: CaptureCreate) -> Capture:
        capture = Capture(
            capture_key=payload.capture_key,
            mission_id=payload.mission_id,
            device_id=payload.device_id,
            source=payload.source.value,
            source_location=payload.source_location.value,
            captured_at=payload.captured_at,
            storage_path=payload.storage_path,
            user_description=payload.user_description,
            point=_point(payload.coordinate) if payload.coordinate else None,
            accuracy_m=payload.coordinate.accuracy_m if payload.coordinate else None,
            heading_deg=payload.heading_deg,
            speed_mps=payload.speed_mps,
            quality=payload.quality,
            detections=[
                Detection(
                    urmind_class=detection.urmind_class.value,
                    confidence=detection.confidence,
                    bbox=detection.bbox.model_dump(),
                    model_version_id=detection.model_version_id,
                )
                for detection in payload.detections
            ],
        )
        self.session.add(capture)
        await self.session.flush()
        return capture

    async def get_by_key(self, capture_key: str) -> Capture | None:
        result = await self.session.execute(
            select(Capture).where(Capture.capture_key == capture_key)
        )
        return result.scalar_one_or_none()

    async def get_by_protocol(self, protocol: str, owner: str) -> Capture | None:
        result = await self.session.execute(
            select(Capture).where(
                Capture.protocol_code == protocol,
                Capture.quality["uploaded_by"].astext == owner,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, capture_id: uuid.UUID) -> Capture | None:
        return await self.session.get(Capture, capture_id)

    async def timeline_history(self, capture_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self.session.execute(
            text("""
            select a.operation,a.created_at,a.after_data
            from public.audit_log a
            where (a.entity_type='capture' and a.entity_id=:capture_id
                and a.operation in ('capture_review_state','capture_location',
                    'attach_report_evidence','detach_report_evidence','capture_inference'))
               or (a.entity_type='event' and a.entity_id in
                    (select id from public.events where capture_id=:capture_id)
                   and a.operation in ('publish_event','withdraw_event','review')
                   and (a.operation<>'review' or not exists(select 1 from public.audit_log b
                       where b.entity_id=:capture_id and b.operation='capture_review_state'
                       and b.after_data->>'review_id'=a.after_data->>'review_id')))
            order by a.created_at,a.id
        """),
            {"capture_id": capture_id},
        )
        return [dict(row) for row in rows.mappings()]

    async def get_for_review(self, capture_id: uuid.UUID) -> Capture | None:
        result = await self.session.execute(
            select(Capture)
            .where(Capture.id == capture_id, Capture.source.in_(["pwa_photo", "exif_upload"]))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def protocol_target(self, protocol: str) -> Capture | None:
        result = await self.session.execute(
            select(Capture).where(Capture.protocol_code == protocol)
        )
        return result.scalar_one_or_none()

    async def recent_owner_uploads(self, owner_id: str, now: datetime) -> int:
        """Count only server-received photo uploads within the sliding window."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Capture)
            .where(
                Capture.captured_at >= now - timedelta(hours=1),
                Capture.storage_path.is_not(None),
                Capture.quality["uploaded_by"].astext == owner_id,
            )
        )
        return int(result.scalar_one())

    async def recent_similar_photo(
        self, owner: str, phash: str, now: datetime, maximum_distance: int
    ) -> bool:
        """Owner-only, server receipt time; unknown legacy pHash is not a match."""
        if not re.fullmatch(r"[0-9a-f]{16}", phash) or not 0 <= maximum_distance <= 16:
            raise ValueError("invalid perceptual hash contract")
        result = await self.session.scalar(
            text("""select exists(select 1 from public.captures
                where quality->>'uploaded_by'=:owner
                and created_at >= :cutoff
                and case when quality->>'phash' ~ '^[0-9a-f]{16}$'
                    then bit_count((('x' || (quality->>'phash'))::bit(64)) #
                                   (('x' || :phash)::bit(64))) <= :distance
                    else false end)"""),
            {
                "owner": owner,
                "cutoff": now - timedelta(days=30),
                "phash": phash,
                "distance": maximum_distance,
            },
        )
        return bool(result)

    async def lock_owner_uploads(self, owner_id: str) -> None:
        """Serialize the quota check and Capture commit across API processes."""
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:owner_id, 0))"),
            {"owner_id": owner_id},
        )

    async def recent_public_uploads(self, now: datetime) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Capture)
            .where(
                Capture.captured_at >= now - timedelta(hours=1),
                Capture.storage_path.is_not(None),
                Capture.quality["public_upload"].astext == "true",
            )
        )
        return int(result.scalar_one())

    async def lock_public_uploads(self) -> None:
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended('urmind-public-photo-global', 0))")
        )

    async def location(self, capture_id: uuid.UUID) -> dict[str, Any] | None:
        """Coordenada original da captura (lat/lon/accuracy) ou None se não houver ponto."""
        result = await self.session.execute(
            select(
                func.ST_Y(func.cast(Capture.point, GEOM_CAST)).label("latitude"),
                func.ST_X(func.cast(Capture.point, GEOM_CAST)).label("longitude"),
                Capture.accuracy_m,
            ).where(Capture.id == capture_id, Capture.point.is_not(None))
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def add_detections(self, capture: Capture, detections: Sequence[Detection]) -> None:
        capture.detections.extend(detections)
        await self.session.flush()


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: EventCreate) -> Event:
        event = Event(
            event_key=payload.event_key,
            capture_id=payload.capture_id,
            mission_id=payload.mission_id,
            urmind_class=payload.urmind_class.value,
            evidence_mode=payload.evidence_mode.value,
            visual_confidence=payload.visual_confidence,
            fused_confidence=payload.fused_confidence,
            occurred_at=payload.occurred_at,
            point=_point(payload.coordinate) if payload.coordinate else None,
            location_accuracy_m=payload.coordinate.accuracy_m if payload.coordinate else None,
            factors=payload.factors,
            model_version_id=payload.model_version_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get(self, event_id: uuid.UUID) -> Event | None:
        return await self.session.get(Event, event_id)

    async def human_event(self, capture: Capture, issue_code: str) -> Event:
        """Reviewer evidence only; never synthesize Detection, confidence or ModelVersion."""
        original_point = await self.session.scalar(
            select(func.ST_AsEWKT(Capture.point)).where(Capture.id == capture.id)
        )
        event = Event(
            event_key=f"human-review-{capture.id}",
            capture_id=capture.id,
            urmind_class=issue_code,
            evidence_mode="photo" if capture.point is not None else "image_only",
            occurred_at=capture.captured_at,
            point=original_point,
            location_accuracy_m=capture.accuracy_m,
            status="review",
            factors={"origin": "human_review", "label_source": "human_review"},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def for_capture(self, capture_id: uuid.UUID) -> list[Event]:
        result = await self.session.execute(
            select(Event).where(Event.capture_id == capture_id).order_by(Event.event_sequence)
        )
        return list(result.scalars())

    async def get_for_review(self, event_id: uuid.UUID) -> Event | None:
        """Serialize votes and status changes for one event in the caller transaction."""
        result = await self.session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_key(self, event_key: str) -> Event | None:
        result = await self.session.execute(select(Event).where(Event.event_key == event_key))
        return result.scalar_one_or_none()

    async def snap_to_road(
        self, coordinate: Coordinate, max_distance_m: float = 50
    ) -> dict[str, Any] | None:
        """Delega o snap à função SQL da migration 0002 (uma ida ao banco)."""
        result = await self.session.execute(
            text(
                "select road_segment_id, distance_m, "
                "ST_Y(snapped_point::geometry) as latitude, "
                "ST_X(snapped_point::geometry) as longitude "
                "from public.snap_to_road("
                "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :max_distance)"
            ),
            {
                "lon": coordinate.longitude,
                "lat": coordinate.latitude,
                "max_distance": max_distance_m,
            },
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def attach_road(
        self,
        event: Event,
        *,
        road_segment_id: uuid.UUID,
        distance_m: float,
        latitude: float,
        longitude: float,
    ) -> Event:
        """Grava o snap sem tocar na coordenada original (§11.2)."""
        event.road_segment_id = road_segment_id
        event.distance_to_road_m = distance_m
        event.snapped_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(
            Event.snapped_point.type
        )
        await self.session.flush()
        return event

    async def find_open_near(
        self,
        *,
        urmind_class: str,
        coordinate: Coordinate,
        radius_m: float,
        since: datetime,
    ) -> Event | None:
        """Evento aberto da mesma classe perto do ponto: candidato a deduplicação (§25.9)."""
        origin = _point(coordinate)
        result = await self.session.execute(
            select(Event)
            .where(
                Event.urmind_class == urmind_class,
                Event.status.not_in(("rejected",)),
                Event.occurred_at >= since,
                Event.point.is_not(None),
                func.ST_DWithin(Event.point, origin, radius_m),
            )
            .order_by(func.ST_Distance(Event.point, origin))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def merge_factors(self, event: Event, extra: dict[str, Any]) -> Event:
        # JSONB é reatribuído inteiro: mutação in-place não é detectada pelo ORM.
        event.factors = {**(event.factors or {}), **extra}
        await self.session.flush()
        return event

    async def recurrence_on_segment(self, event: Event) -> int | None:
        if event.road_segment_id is None:
            return None
        result = await self.session.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.road_segment_id == event.road_segment_id, Event.id != event.id)
        )
        return int(result.scalar_one())

    async def previous_event_times(self, event: Event) -> tuple[list[datetime] | None, str]:
        """Only use insert-order evidence when all candidates have trusted provenance."""
        if event.road_segment_id is None:
            return None, "no_road_segment"
        if event.order_source != "serialized_commit_order" or event.commit_order is None:
            return None, "legacy_order_uncertain"
        result = await self.session.execute(
            select(Event.occurred_at, Event.order_source).where(
                Event.road_segment_id == event.road_segment_id,
                Event.id != event.id,
                Event.occurred_at < event.occurred_at,
                (Event.order_source == "legacy_backfill")
                | (Event.commit_order < event.commit_order),
            )
        )
        rows = result.all()
        if any(row.order_source != "serialized_commit_order" for row in rows):
            return None, "legacy_order_uncertain"
        return [row.occurred_at for row in rows], "serialized_commit_order"

    async def evidence_detections(self, event: Event) -> list[Detection]:
        ids = (event.factors or {}).get("evidence", {}).get("detection_ids") or []
        if not ids or event.capture_id is None:
            return []
        result = await self.session.execute(
            select(Detection).where(
                Detection.id.in_([uuid.UUID(value) for value in ids]),
                Detection.capture_id == event.capture_id,
            )
        )
        return list(result.scalars())

    async def coordinates(self, event_id: uuid.UUID) -> dict[str, Any]:
        """Ponto original e ajustado em lat/lon, para relatório e mapa."""
        snapped = func.cast(Event.snapped_point, GEOM_CAST)
        result = await self.session.execute(
            select(
                func.ST_Y(func.cast(Event.point, GEOM_CAST)).label("latitude"),
                func.ST_X(func.cast(Event.point, GEOM_CAST)).label("longitude"),
                func.ST_Y(snapped).label("snapped_latitude"),
                func.ST_X(snapped).label("snapped_longitude"),
            ).where(Event.id == event_id)
        )
        return dict(result.mappings().one())

    async def save_context(self, event_id: uuid.UUID, source: str, payload: dict[str, Any]) -> None:
        """Upsert por (event_id, source): reconsulta substitui, nunca duplica."""
        await self.session.execute(
            text(
                "insert into public.event_context "
                "(event_id, source, payload, fetched_at, ingested_at) "
                "values (:event_id, :source, cast(:payload as jsonb), "
                "clock_timestamp(), clock_timestamp()) "
                "on conflict (event_id, source) do update "
                "set payload = excluded.payload, fetched_at = excluded.fetched_at, "
                "ingested_at = excluded.ingested_at"
            ),
            {
                "event_id": event_id,
                "source": source,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )

    async def contexts(self, event_id: uuid.UUID) -> list[EventContext]:
        result = await self.session.execute(
            select(EventContext)
            .where(EventContext.event_id == event_id)
            .order_by(EventContext.source)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def segment(self, segment_id: uuid.UUID) -> RoadSegment | None:
        return await self.session.get(RoadSegment, segment_id)

    async def set_status(self, event: Event, status: str) -> Event:
        event.status = status
        await self.session.flush()
        return event

    async def rows(
        self,
        *,
        urmind_class: str | None = None,
        status: str | None = None,
        limit: int = 100,
        after: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        stmt = self._row_select().order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit)
        if after:
            stmt = stmt.where(tuple_(Event.occurred_at, Event.id) < after)
        if urmind_class:
            stmt = stmt.where(Event.urmind_class == urmind_class)
        if status:
            stmt = stmt.where(Event.status == status)
        result = await self.session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    async def nearby(self, query: NearbyQuery) -> list[dict[str, Any]]:
        """ST_DWithin filtra o candidato; ST_Distance ordena do mais próximo."""
        origin = func.ST_SetSRID(func.ST_MakePoint(query.longitude, query.latitude), 4326).cast(
            Event.point.type
        )
        stmt = (
            self._row_select()
            .add_columns(func.ST_Distance(Event.point, origin).label("distance_m"))
            .where(Event.point.is_not(None))
            .where(func.ST_DWithin(Event.point, origin, query.radius_m))
            .order_by(func.ST_Distance(Event.point, origin))
            .limit(query.limit)
        )
        result = await self.session.execute(stmt)
        return [dict(row) for row in result.mappings()]

    @staticmethod
    def _row_select():
        latitude = func.ST_Y(func.cast(Event.point, GEOM_CAST))
        longitude = func.ST_X(func.cast(Event.point, GEOM_CAST))
        snapped = func.cast(Event.snapped_point, GEOM_CAST)
        return select(
            Event.id,
            Event.event_key,
            Event.urmind_class,
            Event.evidence_mode,
            Event.status,
            Event.occurred_at,
            Event.visual_confidence,
            Event.fused_confidence,
            Event.location_accuracy_m,
            Event.road_segment_id,
            Event.distance_to_road_m,
            Event.factors,
            Event.created_at,
            latitude.label("latitude"),
            longitude.label("longitude"),
            func.ST_Y(snapped).label("snapped_latitude"),
            func.ST_X(snapped).label("snapped_longitude"),
        )


class HistoryRepository:
    """Leitura descritiva de histórico (Fase 9). Nada aqui prevê o futuro."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def snapshot_observations(self, days: int) -> dict[str, Any]:
        """One MVCC statement observes rows and the server cutoff together.

        This is a forward-only observation, not a reconstruction of an arbitrary
        past cutoff. Bounded output fails closed instead of silently truncating.
        """
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError("snapshot days must be between 1 and 3650")
        result = await self.session.execute(
            text("""
                with bounds as materialized (
                    select statement_timestamp() as cutoff,
                           statement_timestamp() - make_interval(days => :days) as start
                ), observations as (
                    select e.id as event_id, e.road_segment_id, e.urmind_class, e.status,
                           e.occurred_at, ST_Y(e.point::geometry) as latitude,
                           ST_X(e.point::geometry) as longitude
                    from public.events e cross join bounds b
                    where e.occurred_at >= b.start and e.occurred_at < b.cutoff
                      and e.status <> 'rejected'
                    order by e.occurred_at, e.id limit 10001
                )
                select jsonb_build_object(
                    'schema_version', 'urmind-history-snapshot-v1',
                    'knowledge_cutoff', b.cutoff, 'coverage_start', b.start,
                    'source', 'database_statement_mvcc_snapshot',
                    'observations', coalesce((select jsonb_agg(to_jsonb(o)
                        order by o.occurred_at, o.event_id) from observations o), '[]'::jsonb)
                ) from bounds b
            """),
            {"days": days},
        )
        payload = dict(result.scalar_one())
        if len(payload["observations"]) > 10000:
            raise ValueError("history snapshot exceeds bounded archive; narrow days")
        return payload

    async def save_snapshot(self, payload: dict[str, Any], digest: str) -> uuid.UUID:
        snapshot_id = uuid.uuid4()
        self.session.add(
            AuditLog(
                id=snapshot_id,
                operation="history_snapshot",
                entity_type="history_snapshot",
                entity_id=snapshot_id,
                actor="history_cli",
                after_data=payload,
                event_hash=digest,
            )
        )
        await self.session.flush()
        return snapshot_id

    async def load_snapshot(self, snapshot_id: uuid.UUID) -> tuple[dict[str, Any], str]:
        result = await self.session.execute(
            select(AuditLog).where(
                AuditLog.id == snapshot_id,
                AuditLog.operation == "history_snapshot",
                AuditLog.entity_type == "history_snapshot",
            )
        )
        row = result.scalar_one_or_none()
        if row is None or row.after_data is None:
            raise ValueError("history snapshot not found")
        return dict(row.after_data), row.event_hash

    async def observations(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Events observed in [start, end); human-rejected events are excluded."""
        result = await self.session.execute(
            text(
                "select e.id as event_id, e.road_segment_id, e.urmind_class, e.status, "
                "e.occurred_at, ST_Y(e.point::geometry) as latitude, "
                "ST_X(e.point::geometry) as longitude "
                "from public.events e "
                "where e.occurred_at >= :start and e.occurred_at < :end "
                "and e.status <> 'rejected' "
                "order by e.occurred_at, e.id"
            ),
            {"start": start, "end": end},
        )
        return [dict(row) for row in result.mappings()]


class RoadSegmentRepository:
    """Malha viária (§11.3). Upsert por `osm_id`: reimportar o mesmo recorte não duplica."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_osm(self, ways: Sequence[RoadWay]) -> int:
        if not ways:
            return 0
        statement = text(
            "insert into public.road_segments (osm_id, name, highway, jurisdiction, geom, attributes) "
            "values (:osm_id, :name, :highway, :jurisdiction, ST_GeomFromText(:wkt, 4326), "
            "cast(:attributes as jsonb)) "
            "on conflict (osm_id) do update set name = excluded.name, highway = excluded.highway, "
            "jurisdiction = excluded.jurisdiction, "
            "geom = excluded.geom, attributes = excluded.attributes"
        )
        await self.session.execute(
            statement,
            [
                {
                    "osm_id": way.osm_id,
                    "name": way.name,
                    "highway": way.highway,
                    "jurisdiction": way.jurisdiction,
                    "wkt": way.wkt,
                    "attributes": json.dumps(way.attributes, ensure_ascii=False),
                }
                for way in ways
            ],
        )
        return len(ways)

    async def batch_summary(self, label: str) -> dict[str, Any]:
        """Evidência do lote: contagem, SRID, validade e extensão métrica."""
        result = await self.session.execute(
            text(
                "select count(*) as segments, "
                "count(*) filter (where ST_SRID(geom) = 4326) as srid_4326, "
                "count(*) filter (where ST_IsValid(geom)) as valid, "
                "count(*) filter (where geog is not null) as with_geog, "
                "coalesce(sum(ST_Length(geog)), 0) as total_length_m "
                "from public.road_segments where attributes->'osm_import'->>'label' = :label"
            ),
            {"label": label},
        )
        return dict(result.mappings().one())


class DecisionRepository:
    """Risco, competência e ação (§14): tabelas versionadas, nunca modelo."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def photo_gate_policy(self, *, lock: bool = False) -> PhotoGatePolicy:
        if lock:
            await self.session.execute(text("select pg_advisory_xact_lock(730025)"))
        result = await self.session.execute(
            text("select payload from public.operational_configuration where key='photo_gate'")
        )
        payload = result.scalar_one_or_none()
        # Only an absent row has defaults. Invalid saved configuration/database
        # failure must not silently disable an admission or privacy guard.
        return PhotoGatePolicy.model_validate(payload if payload is not None else {})

    async def save_photo_gate_policy(self, policy: PhotoGatePolicy) -> None:
        await self.session.execute(
            text("""insert into public.operational_configuration(key,payload)
                values ('photo_gate',cast(:payload as jsonb))
                on conflict(key) do update set payload=excluded.payload, updated_at=now()"""),
            {"payload": policy.model_dump_json()},
        )

    async def operational_models(
        self, *, after: tuple[datetime, uuid.UUID] | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = select(ModelVersion)
        if after is not None:
            query = query.where(tuple_(ModelVersion.created_at, ModelVersion.id) < after)
        result = await self.session.execute(
            query.order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).limit(limit)
        )
        return [
            {
                "id": model.id,
                "name": model.name,
                "version": model.version,
                "kind": model.kind,
                "status": model.operational_status,
                "created_at": model.created_at,
            }
            for model in result.scalars()
        ]

    async def report_totals(self, days: int = 30) -> dict[str, Any]:
        if not 1 <= days <= 365:
            raise ValueError("period must be 1..365 days")
        result = await self.session.execute(
            text(f"""select
            count(*) filter(where created_at >= date_trunc('day', now() at time zone 'UTC') at time zone 'UTC') as today,
            count(*) filter(where created_at >= now()-interval '7 days') as week,
            count(*) filter(where coalesce(quality->'human_review'->>'status','') not in ('confirmed','rejected')) as awaiting_review,
            count(*) filter(where point is null) as without_location,
            count(*) filter(where quality->>'location_conflict'='true') as location_conflicts,
            count(*) filter(where exists(select 1 from public.events e where e.capture_id=c.id and {PublicRepository._PUBLISHED})) as published
            from public.captures c where source in ('pwa_photo','exif_upload')""")
        )
        totals = dict(result.mappings().one())
        rejected = await self.session.execute(
            text("""select reason, count(*) as total
            from public.audit_log a cross join lateral
                jsonb_array_elements_text(a.after_data->'reasons') as reason
            where operation='photo_gate_rejected'
              and created_at >= now()-make_interval(days=>:days)
            group by reason order by reason"""),
            {"days": days},
        )
        totals["rejected_by_reason"] = {row.reason: row.total for row in rejected}
        totals["day_timezone"] = "UTC"
        counts = await self.session.execute(
            text("""with rejected as (
            select actor,created_at from public.audit_log
            where operation='photo_gate_rejected' and created_at>=now()-make_interval(days=>:days)
        ) select
          (select count(*) from rejected) rejected,
          (select count(*) from public.captures where source in ('pwa_photo','exif_upload')
             and created_at>=now()-make_interval(days=>:days)) accepted,
          (select count(*) from rejected r where exists(select 1 from public.captures c
            where c.quality->>'uploaded_by'=r.actor and c.created_at>r.created_at
              and c.created_at<=r.created_at+interval '24 hours'
              and c.quality->'human_review'->>'status'='confirmed')) retry_confirmed
        """),
            {"days": days},
        )
        sample = dict(counts.mappings().one())
        denominator = sample["rejected"] + sample["accepted"]
        totals["gate_metrics"] = {
            "days": days,
            **sample,
            "rates_by_reason": {
                key: value / denominator if denominator else None
                for key, value in totals["rejected_by_reason"].items()
            },
            "false_rejection_estimate": sample["retry_confirmed"] / sample["rejected"]
            if sample["rejected"]
            else None,
            "estimate_method": "same_owner_accepted_within_24h_then_human_confirmed; not_same_photo_proof",
        }
        return totals

    async def observed_integrations(self) -> list[dict[str, Any]]:
        rows = await self.session.execute(
            text("""with observations as (
            select after_data->>'name' name, after_data,created_at,id,
            max(created_at) filter(where after_data->>'status' in ('OK','OK_PROVIDER','OK_PUBLIC','AVAILABLE'))
                over(partition by after_data->>'name') last_success,
            max(created_at) filter(where after_data->>'status'='UNAVAILABLE')
                over(partition by after_data->>'name') last_failure
            from public.audit_log where operation='integration_health_check'
        ) select distinct on(name) name,after_data->>'status' status,
            after_data->>'detail' detail,after_data->'latency_ms' latency_ms,
            after_data->>'checked_at' checked_at,last_success,last_failure
            from observations order by name,created_at desc,id desc""")
        )
        return [dict(row) for row in rows.mappings()]

    async def audit_page(
        self, *, operation: str | None, limit: int, after: tuple[datetime, uuid.UUID] | None = None
    ) -> list[dict[str, Any]]:
        query = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        if operation:
            query = query.where(AuditLog.operation == operation)
        if after:
            query = query.where(tuple_(AuditLog.created_at, AuditLog.id) < after)
        result = await self.session.execute(query.limit(limit))
        # Raw payloads/actor can contain private identifiers. This operational
        # listing deliberately exposes only the audit envelope.
        return [
            {
                "id": row.id,
                "operation": row.operation,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "created_at": row.created_at,
                "event_hash": row.event_hash,
            }
            for row in result.scalars()
        ]

    async def dataset_version_for_model(self, model_version_id: uuid.UUID) -> uuid.UUID | None:
        result = await self.session.execute(
            select(ModelVersion.dataset_version_id).where(ModelVersion.id == model_version_id)
        )
        return result.scalar_one_or_none()

    async def add_risk(
        self, event_id: uuid.UUID, persisted: dict[str, Any], **refs: Any
    ) -> RiskAssessment:
        assessment = RiskAssessment(event_id=event_id, **persisted, **refs)
        self.session.add(assessment)
        await self.session.flush()
        return assessment

    async def latest_risk(self, event_id: uuid.UUID) -> RiskAssessment | None:
        result = await self.session.execute(
            select(RiskAssessment)
            .where(RiskAssessment.event_id == event_id)
            .order_by(
                RiskAssessment.commit_order.desc().nulls_last(),
                RiskAssessment.assessment_sequence.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_risk(self, assessment_id: uuid.UUID) -> RiskAssessment | None:
        return await self.session.get(RiskAssessment, assessment_id)

    async def responsibility(
        self, *, jurisdiction: str | None, asset_type: str, urmind_class: str
    ) -> ResponsibilityRule | None:
        if not jurisdiction:
            return None
        result = await self.session.execute(
            select(ResponsibilityRule)
            .where(
                ResponsibilityRule.active.is_(True),
                ResponsibilityRule.jurisdiction == jurisdiction,
                ResponsibilityRule.asset_type == asset_type,
                ResponsibilityRule.urmind_class == urmind_class,
            )
            .order_by(ResponsibilityRule.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def action(self, code: str) -> ActionCatalog | None:
        result = await self.session.execute(
            select(ActionCatalog)
            .where(ActionCatalog.code == code, ActionCatalog.active.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def add_review(
        self,
        *,
        event_id: uuid.UUID | None,
        reviewer: str,
        decision: str,
        corrected_class: str | None,
        corrected_location: Coordinate | None = None,
        notes: str | None,
        capture_id: uuid.UUID | None = None,
    ) -> Review:
        review = Review(
            event_id=event_id,
            capture_id=capture_id,
            reviewer=reviewer,
            decision=decision,
            corrected_class=corrected_class,
            corrected_point=_point(corrected_location) if corrected_location else None,
            notes=notes,
        )
        self.session.add(review)
        await self.session.flush()
        return review

    async def attach_capture_review(self, review_id: uuid.UUID, capture_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Review).where(Review.id == review_id).values(capture_id=capture_id)
        )

    async def attach_report_event(self, capture_id: uuid.UUID, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Review)
            .where(
                Review.capture_id == capture_id,
                Review.event_id.is_(None),
                Review.decision != "detach_evidence",
            )
            .values(event_id=event_id)
        )

    async def capture_review_history(self, capture_id: uuid.UUID) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(Review).where(Review.capture_id == capture_id).order_by(Review.review_sequence)
        )
        return [
            {
                "id": row.id,
                "decision": row.decision,
                "corrected_class": row.corrected_class,
                "notes": row.notes,
                "created_at": row.created_at,
            }
            for row in result.scalars()
        ]

    async def add_audit(
        self,
        *,
        operation: str,
        entity_type: str,
        entity_id: uuid.UUID,
        actor: str,
        before: dict[str, Any],
        after: dict[str, Any],
        event_hash: str,
    ) -> AuditLog:
        entry = AuditLog(
            operation=operation,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            before_data=before,
            after_data=after,
            event_hash=event_hash,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def audit_by_event_hash(self, event_hash: str) -> AuditLog | None:
        result = await self.session.execute(
            select(AuditLog).where(AuditLog.event_hash == event_hash).limit(1)
        )
        return result.scalar_one_or_none()

    async def reviewed_event_ids(self, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
        rows = await self.session.execute(
            text("""select distinct event_id from public.reviews
            where event_id is not null and decision in ('confirm','correct','reject')
            and (cast(:after as uuid) is null or event_id < cast(:after as uuid))
            order by event_id desc limit :limit"""),
            {"after": after, "limit": limit},
        )
        return list(rows.scalars())

    async def dataset_candidates(
        self, event_ids: list[uuid.UUID] | None = None
    ) -> list[dict[str, Any]]:
        """Revisões confirmadas/corrigidas com toda a linhagem da inferência original."""
        result = await self.session.execute(
            text(
                "select r.id as review_id, r.decision, r.corrected_class, r.reviewer, r.notes, "
                "r.created_at as reviewed_at, r.review_sequence, r.order_source, r.commit_order, "
                "a.after_data->>'reviewer_role' as reviewer_role, "
                "a.after_data->>'review_schema_version' as review_schema_version, "
                "coalesce(a.after_data->>'adjudicated', 'false') = 'true' as adjudicated, "
                "ST_Y(r.corrected_point::geometry) as corrected_latitude, "
                "ST_X(r.corrected_point::geometry) as corrected_longitude, "
                "e.id as event_id, e.event_key, e.urmind_class as inferred_class, "
                "e.visual_confidence, e.factors->'evidence' as evidence, "
                "ST_Y(e.point::geometry) as latitude, ST_X(e.point::geometry) as longitude, "
                "c.id as capture_id, c.capture_key, c.storage_path, c.source, c.source_location, "
                "c.captured_at, c.quality->>'sha256' as image_sha256, "
                "m.id as model_version_id, m.name as model_name, m.version as model_version, "
                "m.checksum as model_checksum, d.id as dataset_version_id, "
                "d.name as dataset_name, d.version as dataset_version "
                "from public.reviews r "
                "left join public.audit_log a on a.after_data->>'review_id' = r.id::text "
                "and a.operation = 'review' "
                "join public.events e on e.id = r.event_id "
                "left join public.captures c on c.id = e.capture_id "
                "left join public.model_versions m on m.id = e.model_version_id "
                "left join public.dataset_versions d on d.id = m.dataset_version_id "
                "where (cast(:event_ids as uuid[]) is null or e.id=any(cast(:event_ids as uuid[]))) "
                "order by r.commit_order nulls first, r.review_sequence"
            ),
            {"event_ids": event_ids},
        )
        rows = [dict(row) for row in result.mappings()]
        all_ids = {
            uuid.UUID(i)
            for row in rows
            for i in (row.get("evidence") or {}).get("detection_ids", [])
        }
        detections = await self.session.execute(select(Detection).where(Detection.id.in_(all_ids)))
        by_id = {str(d.id): d for d in detections.scalars()}
        for row in rows:
            ids = (row.get("evidence") or {}).get("detection_ids", [])
            row["original_detections"] = [
                {
                    "id": str(d.id),
                    "capture_id": str(d.capture_id),
                    "urmind_class": d.urmind_class,
                    "confidence": d.confidence,
                    "bbox": d.bbox,
                    "model_version_id": str(d.model_version_id) if d.model_version_id else None,
                }
                for value in ids
                if (d := by_id.get(value)) is not None
            ]
        return rows

    async def snapshot_before_review(
        self, event_id: uuid.UUID, cutoff: datetime
    ) -> RiskAssessment | None:
        result = await self.session.execute(
            select(RiskAssessment)
            .where(
                RiskAssessment.event_id == event_id,
                RiskAssessment.created_at < cutoff,
                RiskAssessment.order_source == "serialized_commit_order",
            )
            .order_by(RiskAssessment.commit_order.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def persisted_review(self, review_id: uuid.UUID) -> Review | None:
        return await self.session.get(Review, review_id)

    async def reviews(self, event_id: uuid.UUID) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.event_id == event_id)
            .order_by(Review.commit_order.asc().nulls_first(), Review.review_sequence)
        )
        return list(result.scalars())

    async def review_votes(
        self, event_id: uuid.UUID | None, *, capture_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        predicate = "r.capture_id = :capture_id" if capture_id else "r.event_id = :event_id"
        result = await self.session.execute(
            text(
                "select r.id as review_id, r.decision, r.corrected_class, r.reviewer, "
                "r.order_source, r.commit_order, "
                "ST_Y(r.corrected_point::geometry) as corrected_latitude, "
                "ST_X(r.corrected_point::geometry) as corrected_longitude, "
                "a.after_data->>'reviewer_role' as reviewer_role, "
                "coalesce(a.after_data->>'adjudicated', 'false') = 'true' as adjudicated "
                "from public.reviews r left join public.audit_log a "
                "on a.after_data->>'review_id' = r.id::text and a.operation = 'review' "
                f"where {predicate} and r.decision in ('confirm','correct','reject') "
                "order by r.commit_order nulls first, r.review_sequence"
            ),
            {"event_id": event_id, "capture_id": capture_id},
        )
        return [dict(row) for row in result.mappings()]

    async def get_rule(self, rule_id: uuid.UUID | None) -> ResponsibilityRule | None:
        return await self.session.get(ResponsibilityRule, rule_id) if rule_id else None

    async def get_action(self, action_id: uuid.UUID | None) -> ActionCatalog | None:
        return await self.session.get(ActionCatalog, action_id) if action_id else None


class InferenceRepository:
    """Fila `inference_jobs` (pgmq) e modelo promovido (§7, §9)."""

    QUEUE = "inference_jobs"

    async def enqueue_capture(self, capture_id: uuid.UUID) -> None:
        await self.session.execute(
            text("select pgmq.send(:queue, jsonb_build_object('capture_id',cast(:id as text)))"),
            {"queue": self.QUEUE, "id": str(capture_id)},
        )

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read_job(self, visibility_timeout_s: int) -> dict[str, Any] | None:
        result = await self.session.execute(
            text("select msg_id, read_ct, message from pgmq.read(:queue, :vt, 1)"),
            {"queue": self.QUEUE, "vt": visibility_timeout_s},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def archive_job(self, msg_id: int) -> None:
        await self.session.execute(
            text("select pgmq.archive(:queue, cast(:msg_id as bigint))"),
            {"queue": self.QUEUE, "msg_id": msg_id},
        )

    async def pending(self) -> int:
        result = await self.session.execute(
            text("select queue_length from pgmq.metrics(:queue)"), {"queue": self.QUEUE}
        )
        return int(result.scalar_one())

    async def stats(self) -> dict[str, Any]:
        """Métricas simples do Worker (§19), direto da fila e das capturas."""
        queue = (
            (
                await self.session.execute(
                    text(
                        "select "
                        "(select count(*) from pgmq.q_inference_jobs where vt <= now()) as pending, "
                        "(select count(*) from pgmq.q_inference_jobs where vt > now()) as processing, "
                        "(select count(*) from pgmq.a_inference_jobs) as archived, "
                        "(select count(*) from pgmq.a_inference_jobs where read_ct > 1) as retried"
                    )
                )
            )
            .mappings()
            .one()
        )
        outcomes = await self.session.execute(
            text(
                "select coalesce(quality->'inference'->>'status', 'not_processed') as status, "
                "count(*) as total from public.captures where storage_path is not null group by 1"
            )
        )
        latency = (
            (
                await self.session.execute(
                    text(
                        "select percentile_cont(0.5) within group (order by v) as p50_ms, "
                        "percentile_cont(0.95) within group (order by v) as p95_ms, count(*) as samples "
                        "from (select (quality->'inference'->>'latency_ms')::float as v "
                        "from public.captures where quality->'inference'->>'latency_ms' is not null) s"
                    )
                )
            )
            .mappings()
            .one()
        )
        pipeline = (
            (
                await self.session.execute(
                    text(
                        "select count(*) as captures_received, "
                        "percentile_cont(0.5) within group (order by to_detection_ms) "
                        "  as time_to_detection_p50_ms, "
                        "percentile_cont(0.95) within group (order by to_detection_ms) "
                        "  as time_to_detection_p95_ms, "
                        "percentile_cont(0.5) within group (order by to_completion_ms) "
                        "  as pipeline_completion_p50_ms, "
                        "percentile_cont(0.95) within group (order by to_completion_ms) "
                        "  as pipeline_completion_p95_ms "
                        "from (select "
                        "  extract(epoch from ((quality->'inference'->>'detection_completed_at')"
                        "    ::timestamptz - created_at)) * 1000 as to_detection_ms, "
                        "  case when quality->'inference'->>'status' = 'analysis_completed' then "
                        "    extract(epoch from ((quality->'inference'->>'at')::timestamptz"
                        "      - created_at)) * 1000 end as to_completion_ms "
                        "  from public.captures where storage_path is not null) s"
                    )
                )
            )
            .mappings()
            .one()
        )
        context = await self.session.execute(
            text(
                "select source, count(*) filter (where payload->>'status' is distinct from 'ok') "
                "as failures, count(*) as total from public.event_context group by source"
            )
        )
        return {
            "queue": dict(queue),
            "outcomes": {row.status: row.total for row in outcomes},
            # Detection-stage latency measured by the Worker (job start → detections).
            "latency_ms": dict(latency),
            # Capture created → stage timestamps: includes queue wait.
            "pipeline_ms": dict(pipeline),
            "context_failures": {
                row.source: {"failures": row.failures, "total": row.total} for row in context
            },
            # Rejected uploads (4xx) are never persisted; they are only in the API logs.
            "captures_rejected": "not_persisted; see API log event capture_rejected",
        }

    async def merge_model_metrics(self, model_version_id: uuid.UUID, key: str, value: Any) -> None:
        """Acrescenta um relatório (avaliação, benchmark) sem apagar as métricas de registro."""
        model = await self.session.get(ModelVersion, model_version_id)
        if model is None:
            raise ValueError("model_version inexistente")
        model.metrics = {**(model.metrics or {}), key: value}
        await self.session.flush()

    async def dataset_version(self, dataset_version_id: uuid.UUID) -> DatasetVersion | None:
        return await self.session.get(DatasetVersion, dataset_version_id)

    async def model_version(self, model_version_id: uuid.UUID) -> ModelVersion | None:
        return await self.session.get(ModelVersion, model_version_id)

    async def shadow_vision_model(self, model_version_id: uuid.UUID) -> ModelVersion | None:
        model = await self.session.get(ModelVersion, model_version_id)
        if model is None or model.kind != "vision" or model.promoted_at is not None:
            return None
        if model.operational_status != "EXPERIMENTAL_SHADOW":
            return None
        metrics = model.metrics if isinstance(model.metrics, dict) else {}
        from app.config import URMIND_DEV_SHADOW_REF

        if (
            metrics.get("shadow_scope") != "URMIND_DEV_ONLY"
            or metrics.get("shadow_project_ref") != URMIND_DEV_SHADOW_REF
        ):
            return None
        if model.dataset_version_id is None or not model.checksum:
            return None
        if await self.session.get(DatasetVersion, model.dataset_version_id) is None:
            return None
        return model

    async def configured_vision_model(
        self, mode: str, shadow_model_version_id: uuid.UUID | None
    ) -> ModelVersion | None:
        if mode == "shadow" and shadow_model_version_id is not None:
            return await self.shadow_vision_model(shadow_model_version_id)
        if mode == "production":
            try:
                return await self.promoted_vision_model()
            except RuntimeError as exc:
                logger.error("production_model_unavailable: %s", exc)
                return None
        return None

    async def model_version_for_update(self, model_version_id: uuid.UUID) -> ModelVersion | None:
        result = await self.session.execute(
            select(ModelVersion).where(ModelVersion.id == model_version_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def promoted_vision_model(self) -> ModelVersion | None:
        result = await self.session.execute(
            select(ModelVersion)
            .where(ModelVersion.kind == "vision", ModelVersion.promoted_at.is_not(None))
            .order_by(ModelVersion.promoted_at.desc())
            .limit(2)
        )
        models = result.scalars().all()
        if len(models) > 1:
            raise RuntimeError("múltiplos modelos vision promovidos")
        if models and not _promotion_quality_approved(models[0]):
            raise RuntimeError("modelo vision promovido sem quality gate aprovado")
        if models and models[0].operational_status != "PRODUCTION_APPROVED":
            raise RuntimeError("modelo vision promovido está arquivado ou em quarentena")
        if models:
            from app.ml.serving import validate_registered_model_evidence

            model = models[0]
            dataset = (
                await self.session.get(DatasetVersion, model.dataset_version_id)
                if model.dataset_version_id is not None
                else None
            )
            try:
                validate_registered_model_evidence(model, dataset)
            except (OSError, TypeError, ValueError, KeyError) as exc:
                raise RuntimeError("modelo vision promovido sem closure íntegro") from exc
        return models[0] if models else None

    async def has_detections_from(self, capture_id: uuid.UUID, model_version_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(Detection)
            .where(
                Detection.capture_id == capture_id, Detection.model_version_id == model_version_id
            )
        )
        return int(result.scalar_one()) > 0

    async def set_capture_inference(self, capture: Capture, state: dict[str, Any]) -> None:
        previous = (capture.quality or {}).get("inference") or {}
        capture.quality = {**(capture.quality or {}), "inference": state}
        if previous.get("status") != state.get("status"):
            await DecisionRepository(self.session).add_audit(
                operation="capture_inference",
                entity_type="capture",
                entity_id=capture.id,
                actor="worker",
                before={"status": previous.get("status")},
                after={"status": state.get("status"), "model_status": state.get("model_status")},
                event_hash=f"inference-{uuid.uuid4()}",
            )
        await self.session.flush()

    async def upsert_dataset_version(self, **fields: Any) -> DatasetVersion:
        result = await self.session.execute(
            select(DatasetVersion).where(
                DatasetVersion.name == fields["name"], DatasetVersion.version == fields["version"]
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        dataset = DatasetVersion(**fields)
        self.session.add(dataset)
        await self.session.flush()
        return dataset

    async def register_model(
        self, *, refresh_unpromoted: bool = False, **fields: Any
    ) -> ModelVersion:
        result = await self.session.execute(
            select(ModelVersion).where(
                ModelVersion.name == fields["name"], ModelVersion.version == fields["version"]
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if refresh_unpromoted:
                if (
                    existing.promoted_at is not None
                    or existing.checksum != fields["checksum"]
                    or existing.kind != fields["kind"]
                    or existing.dataset_version_id != fields["dataset_version_id"]
                ):
                    raise ValueError("ModelVersion existente diverge do fechamento validado")
                existing.metrics = fields["metrics"]
                await self.session.flush()
            return existing
        model = ModelVersion(**fields)
        self.session.add(model)
        await self.session.flush()
        return model

    async def promote_exclusive(self, model: ModelVersion, *, promoted_at: datetime) -> None:
        """Promove uma versão e despromove qualquer outra da mesma função."""
        if model.kind == "vision" and not _promotion_quality_approved(model):
            raise ValueError("ModelVersion sem quality gate aprovado")
        if model.kind == "vision":
            from app.ml.serving import validate_registered_model_evidence

            dataset = (
                await self.session.get(DatasetVersion, model.dataset_version_id)
                if model.dataset_version_id is not None
                else None
            )
            validate_registered_model_evidence(model, dataset)
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"urmind:model-promotion:{model.kind}"},
        )
        await self.session.execute(
            update(ModelVersion)
            .where(ModelVersion.kind == model.kind, ModelVersion.id != model.id)
            .values(promoted_at=None)
        )
        model.promoted_at = promoted_at
        await self.session.flush()


class PublicRepository:
    """Leitura do painel público (§17): só o que o contrato público expõe.

    Nenhuma consulta aqui devolve identificador de usuário, revisor, caminho de
    Storage ou payload cru de provider — o recorte já sai do SQL.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # Publication is a separate reviewer decision, never implied by a detector,
    # upload, sanitized image or confirmed status alone. Match its Review so
    # stale publication cannot survive a later correction/rejection.
    _PUBLISHED = (
        "e.status = 'confirmed' "
        "and e.factors->'publication'->>'policy_version' = 'urmind-publication-v1' "
        "and e.factors->'publication'->>'status' = 'published' "
        "and exists (select 1 from public.reviews pr where pr.event_id = e.id "
        "and pr.id::text = e.factors->'publication'->>'review_id' "
        "and pr.reviewer = e.factors->'publication'->>'reviewer' "
        "and pr.decision in ('confirm', 'correct') "
        "and pr.id = (select lr.id from public.reviews lr where lr.event_id = e.id "
        "order by lr.commit_order desc nulls last, lr.review_sequence desc limit 1))"
    )

    _SUMMARY = (
        "select e.id, e.public_id, e.occurred_at, "
        "coalesce(hr.corrected_class, e.urmind_class) as urmind_class, "
        "e.status, e.evidence_mode, "
        "e.visual_confidence, e.location_accuracy_m, e.distance_to_road_m, "
        "ST_Y(coalesce(hr.corrected_point,e.point)::geometry) as latitude, "
        "ST_X(coalesce(hr.corrected_point,e.point)::geometry) as longitude, "
        "ST_Y(e.snapped_point::geometry) as snapped_latitude, "
        "ST_X(e.snapped_point::geometry) as snapped_longitude, "
        "case when hr.corrected_point is null then coalesce(r.name,c.quality->'address'->>'road') end as road_name, "
        "r.highway as road_highway, r.jurisdiction as road_jurisdiction, "
        "e.road_segment_id, e.model_version_id, e.capture_id, "
        "e.factors->'publication'->>'review_id' as publication_review_id, "
        "e.factors->'publication'->'public_image' as publication_image, "
        "risk.severity, risk.priority_score, risk.factors "
        "from public.events e "
        "left join public.captures c on c.id=e.capture_id "
        "left join lateral (select corrected_class, corrected_point from public.reviews "
        "where event_id=e.id order by commit_order desc nulls last, review_sequence desc limit 1) "
        "hr on e.status='confirmed' "
        "left join public.road_segments r on r.id = e.road_segment_id "
        "left join lateral ("
        "  select severity, priority_score, factors from public.risk_assessments a "
        "  where a.event_id = e.id "
        "  order by a.commit_order desc nulls last, a.assessment_sequence desc limit 1"
        ") risk on true "
    )

    async def events(
        self,
        *,
        limit: int,
        bbox: tuple[float, float, float, float] | None = None,
        urmind_class: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clauses = [self._PUBLISHED]
        params: dict[str, Any] = {"limit": limit}
        if bbox is not None:
            clauses.append(
                "ST_Intersects(coalesce(hr.corrected_point,e.point)::geometry, "
                "ST_MakeEnvelope(:west, :south, :east, :north, 4326))"
            )
            params |= {"south": bbox[0], "west": bbox[1], "north": bbox[2], "east": bbox[3]}
        if urmind_class:
            clauses.append("coalesce(hr.corrected_class,e.urmind_class) = :urmind_class")
            params["urmind_class"] = urmind_class
        if status:
            clauses.append("e.status = :status")
            params["status"] = status
        if since is not None:
            clauses.append("e.occurred_at >= :since")
            params["since"] = since
        where = (" where " + " and ".join(clauses)) if clauses else ""
        result = await self.session.execute(
            text(f"{self._SUMMARY}{where} order by e.occurred_at desc limit :limit"), params
        )
        return [dict(row) for row in result.mappings()]

    async def event(
        self, event_id: uuid.UUID | str, *, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        access = f"({self._PUBLISHED})"
        params: dict[str, Any] = {"event_id": event_id}
        if owner_id:
            access += (
                " or exists (select 1 from public.captures oc "
                "where oc.quality->>'uploaded_by' = :owner_id "
                "and (oc.id = e.capture_id or "
                "e.factors->'evidence'->'capture_ids' @> jsonb_build_array(oc.id::text)))"
            )
            params["owner_id"] = owner_id
        identity_column = "e.id" if isinstance(event_id, uuid.UUID) else "e.public_id"
        result = await self.session.execute(
            text(f"{self._SUMMARY} where {identity_column} = :event_id and ({access})"), params
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def prediction(self, event_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                "select p.task, p.horizon_days, p.value, p.uncertainty, p.created_at, m.version "
                "from public.predictions p "
                "left join public.model_versions m on m.id = p.model_version_id "
                "where p.event_id = :event_id order by p.created_at desc limit 1"
            ),
            {"event_id": event_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def overview(self) -> dict[str, Any]:
        result = await self.session.execute(
            text(
                f"select (select count(*) from public.events e where {self._PUBLISHED}) "
                "as events_total, "
                "(select count(*) from public.road_segments) as road_segments_total, "
                f"(select max(occurred_at) from public.events e where {self._PUBLISHED}) "
                "as last_event_at, "
                "(select attributes->'osm_import'->>'label' from public.road_segments limit 1) as pilot_area"
            )
        )
        return dict(result.mappings().one())

    async def scout_device(self) -> dict[str, Any] | None:
        """Dispositivo de coleta mais recente, quando existir (§21). Sem Scout, devolve None."""
        result = await self.session.execute(
            text(
                "select d.code, d.kind, d.firmware_version, "
                "(select max(c.captured_at) from public.captures c where c.device_id = d.id) as last_capture_at "
                "from public.devices d where d.kind in ('scout', 'gateway') "
                "order by d.created_at desc limit 1"
            )
        )
        row = result.mappings().first()
        return dict(row) if row else None
