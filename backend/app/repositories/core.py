"""Persistência do núcleo no PostgreSQL/PostGIS (SQLAlchemy 2 + psycopg 3).

Única camada do backend que emite SQL (§31.19). Captura, detecção e evento
precisam de PostGIS (ST_DWithin, KNN, ST_ClosestPoint) e transação real.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from geoalchemy2 import Geometry
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.schemas.core import CaptureCreate, Coordinate, EventCreate, NearbyQuery

if TYPE_CHECKING:
    from app.services.osm_import import RoadWay

# Cast sem typmod: ST_X/ST_Y só aceitam geometry, e "geometry(GEOMETRY,-1)"
# não é um tipo válido no PostGIS.
GEOM_CAST = Geometry(geometry_type=None, srid=-1)


def _point(coordinate: Coordinate):
    """Coordenada → geography(Point,4326). Ordem PostGIS é (lon, lat)."""
    return func.ST_SetSRID(func.ST_MakePoint(coordinate.longitude, coordinate.latitude), 4326).cast(
        Event.point.type
    )


class CaptureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: CaptureCreate) -> Capture:
        capture = Capture(
            capture_key=payload.capture_key,
            mission_id=payload.mission_id,
            device_id=payload.device_id,
            source=payload.source.value,
            source_location=payload.source_location.value,
            captured_at=payload.captured_at,
            storage_path=payload.storage_path,
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

    async def get(self, capture_id: uuid.UUID) -> Capture | None:
        return await self.session.get(Capture, capture_id)

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
    ) -> list[dict[str, Any]]:
        stmt = self._row_select().order_by(Event.occurred_at.desc()).limit(limit)
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
        event_id: uuid.UUID,
        reviewer: str,
        decision: str,
        corrected_class: str | None,
        corrected_location: Coordinate | None = None,
        notes: str | None,
    ) -> Review:
        review = Review(
            event_id=event_id,
            reviewer=reviewer,
            decision=decision,
            corrected_class=corrected_class,
            corrected_point=_point(corrected_location) if corrected_location else None,
            notes=notes,
        )
        self.session.add(review)
        await self.session.flush()
        return review

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

    async def dataset_candidates(self) -> list[dict[str, Any]]:
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
                "order by r.commit_order nulls first, r.review_sequence"
            )
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

    async def reviews(self, event_id: uuid.UUID) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.event_id == event_id)
            .order_by(Review.commit_order.asc().nulls_first(), Review.review_sequence)
        )
        return list(result.scalars())

    async def review_votes(self, event_id: uuid.UUID) -> list[dict[str, Any]]:
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
                "where r.event_id = :event_id "
                "order by r.commit_order nulls first, r.review_sequence"
            ),
            {"event_id": event_id},
        )
        return [dict(row) for row in result.mappings()]

    async def get_rule(self, rule_id: uuid.UUID | None) -> ResponsibilityRule | None:
        return await self.session.get(ResponsibilityRule, rule_id) if rule_id else None

    async def get_action(self, action_id: uuid.UUID | None) -> ActionCatalog | None:
        return await self.session.get(ActionCatalog, action_id) if action_id else None


class InferenceRepository:
    """Fila `inference_jobs` (pgmq) e modelo promovido (§7, §9)."""

    QUEUE = "inference_jobs"

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

    async def enqueue(self, capture_id: uuid.UUID) -> None:
        await self.session.execute(
            text("select pgmq.send(:queue, jsonb_build_object('capture_id', cast(:id as text)))"),
            {"queue": self.QUEUE, "id": str(capture_id)},
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
        return {
            "queue": dict(queue),
            "outcomes": {row.status: row.total for row in outcomes},
            "latency_ms": dict(latency),
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

    async def promoted_vision_model(self) -> ModelVersion | None:
        result = await self.session.execute(
            select(ModelVersion)
            .where(ModelVersion.kind == "vision", ModelVersion.promoted_at.is_not(None))
            .order_by(ModelVersion.promoted_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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
        capture.quality = {**(capture.quality or {}), "inference": state}
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

    async def register_model(self, **fields: Any) -> ModelVersion:
        result = await self.session.execute(
            select(ModelVersion).where(
                ModelVersion.name == fields["name"], ModelVersion.version == fields["version"]
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        model = ModelVersion(**fields)
        self.session.add(model)
        await self.session.flush()
        return model


class PublicRepository:
    """Leitura do painel público (§17): só o que o contrato público expõe.

    Nenhuma consulta aqui devolve identificador de usuário, revisor, caminho de
    Storage ou payload cru de provider — o recorte já sai do SQL.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    _SUMMARY = (
        "select e.id, e.occurred_at, e.urmind_class, e.status, e.evidence_mode, "
        "e.visual_confidence, e.location_accuracy_m, e.distance_to_road_m, "
        "ST_Y(e.point::geometry) as latitude, ST_X(e.point::geometry) as longitude, "
        "ST_Y(e.snapped_point::geometry) as snapped_latitude, "
        "ST_X(e.snapped_point::geometry) as snapped_longitude, "
        "r.name as road_name, r.highway as road_highway, r.jurisdiction as road_jurisdiction, "
        "e.road_segment_id, risk.severity, risk.priority_score, risk.factors "
        "from public.events e "
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
        clauses = []
        params: dict[str, Any] = {"limit": limit}
        if bbox is not None:
            clauses.append(
                "ST_Intersects(e.point::geometry, ST_MakeEnvelope(:west, :south, :east, :north, 4326))"
            )
            params |= {"south": bbox[0], "west": bbox[1], "north": bbox[2], "east": bbox[3]}
        if urmind_class:
            clauses.append("e.urmind_class = :urmind_class")
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

    async def event(self, event_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(f"{self._SUMMARY} where e.id = :event_id"), {"event_id": event_id}
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
                "select (select count(*) from public.events) as events_total, "
                "(select count(*) from public.road_segments) as road_segments_total, "
                "(select max(occurred_at) from public.events) as last_event_at, "
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
