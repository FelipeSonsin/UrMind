"""Teste de integração real contra PostgreSQL/PostGIS.

Só roda quando DATABASE_URL está definida (Supabase ou Postgres+PostGIS local).
Sem ela os testes são pulados, mantendo a suíte offline verde.

    DATABASE_URL=postgresql://... pytest tests/test_db_integration.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db.migrate import current_revision, head_revision, pending, upgrade
from app.db.session import Database
from app.repositories.core import CaptureRepository, EventRepository
from app.schemas.core import (
    CaptureCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EvidenceMode,
    LocationSource,
    NearbyQuery,
    UrmindClass,
)
from app.services.core import CoreService

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="DATABASE_URL não definida; integração pulada"
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
# Av. Paulista, faixa usada só neste teste.
LAT, LON = -23.5613, -46.6560


@pytest.fixture(scope="module")
async def database():
    db = Database(get_settings())
    await upgrade()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_migrations_ficam_idempotentes(database):
    assert await pending(database) == []
    assert await current_revision(database) == head_revision()


@pytest.mark.asyncio
async def test_postgis_disponivel(database):
    health = await database.health()
    assert health["database"] == "connected"
    assert health["postgis"]


@pytest.mark.asyncio
async def test_evento_faz_snap_no_trecho_viario_e_aparece_na_busca_por_raio(database):
    suffix = uuid.uuid4().hex[:8]
    async with database.session() as session:
        # Trecho viário sintético a poucos metros do ponto do evento.
        segment_id = await session.scalar(
            text(
                "insert into public.road_segments(name, highway, geom) "
                "values (:name, 'residential', "
                "ST_SetSRID(ST_MakeLine(ST_MakePoint(:lon1, :lat), ST_MakePoint(:lon2, :lat)), 4326)) "
                "returning id"
            ),
            {"name": f"teste-{suffix}", "lat": LAT, "lon1": LON - 0.002, "lon2": LON + 0.002},
        )

        service = CoreService(CaptureRepository(session), EventRepository(session))
        capture = await service.register_capture(
            CaptureCreate(
                capture_key=f"cap-{suffix}",
                source=CaptureSource.PWA_PHOTO,
                source_location=LocationSource.GPS_DEVICE,
                captured_at=NOW,
                coordinate=Coordinate(latitude=LAT + 0.00005, longitude=LON, accuracy_m=6),
                detections=[
                    {
                        "urmind_class": UrmindClass.ROAD_D40,
                        "confidence": 0.93,
                        "bbox": {"x": 0.2, "y": 0.3, "width": 0.2, "height": 0.2},
                    }
                ],
            )
        )
        assert capture["created"] is True

        event = await service.register_event(
            EventCreate(
                event_key=f"evt-{suffix}",
                capture_id=capture["id"],
                urmind_class=UrmindClass.ROAD_D40,
                evidence_mode=EvidenceMode.PHOTO,
                occurred_at=NOW,
                coordinate=Coordinate(latitude=LAT + 0.00005, longitude=LON, accuracy_m=6),
                visual_confidence=0.93,
            )
        )
        assert event["road_segment_id"] == segment_id
        assert event["distance_to_road_m"] < 20
        assert event["status"] == "detected"

        nearby = await service.events_nearby(
            NearbyQuery(latitude=LAT, longitude=LON, radius_m=200)
        )
        found = next(row for row in nearby if row["event_key"] == f"evt-{suffix}")
        # A coordenada original é preservada; o snap vive em snapped_*.
        assert found["latitude"] == pytest.approx(LAT + 0.00005, abs=1e-6)
        assert found["snapped_latitude"] == pytest.approx(LAT, abs=1e-5)

        # Limpeza: o teste não deixa resíduo no banco compartilhado.
        await session.execute(
            text("delete from public.events where event_key = :key"), {"key": f"evt-{suffix}"}
        )
        await session.execute(
            text("delete from public.captures where capture_key = :key"), {"key": f"cap-{suffix}"}
        )
        await session.execute(
            text("delete from public.road_segments where id = :id"), {"id": segment_id}
        )
