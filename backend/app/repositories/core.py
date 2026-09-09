"""Persistência do núcleo no PostgreSQL/PostGIS (SQLAlchemy 2 + psycopg 3).

Única camada do backend que emite SQL (§31.19). Captura, detecção e evento
precisam de PostGIS (ST_DWithin, KNN, ST_ClosestPoint) e transação real.
"""

from __future__ import annotations

import uuid
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Capture, Detection, Event
from app.schemas.core import CaptureCreate, Coordinate, EventCreate, NearbyQuery

# Cast sem typmod: ST_X/ST_Y só aceitam geometry, e "geometry(GEOMETRY,-1)"
# não é um tipo válido no PostGIS.
GEOM_CAST = Geometry(geometry_type=None, srid=-1)


def _point(coordinate: Coordinate):
    """Coordenada → geography(Point,4326). Ordem PostGIS é (lon, lat)."""
    return func.ST_SetSRID(
        func.ST_MakePoint(coordinate.longitude, coordinate.latitude), 4326
    ).cast(Event.point.type)


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
        event.snapped_point = func.ST_SetSRID(
            func.ST_MakePoint(longitude, latitude), 4326
        ).cast(Event.snapped_point.type)
        await self.session.flush()
        return event

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
        origin = func.ST_SetSRID(
            func.ST_MakePoint(query.longitude, query.latitude), 4326
        ).cast(Event.point.type)
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
