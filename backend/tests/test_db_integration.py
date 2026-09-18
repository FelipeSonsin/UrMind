"""Teste de integração real contra PostgreSQL/PostGIS.

Só roda quando MIGRATION_DATABASE_URL ou DATABASE_URL (migrations), e
DATABASE_POOLER_URL (runtime), estão
definidas no ambiente do shell. A suíte não lê o backend/.env (ENVIRONMENT=test),
então a integração é sempre opt-in explícito e nunca toca o banco por acidente.
Com Postgres+PostGIS local, sem pooler, as duas apontam para a mesma URL.

    MIGRATION_DATABASE_URL=postgresql://... DATABASE_POOLER_URL=postgresql://... \
        pytest tests/test_db_integration.py
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
    not (
        (os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL"))
        and os.getenv("DATABASE_POOLER_URL")
    ),
    reason="URL de migrations e DATABASE_POOLER_URL não definidas; integração pulada",
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


@pytest.mark.asyncio
async def test_deteccoes_viram_evento_deduplicado_com_risco_revisao_e_auditoria(database):
    """Capture→Detection→Event→risco→revisão no banco real, tudo desfeito no fim."""
    from app.repositories.core import DecisionRepository
    from app.schemas.core import DetectionCreate, ReviewCreate, ReviewDecision

    suffix = uuid.uuid4().hex[:8]
    async with database.sessionmaker() as session:
        try:
            await session.execute(
                text(
                    "insert into public.road_segments(name, highway, jurisdiction, geom) values "
                    "(:name, 'trunk', 'BR-rodovia-federal', ST_SetSRID(ST_MakeLine("
                    "ST_MakePoint(:lon1, :lat), ST_MakePoint(:lon2, :lat)), 4326))"
                ),
                {"name": f"teste-{suffix}", "lat": LAT, "lon1": LON - 0.002, "lon2": LON + 0.002},
            )
            service = CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            )

            def capture(n: int, lat_offset: float) -> CaptureCreate:
                return CaptureCreate(
                    capture_key=f"cap-{suffix}-{n}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.GPS_DEVICE,
                    captured_at=NOW,
                    coordinate=Coordinate(latitude=LAT + lat_offset, longitude=LON, accuracy_m=5),
                    detections=[
                        DetectionCreate(
                            urmind_class=UrmindClass.ROAD_D40,
                            confidence=0.91,
                            bbox={"x": 0.3, "y": 0.4, "width": 0.3, "height": 0.2},
                        ),
                        DetectionCreate(
                            urmind_class=UrmindClass.ROAD_D00,
                            confidence=0.10,  # abaixo do mínimo: não vira evento
                            bbox={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                        ),
                    ],
                )

            first = await service.register_capture(capture(1, 0.00003))
            result = await service.consolidate_capture(first["id"])
            (outcome,) = result["events"]
            assert outcome["created"] is True
            assert outcome["risk"]["severity"] in {"low", "medium", "high"}
            assert outcome["risk"]["responsible"].startswith("DNIT")
            assert outcome["risk"]["action"] == "inspecao_tecnica"

            again = await service.consolidate_capture(first["id"])
            assert again["events"][0] == {
                "event_id": outcome["event_id"], "created": False, "deduplicated": False
            }

            second = await service.register_capture(capture(2, 0.00005))  # ~2 m do primeiro
            dedup = await service.consolidate_capture(second["id"])
            assert dedup["events"][0]["event_id"] == outcome["event_id"]
            assert dedup["events"][0]["deduplicated"] is True

            dossier = await service.event_dossier(outcome["event_id"])
            assert len(dossier["factors"]["evidence"]["capture_ids"]) == 2
            assert dossier["road_segment_id"] is not None
            assert "DNIT" in dossier["report"]
            assert dossier["detections"] and all(d["confidence"] >= 0.25 for d in dossier["detections"])

            review = await service.review_event(
                outcome["event_id"],
                ReviewCreate(decision=ReviewDecision.CORRECT, corrected_class=UrmindClass.ROAD_D20),
                reviewer="revisor-teste",
            )
            assert review["status"] == "confirmed"
            audit = await session.execute(
                text("select before_data, after_data from public.audit_log where entity_id = :id"),
                {"id": outcome["event_id"]},
            )
            before, after = audit.one()
            assert before["urmind_class"] == "URMIND_ROAD_D40"  # inferência original preservada
            assert after["corrected_class"] == "URMIND_ROAD_D20"
        finally:
            await session.rollback()
