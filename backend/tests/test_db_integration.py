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

import asyncio
import hashlib
import io
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import exc, text

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
from app.services.storage import StorageClient, object_path, validate_image

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


@pytest.mark.asyncio
async def test_human_report_real_storage_publication_and_cleanup(database):
    """Synthetic isolated integration, not a citizen E2E or detector evaluation."""
    from fastapi import UploadFile
    from PIL import Image

    from app.api.v1.core import publish_event, review_capture, upload_photo
    from app.auth import AuthenticatedUser
    from app.repositories.core import DecisionRepository, PublicRepository
    from app.schemas.core import CaptureReviewCreate, PublicationRequest
    from tests.test_exif import build_jpeg, gps_block

    owner = str(uuid.uuid4())
    user = AuthenticatedUser(owner, None, "authenticated", urmind_role="admin")
    storage = StorageClient(get_settings())
    paths: list[str] = []
    capture_id = event_id = None
    with Image.open(io.BytesIO(build_jpeg(gps=gps_block()))) as metadata:
        buffer = io.BytesIO()
        Image.effect_noise((640, 640), 30).convert("RGB").save(
            buffer, format="JPEG", exif=metadata.getexif()
        )
    try:
        async with database.sessionmaker() as session:
            service = CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            )
            result = await upload_photo(
                user,
                service,
                storage,
                UploadFile(filename="isolated.jpg", file=io.BytesIO(buffer.getvalue())),
            )
            capture_id = result["id"]
            capture = await service.captures.get(capture_id)
            paths.append(capture.storage_path)
            assert not await service.events.for_capture(capture_id)
            reviewed = await review_capture(
                capture_id,
                CaptureReviewCreate(
                    decision="correct", corrected_class="URMIND_FALLEN_TREE", adjudicate=True
                ),
                user,
                service,
            )
            event_id = reviewed["event_id"]
            assert event_id is not None
            event = await service.events.get(event_id)
            assert event.factors["origin"] == "human_review"
            assert not capture.detections
            public = PublicRepository(session)
            assert await public.event(event_id) is None
            await publish_event(
                event_id,
                PublicationRequest(
                    publish=True,
                    review_id=reviewed["review_id"],
                    visible_content_reviewed=True,
                    reason="Synthetic integration privacy attestation",
                ),
                user,
                service,
                storage,
            )
            event = await service.events.get(event_id)
            derived = event.factors["publication"]["public_image"]["storage_path"]
            paths.append(derived)
            assert derived != paths[0]
            assert await storage.download(paths[0]) == buffer.getvalue()
            with Image.open(io.BytesIO(await storage.download(derived))) as sanitized:
                assert not sanitized.getexif()
            assert (await public.event(event_id))["id"] == event_id
            await publish_event(
                event_id,
                PublicationRequest(publish=False, reason="Fixture withdrawal"),
                user,
                service,
                storage,
            )
            assert await public.event(event_id) is None
    finally:
        async with database.session() as session:
            for identifier in (event_id, capture_id):
                if identifier:
                    await session.execute(
                        text("delete from public.audit_log where entity_id=:id"), {"id": identifier}
                    )
            if capture_id:
                await session.execute(
                    text("delete from pgmq.q_inference_jobs where message->>'capture_id'=:id"),
                    {"id": str(capture_id)},
                )
                await session.execute(
                    text("delete from public.reviews where capture_id=:id"), {"id": capture_id}
                )
            if event_id:
                await session.execute(
                    text("delete from public.events where id=:id"), {"id": event_id}
                )
            if capture_id:
                await session.execute(
                    text("delete from public.captures where id=:id"), {"id": capture_id}
                )
        for path in paths:
            await storage.delete(path)


@pytest.mark.asyncio
async def test_report_identity_retries_forced_collisions_in_isolated_temp_tables(database):
    """Exercise the canonical trigger body; only entropy and table targets are isolated."""
    import ast
    from pathlib import Path

    tree = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions/0023_report_identity.py").read_text(
            encoding="utf-8"
        )
    )
    sql = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("create function public.assign_report_identity")
    )
    sql = (
        sql.replace("public.assign_report_identity", "pg_temp.assign_report_identity")
        .replace("public.captures", "pg_temp.identity_fixture")
        .replace("gen_random_uuid()", "pg_temp.identity_entropy()")
    )
    async with database.session() as session:
        await session.execute(
            text(
                "create temporary table identity_fixture(public_id text unique, protocol_code text unique) on commit drop"
            )
        )
        await session.execute(text("create temporary sequence identity_calls"))
        await session.execute(
            text("""create function pg_temp.identity_entropy() returns uuid language sql as $$
            select (case nextval('pg_temp.identity_calls') when 1 then '00000000-0000-0000-0000-000000000000'
                when 2 then '11111111-1111-1111-1111-111111111111'
                when 3 then '00000000-0000-0000-0000-000000000000'
                else 'ffffffff-ffff-ffff-ffff-ffffffffffff' end)::uuid $$""")
        )
        await session.execute(text(sql))
        await session.execute(
            text(
                "insert into identity_fixture values('00000000000000000000000000000000','URM-22222222')"
            )
        )
        await session.execute(
            text("""create trigger identity_retry before insert on identity_fixture
            for each row when(new.public_id is null) execute function pg_temp.assign_report_identity()""")
        )
        row = (
            await session.execute(
                text(
                    "insert into identity_fixture default values returning public_id,protocol_code"
                )
            )
        ).one()
        assert row.public_id == "11111111111111111111111111111111"
        assert row.protocol_code == "URM-ZZZZZZZZ"
        assert await session.scalar(text("select last_value from identity_calls")) == 4
        await session.rollback()


@pytest.mark.asyncio
async def test_additional_evidence_has_one_point_and_can_be_detached(database):
    from app.api.v1.core import detach_evidence
    from app.auth import AuthenticatedUser
    from app.repositories.core import DecisionRepository

    owner = str(uuid.uuid4())
    ids = []
    try:
        async with database.session() as session:
            repo = CaptureRepository(session)
            parent = await repo.create(
                CaptureCreate(
                    capture_key=f"evidence-parent-{uuid.uuid4()}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.MANUAL,
                    captured_at=NOW,
                    coordinate=Coordinate(latitude=LAT, longitude=LON),
                    quality={"uploaded_by": owner},
                )
            )
            ids.append(parent.id)
            assert (await repo.nearby_reports(owner, Coordinate(latitude=LAT, longitude=LON), 25))[
                0
            ]["id"] == parent.id
            assert not await repo.nearby_reports(
                str(uuid.uuid4()), Coordinate(latitude=LAT, longitude=LON), 25
            )
            child = await repo.create(
                CaptureCreate(
                    capture_key=f"evidence-child-{uuid.uuid4()}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.MANUAL,
                    captured_at=NOW,
                    coordinate=Coordinate(latitude=LAT, longitude=LON),
                    quality={
                        "uploaded_by": owner,
                        "additional_evidence": {
                            "capture_id": str(parent.id),
                            "public_id": parent.public_id,
                        },
                        "inference": {"status": "needs_review"},
                    },
                )
            )
            ids.append(child.id)
            service = CoreService(repo, EventRepository(session), DecisionRepository(session))
            markers = await service.capture_markers(owner)
            assert [row["id"] for row in markers] == [parent.id]
            assert markers[0]["reporters_count"] == 1
            assert len(await service.capture_markers(owner, include_unlocated=True)) == 2
        async with database.session() as session:
            service = CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            )
            assert (
                await detach_evidence(
                    child.id,
                    AuthenticatedUser(owner, None, "authenticated", urmind_role="reviewer"),
                    service,
                )
            )["detached"]
            assert len(await service.capture_markers(owner)) == 2
            reviews = await service.decisions.capture_review_history(child.id)
            assert reviews[0]["decision"] == "detach_evidence"
            assert await service.decisions.review_votes(None, capture_id=child.id) == []
            assert not await service.events.for_capture(child.id)
    finally:
        async with database.session() as session:
            for identifier in reversed(ids):
                await session.execute(
                    text("delete from pgmq.q_inference_jobs where message->>'capture_id'=:id"),
                    {"id": str(identifier)},
                )
                await session.execute(
                    text("delete from public.audit_log where entity_id=:id"), {"id": identifier}
                )
                await session.execute(
                    text("delete from public.reviews where capture_id=:id"), {"id": identifier}
                )
                await session.execute(
                    text("delete from public.captures where id=:id"), {"id": identifier}
                )


@pytest.mark.asyncio
async def test_privacy_consent_owner_rls_and_version(database):
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    async with database.session() as session:
        repo = CaptureRepository(session)
        assert not await repo.has_privacy_consent(owner, "test-v1")
        await repo.accept_privacy_notice(owner, "test-v1")
        await repo.accept_privacy_notice(owner, "test-v1")
        assert await repo.has_privacy_consent(owner, "test-v1")
        assert not await repo.has_privacy_consent(owner, "test-v2")
        assert not await repo.has_privacy_consent(other, "test-v1")
        await session.execute(text("set local role authenticated"))
        await session.execute(
            text("select set_config('request.jwt.claim.sub',:owner,true)"), {"owner": other}
        )
        assert not await repo.has_privacy_consent(owner, "test-v1")
        await session.execute(
            text("select set_config('request.jwt.claim.sub',:owner,true)"), {"owner": owner}
        )
        assert await repo.has_privacy_consent(owner, "test-v1")
        await session.rollback()


@pytest.mark.asyncio
async def test_photo_admission_lease_is_shared_and_expiring(database):
    owner, token, competing = str(uuid.uuid4()), uuid.uuid4(), uuid.uuid4()
    try:
        async with database.session() as first:
            assert await CaptureRepository(first).acquire_photo_lease(owner, token)
        async with database.session() as second:
            repo = CaptureRepository(second)
            assert not await repo.acquire_photo_lease(owner, competing)
            await repo.release_photo_lease(owner, competing)
            assert not await repo.acquire_photo_lease(owner, competing)
            await second.execute(
                text(
                    "update public.photo_admission_leases set expires_at=now()-interval '1 second' where owner_id=cast(:owner as uuid)"
                ),
                {"owner": owner},
            )
        async with database.session() as third:
            repo = CaptureRepository(third)
            assert await repo.acquire_photo_lease(owner, competing)
            assert not await third.scalar(
                text(
                    "select has_table_privilege('authenticated','public.photo_admission_leases','SELECT')"
                )
            )
            assert await third.scalar(
                text(
                    "select relrowsecurity from pg_class where oid='public.photo_admission_leases'::regclass"
                )
            )
    finally:
        async with database.session() as cleanup:
            await cleanup.execute(
                text(
                    "delete from public.photo_admission_leases where owner_id=cast(:owner as uuid)"
                ),
                {"owner": owner},
            )


@pytest.mark.asyncio
async def test_operational_policy_persistence_and_rls(database):
    from app.repositories.core import DecisionRepository
    from app.schemas.core import PhotoGatePolicy

    async with database.session() as session:
        repo = DecisionRepository(session)
        await repo.photo_gate_policy(lock=True)
        await repo.save_photo_gate_policy(PhotoGatePolicy(min_side=800))
        assert (await repo.photo_gate_policy()).min_side == 800
        totals = await repo.report_totals()
        assert totals["day_timezone"] == "UTC"
        assert totals["published"] >= 0
        models = await repo.operational_models()
        assert all("metrics" not in row and "checksum" not in row for row in models)
        rows = await repo.audit_page(operation=None, offset=0, limit=3)
        assert len(rows) <= 3 and all("actor" not in row for row in rows)
        service = CoreService(CaptureRepository(session), EventRepository(session), repo)
        assert (await service.tabular_ground_truth())["training_authorized"] is False
        assert await session.scalar(
            text(
                "select relrowsecurity from pg_class where oid='public.operational_configuration'::regclass"
            )
        )
        assert not await session.scalar(
            text(
                "select has_table_privilege('authenticated', 'public.operational_configuration', 'SELECT')"
            )
        )
        assert not await session.scalar(
            text("select has_table_privilege('anon', 'public.operational_configuration', 'UPDATE')")
        )
        await session.rollback()


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
async def test_report_identity_unique_server_generated_and_owner_lookup(database):
    import re

    owner = str(uuid.uuid4())
    async with database.session() as session:
        repo = CaptureRepository(session)
        identities, protocols = set(), set()
        for _ in range(20):
            row = await repo.create(
                CaptureCreate(
                    capture_key=f"identity-test-{uuid.uuid4()}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.UNKNOWN,
                    captured_at=NOW,
                    quality={"uploaded_by": owner},
                )
            )
            assert re.fullmatch(r"[a-f0-9]{32}", row.public_id)
            assert row.public_id != row.id.hex
            assert re.fullmatch(r"URM-[2-9A-HJ-NP-Z]{8}", row.protocol_code)
            assert row.public_id not in identities and row.protocol_code not in protocols
            identities.add(row.public_id)
            protocols.add(row.protocol_code)
        assert await repo.get_by_protocol(row.protocol_code, owner) is row
        assert await repo.get_by_protocol(row.protocol_code, str(uuid.uuid4())) is None
        # Constraints remain the final protection, even for direct runtime SQL.
        with pytest.raises(exc.IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text("update public.captures set protocol_code=:code where public_id=:id"),
                    {
                        "code": row.protocol_code,
                        "id": next(value for value in identities if value != row.public_id),
                    },
                )
        await session.rollback()


@pytest.mark.asyncio
async def test_human_capture_review_creates_no_detection_and_preserves_original(database):
    from app.repositories.core import DecisionRepository
    from app.schemas.core import CaptureReviewCreate

    async with database.session() as session:
        captures, events = CaptureRepository(session), EventRepository(session)
        decisions = DecisionRepository(session)
        service = CoreService(captures, events, decisions)
        capture = await captures.create(
            CaptureCreate(
                capture_key=f"human-review-test-{uuid.uuid4()}",
                source=CaptureSource.PWA_PHOTO,
                source_location=LocationSource.MANUAL,
                captured_at=NOW,
                coordinate=Coordinate(latitude=LAT, longitude=LON),
                quality={"uploaded_by": "test-owner"},
            )
        )
        rejected = await service.review_capture(
            capture.id,
            CaptureReviewCreate(decision="reject"),
            reviewer="test-reviewer",
            reviewer_role="reviewer",
        )
        assert rejected["event_id"] is None
        assert await events.for_capture(capture.id) == []
        accepted = await service.review_capture(
            capture.id,
            CaptureReviewCreate(
                decision="correct",
                corrected_class="URMIND_FALLEN_TREE",
                adjudicate=True,
                corrected_location=Coordinate(latitude=LAT + 0.001, longitude=LON),
            ),
            reviewer="test-admin",
            reviewer_role="admin",
        )
        event = await events.get(accepted["event_id"])
        assert event.factors["origin"] == "human_review"
        assert event.model_version_id is None and event.visual_confidence is None
        assert event.status == "confirmed"
        assert accepted["ground_truth_status"] == "adjudicated"
        original = await captures.location(capture.id)
        assert original["latitude"] == LAT
        assert capture.quality["human_review"]["corrected_location"]["latitude"] == LAT + 0.001
        assert await events.evidence_detections(event) == []
        assert len(await decisions.capture_review_history(capture.id)) == 2
        await session.rollback()


@pytest.mark.asyncio
async def test_photo_report_marker_exif_storage_owner_and_missing_location(database):
    """Real Storage + PostGIS + queue + RLS. No Auth change, model or fake Event."""
    from fastapi import UploadFile
    from PIL import Image

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from tests.test_exif import build_jpeg, gps_block

    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    storage = StorageClient(get_settings())
    ids = []
    paths = []
    try:
        for located in (True, False):
            # Synthetic image generated exclusively for this disposable integration test.
            data = build_jpeg(gps=gps_block() if located else None)
            with Image.open(io.BytesIO(data)) as metadata_source:
                report_buffer = io.BytesIO()
                Image.effect_noise((640, 640), 30).convert("RGB").save(
                    report_buffer, format="JPEG", exif=metadata_source.getexif()
                )
                data = report_buffer.getvalue()
            async with database.sessionmaker() as session:
                service = CoreService(CaptureRepository(session), EventRepository(session))
                result = await upload_photo(
                    user=AuthenticatedUser(owner, None, "authenticated"),
                    service=service,
                    storage=storage,
                    file=UploadFile(filename="integration-report.jpg", file=io.BytesIO(data)),
                    user_description="<script>plain text</script>",
                )
                capture_id = result["id"]
                ids.append(capture_id)
                capture = await service.captures.get(capture_id)
                paths.append(capture.storage_path)
                assert await service.captures.recent_similar_photo(
                    owner, capture.quality["phash"], datetime.now(UTC), 0
                )
                assert not await service.captures.recent_similar_photo(
                    other, capture.quality["phash"], datetime.now(UTC), 16
                )
                assert await storage.download(capture.storage_path) == data
                assert result["requires_manual_location"] is not located
                markers = await service.capture_markers(owner)
                assert any(row["id"] == capture_id for row in markers) is located
                assert not await service.capture_markers(other)
                if not located:
                    assert (
                        await session.scalar(
                            text(
                                "select count(*) from pgmq.q_inference_jobs where message->>'capture_id'=:id"
                            ),
                            {"id": str(capture_id)},
                        )
                        == 0
                    )
                    assert await service.captures.fill_missing_location(
                        capture_id, owner, Coordinate(latitude=LAT, longitude=LON)
                    )
                    await session.commit()
                    assert not await service.captures.fill_missing_location(
                        capture_id, owner, Coordinate(latitude=LAT + 1, longitude=LON)
                    )
                    markers = await service.capture_markers(owner)
                marker = next(row for row in markers if row["id"] == capture_id)
                assert marker["location_source"] == ("exif" if located else "manual")
                assert marker["urmind_class"] is None and marker["severity"] is None
                assert marker["user_description"] == "<script>plain text</script>"
                assert (
                    await session.scalar(
                        text(
                            "select count(*) from pgmq.q_inference_jobs where message->>'capture_id'=:id"
                        ),
                        {"id": str(capture_id)},
                    )
                    == 1
                )
                # Realtime SELECT authorization uses the same RLS as Data API.
                await session.execute(text("set local role authenticated"))
                for caller, role, anonymous, count in (
                    (owner, None, True, 1),
                    (other, None, True, 0),
                    (other, "reviewer", False, 1),
                    (other, "reviewer", True, 0),
                ):
                    await session.execute(
                        text("select set_config('request.jwt.claims', :claims, true)"),
                        {
                            "claims": json.dumps(
                                {
                                    "sub": caller,
                                    "is_anonymous": anonymous,
                                    "app_metadata": {"urmind_role": role},
                                }
                            )
                        },
                    )
                    assert (
                        await session.scalar(
                            text("select count(*) from public.captures where id=:id"),
                            {"id": capture_id},
                        )
                        == count
                    )
                await session.rollback()
    finally:
        async with database.session() as session:
            for capture_id in ids:
                for table in ("q_inference_jobs", "a_inference_jobs"):
                    await session.execute(
                        text(f"delete from pgmq.{table} where message->>'capture_id'=:id"),
                        {"id": str(capture_id)},
                    )
                await session.execute(
                    text("delete from public.captures where id=:id"), {"id": capture_id}
                )
        for path in paths:
            await storage.delete(path)


@pytest.mark.asyncio
async def test_history_archive_retains_observations_and_rejects_mutation(database):
    from app.repositories.core import HistoryRepository
    from app.services.history import snapshot_hash, snapshot_report

    # Fully rolled back fixture. This is not evidence of the visual E2E pipeline.
    async with database.sessionmaker() as session:
        async with session.begin():
            repository = HistoryRepository(session)
            event_id = await session.scalar(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, occurred_at) "
                    "values (:key, 'URMIND_ROAD_D40', 'photo', statement_timestamp() - interval '1 day') "
                    "returning id"
                ),
                {"key": f"integration-history-{uuid.uuid4()}"},
            )
            payload = await repository.snapshot_observations(90)
            digest = snapshot_hash(payload)
            archive_id = await repository.save_snapshot(payload, digest)
            first = snapshot_report(payload, digest)
            loaded, loaded_digest = await repository.load_snapshot(archive_id)
            assert snapshot_report(loaded, loaded_digest) == first
            assert first["scientific_feature_eligible"] is False
            original = next(r for r in payload["observations"] if r["event_id"] == str(event_id))
            assert original["status"] != "confirmed"
            await session.execute(
                text("update public.events set status='confirmed' where id=:id"), {"id": event_id}
            )
            await session.execute(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, occurred_at) "
                    "values (:key, 'URMIND_ROAD_D40', 'photo', statement_timestamp() - interval '2 days')"
                ),
                {"key": f"integration-history-backdated-{uuid.uuid4()}"},
            )
            newer = await repository.snapshot_observations(90)
            assert len(newer["observations"]) == len(payload["observations"]) + 1
            assert (
                next(r for r in newer["observations"] if r["event_id"] == str(event_id))["status"]
                == "confirmed"
            )
            archived, archived_digest = await repository.load_snapshot(archive_id)
            assert snapshot_report(archived, archived_digest) == first
            for statement in (
                "update public.audit_log set after_data = '{}'::jsonb where id = :id",
                "delete from public.audit_log where id = :id",
            ):
                with pytest.raises(exc.IntegrityError, match="immutable"):
                    async with session.begin_nested():
                        await session.execute(text(statement), {"id": archive_id})
            await session.rollback()
        assert not session.in_transaction()


@pytest.mark.asyncio
async def test_public_image_quota_shared_atomic_expiring_and_private(database):
    from app.repositories.core import PublicImageQuota, QuotaExceededError

    caller = hashlib.sha256(f"quota-test-{uuid.uuid4()}".encode()).hexdigest()
    other = hashlib.sha256(f"quota-test-{uuid.uuid4()}".encode()).hexdigest()
    resource = uuid.uuid4()
    second = Database(get_settings())
    first_quota = PublicImageQuota(database.sessionmaker)
    second_quota = PublicImageQuota(second.sessionmaker)
    try:
        async with database.session() as session:
            # Seed only ephemeral quota fixtures, never Detection/Event/evidence.
            await session.execute(
                text(
                    "insert into public.public_image_admissions "
                    "(stage, caller_hash, resource_id, admitted_at) "
                    "select 'download', :caller, :resource, clock_timestamp() "
                    "from generate_series(1,29)"
                ),
                {"caller": caller, "resource": resource},
            )
        results = await asyncio.gather(
            first_quota.admit("download", caller, resource),
            second_quota.admit("download", other, resource),
            return_exceptions=True,
        )
        assert sum(value is None for value in results) == 1
        assert sum(isinstance(value, QuotaExceededError) for value in results) == 1
        async with database.session() as session:
            assert (
                await session.scalar(
                    text(
                        "select count(*) from public.public_image_admissions where resource_id=:resource"
                    ),
                    {"resource": resource},
                )
                == 30
            )
            # Another resource/caller cannot bypass the separate global download cap.
            await session.execute(
                text(
                    "insert into public.public_image_admissions "
                    "(stage, caller_hash, resource_id, admitted_at) "
                    "select 'download', :caller, :resource, clock_timestamp() from generate_series(1,30)"
                ),
                {"caller": caller, "resource": uuid.uuid4()},
            )
        with pytest.raises(QuotaExceededError):
            await second_quota.admit("download", other, uuid.uuid4())
        # Lookup budget is independent of exhausted downloads and of other callers.
        async with database.session() as session:
            await session.execute(
                text(
                    "insert into public.public_image_admissions (stage, caller_hash, admitted_at) "
                    "select 'lookup', :caller, clock_timestamp() from generate_series(1,120)"
                ),
                {"caller": caller},
            )
        with pytest.raises(QuotaExceededError):
            await first_quota.admit("lookup", caller)
        await second_quota.admit("lookup", other)
        async with database.session() as session:
            await session.execute(
                text(
                    "update public.public_image_admissions set admitted_at=clock_timestamp()-interval '61 seconds' "
                    "where caller_hash=:caller"
                ),
                {"caller": caller},
            )
        await second_quota.admit("lookup", caller)
        async with database.session() as session:
            assert (
                await session.scalar(
                    text(
                        "select count(*) from public.public_image_admissions where stage='lookup' and caller_hash=:caller"
                    ),
                    {"caller": caller},
                )
                == 1
            )
            for role in ("anon", "authenticated"):
                assert not await session.scalar(
                    text(
                        "select has_table_privilege(:role, 'public.public_image_admissions', 'SELECT')"
                    ),
                    {"role": role},
                )
                assert not await session.scalar(
                    text(
                        "select has_table_privilege(:role, 'public.public_image_admissions', 'INSERT')"
                    ),
                    {"role": role},
                )
            assert await session.scalar(
                text(
                    "select relrowsecurity from pg_class where oid='public.public_image_admissions'::regclass"
                )
            )
    finally:
        async with database.session() as session:
            await session.execute(
                text(
                    "delete from public.public_image_admissions where caller_hash in (:caller,:other)"
                ),
                {"caller": caller, "other": other},
            )
        await second.close()


@pytest.mark.asyncio
async def test_publication_owner_isolation_and_latest_review_gate_real_postgres(database):
    from app.repositories.core import DecisionRepository, PublicRepository

    owner = f"publication-owner-{uuid.uuid4()}"
    fixture_time = datetime.now(UTC)
    async with database.sessionmaker() as session:
        try:
            capture = await CaptureRepository(session).create(
                CaptureCreate(
                    capture_key=f"publication-{uuid.uuid4()}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.MANUAL,
                    captured_at=fixture_time,
                    coordinate=Coordinate(latitude=LAT, longitude=LON),
                    quality={"uploaded_by": owner},
                )
            )
            event = await EventRepository(session).create(
                EventCreate(
                    event_key=f"publication-{uuid.uuid4()}",
                    capture_id=capture.id,
                    urmind_class=UrmindClass.ROAD_D40,
                    evidence_mode=EvidenceMode.PHOTO,
                    occurred_at=fixture_time,
                    coordinate=Coordinate(latitude=LAT, longitude=LON),
                    visual_confidence=0.7,
                )
            )
            public = PublicRepository(session)
            decisions = DecisionRepository(session)
            assert await public.event(event.id) is None
            assert await public.event(event.id, owner_id="other-owner") is None
            assert (await public.event(event.id, owner_id=owner))["id"] == event.id
            assert event.id not in {r["id"] for r in await public.events(limit=200)}
            review = await decisions.add_review(
                event_id=event.id,
                reviewer="integration-reviewer",
                decision="confirm",
                corrected_class=None,
                notes="rollback fixture",
            )
            event.status = "confirmed"
            publication = {
                "policy_version": "urmind-publication-v1",
                "status": "published",
                "review_id": str(review.id),
                "reviewer": review.reviewer,
                "published_at": NOW.isoformat(),
            }
            event.factors = {**(event.factors or {}), "publication": publication}
            await session.flush()
            assert (await public.event(event.id))["id"] == event.id
            # Filter by this fixture's ID after SQL publication evaluation; no
            # assumption about unrelated rows already present in an opt-in DB.
            rows = await public.events(limit=200, since=fixture_time, status="confirmed")
            assert event.id in {r["id"] for r in rows}
            event.factors = {
                **event.factors,
                "publication": {
                    **publication,
                    "policy_version": "unknown-policy",
                },
            }
            await session.flush()
            assert await public.event(event.id) is None
            event.factors = {**event.factors, "publication": publication}
            await decisions.add_review(
                event_id=event.id,
                reviewer="integration-reviewer",
                decision="confirm",
                corrected_class=None,
                notes="later review invalidates old publication",
            )
            assert await public.event(event.id) is None
            assert (await public.event(event.id, owner_id=owner))["id"] == event.id
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_public_capture_quota_uses_real_postgres_ordered_window(database):
    owner = f"integration-quota-{uuid.uuid4()}"
    now = datetime.now(UTC)
    async with database.sessionmaker() as session:
        repo = CaptureRepository(session)
        try:
            await repo.lock_public_uploads()
            await repo.lock_owner_uploads(owner)
            public_before = await repo.recent_public_uploads(now)
            assert await repo.recent_owner_uploads(owner, now) == 0
            await repo.create(
                CaptureCreate(
                    capture_key=f"quota-{uuid.uuid4()}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.MANUAL,
                    captured_at=now,
                    storage_path=f"integration-fixture/{uuid.uuid4()}.jpg",
                    coordinate=Coordinate(latitude=LAT, longitude=LON),
                    quality={"uploaded_by": owner, "public_upload": True},
                )
            )
            assert await repo.recent_owner_uploads(owner, now) == 1
            assert await repo.recent_public_uploads(now) == public_before + 1
        finally:
            # The Capture and its queue-triggered job are both test-only.
            await session.rollback()


@pytest.mark.asyncio
async def test_storage_real_upload_download_signed_url_and_cleanup():
    import httpx
    from PIL import Image

    canvas = Image.new("RGB", (8, 8), (12, 34, 56))
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG")
    image = validate_image(buffer.getvalue())
    path = object_path("integration-fixture", image, datetime.now(UTC), upload_id=uuid.uuid4().hex)
    storage = StorageClient(get_settings())
    uploaded = False
    try:
        uploaded = await storage.upload(path, image)
        assert uploaded
        assert await storage.download(path) == image.data
        signed_url = await storage.signed_url(path, expires_in=60)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(signed_url)
        assert response.status_code == 200
        assert response.content == image.data
    finally:
        if uploaded:
            await storage.delete(path)


@pytest.mark.asyncio
async def test_storage_compensates_real_db_constraint_failure(database):
    from fastapi import UploadFile
    from PIL import Image

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.services.storage import StorageError

    buffer = io.BytesIO()
    Image.effect_noise((640, 640), 30).convert("RGB").save(buffer, format="JPEG")
    image = validate_image(buffer.getvalue())
    fixture_owner = f"integration-fixture-{uuid.uuid4()}"
    capture_key = f"photo-{hashlib.sha256(f'{fixture_owner}:{image.sha256}'.encode()).hexdigest()}"
    async with database.session() as session:
        await session.execute(
            text(
                "insert into public.captures(capture_key, source, source_location, captured_at) "
                "values (:key, 'pwa_photo', 'unknown', now())"
            ),
            {"key": capture_key},
        )

    class RecordingStorage(StorageClient):
        uploaded_path = None
        deleted_path = None

        async def upload(self, path, payload):
            self.uploaded_path = path
            return await super().upload(path, payload)

        async def delete(self, path):
            self.deleted_path = path
            await super().delete(path)

    storage = RecordingStorage(get_settings())
    try:
        async with database.sessionmaker() as session:
            real_repo = CaptureRepository(session)

            class RaceCaptureRepository:
                # Simulate a stale pre-upload read; the database uniqueness
                # constraint remains the authoritative failure at flush.
                async def get_by_key(self, _key):
                    return None

                async def create(self, payload):
                    return await real_repo.create(payload)

                async def recent_similar_photo(self, *args):
                    return await real_repo.recent_similar_photo(*args)

            captures = RaceCaptureRepository()
            captures.session = session
            service = CoreService(captures, EventRepository(session))
            with pytest.raises(exc.IntegrityError):
                await upload_photo(
                    user=AuthenticatedUser(
                        id=fixture_owner,
                        email=None,
                        role="authenticated",
                        urmind_role="reviewer",
                    ),
                    service=service,
                    storage=storage,
                    file=UploadFile(filename="fixture.jpg", file=io.BytesIO(image.data)),
                    source=CaptureSource.PWA_PHOTO,
                    latitude=LAT,
                    longitude=LON,
                    accuracy_m=None,
                    location_source=LocationSource.MANUAL,
                    tz_offset_minutes=None,
                )
            assert storage.uploaded_path == storage.deleted_path
            with pytest.raises(StorageError):
                await storage.download(storage.uploaded_path)
    finally:
        # Test cleanup is independent from the behavior under test: a failed
        # compensation must still remove its uniquely identified fixture.
        try:
            if getattr(storage, "uploaded_path", None):
                await storage.delete(storage.uploaded_path)
        finally:
            async with database.session() as session:
                await session.execute(
                    text("delete from public.captures where capture_key=:key"), {"key": capture_key}
                )


@pytest.mark.asyncio
async def test_capture_trigger_queue_read_archive_and_transaction_rollback(database):
    from app.repositories.core import InferenceRepository

    async with database.sessionmaker() as session:
        try:
            capture_id = await session.scalar(
                text(
                    "insert into public.captures(capture_key, source, source_location, "
                    "captured_at, storage_path, point) values (:key, 'pwa_photo', 'manual', "
                    "now(), :path, ST_SetSRID(ST_MakePoint(-46.63,-23.55),4326)::geography) returning id"
                ),
                {
                    "key": f"integration-queue-{uuid.uuid4().hex}",
                    "path": f"integration-fixture/{uuid.uuid4().hex}.jpg",
                },
            )
            queue = InferenceRepository(session)
            job = await queue.read_job(visibility_timeout_s=2)
            assert job is not None
            assert job["message"]["capture_id"] == str(capture_id)
            assert job["read_ct"] == 1
            await queue.archive_job(job["msg_id"])
            archived = await session.scalar(
                text("select count(*) from pgmq.a_inference_jobs where msg_id = :id"),
                {"id": job["msg_id"]},
            )
            assert archived == 1
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_rls_and_realtime_publication_are_narrow(database):
    async with database.sessionmaker() as session:
        published = (
            (
                await session.execute(
                    text(
                        "select tablename from pg_publication_tables where "
                        "pubname='supabase_realtime' and schemaname='public'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(published) == {"events", "risk_assessments", "captures"}
        await session.execute(text("set local role anon"))
        with pytest.raises(exc.DBAPIError):
            await session.execute(text("select count(*) from public.events"))
        await session.rollback()


@pytest.mark.asyncio
async def test_realtime_rls_reviewer_only(database):
    async with database.sessionmaker() as session:
        try:
            event_id = await session.scalar(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, occurred_at) "
                    "values (:key, 'URMIND_ROAD_D40', 'photo', now()) returning id"
                ),
                {"key": f"integration-rls-{uuid.uuid4().hex}"},
            )
            await session.execute(
                text(
                    "insert into public.risk_assessments(event_id, severity) values (:id, 'high')"
                ),
                {"id": event_id},
            )
            await session.execute(text("set local role authenticated"))
            for role, expected in (("viewer", 0), ("reviewer", 1), ("admin", 1)):
                claims = json.dumps(
                    {"sub": str(uuid.uuid4()), "app_metadata": {"urmind_role": role}}
                )
                await session.execute(
                    text("select set_config('request.jwt.claims', :claims, true)"),
                    {"claims": claims},
                )
                visible_events = await session.scalar(
                    text("select count(*) from public.events where id=:id"), {"id": event_id}
                )
                visible_risks = await session.scalar(
                    text("select count(*) from public.risk_assessments where event_id=:id"),
                    {"id": event_id},
                )
                assert visible_events == expected
                assert visible_risks == expected
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_history_excludes_backdated_event_inserted_later(database):
    async with database.sessionmaker() as session:
        try:
            segment_id = await session.scalar(
                text(
                    "insert into public.road_segments(geom) values "
                    "(ST_SetSRID(ST_MakeLine(ST_MakePoint(:lon1,:lat), "
                    "ST_MakePoint(:lon2,:lat)),4326)) returning id"
                ),
                {"lon1": LON - 0.001, "lon2": LON + 0.001, "lat": LAT},
            )

            async def insert_event(name, offset_days):
                return await session.scalar(
                    text(
                        "insert into public.events(event_key, urmind_class, evidence_mode, "
                        "occurred_at, road_segment_id) values (:key, 'URMIND_ROAD_D40', "
                        "'photo', :occurred_at, :segment_id) returning id"
                    ),
                    {
                        "key": f"integration-history-{name}-{uuid.uuid4().hex}",
                        "occurred_at": NOW + timedelta(days=offset_days),
                        "segment_id": segment_id,
                    },
                )

            await insert_event("known", -2)
            current_id = await insert_event("current", 0)
            await insert_event("late-backdated", -1)
            repo = EventRepository(session)
            current = await repo.get(current_id)
            assert current is not None
            previous, status = await repo.previous_event_times(current)
            assert previous == [NOW - timedelta(days=2)]
            assert status == "serialized_commit_order"
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_legacy_order_is_not_counted_as_trusted_history(database):
    async with database.sessionmaker() as session:
        try:
            segment_id = await session.scalar(
                text(
                    "insert into public.road_segments(geom) values "
                    "(ST_SetSRID(ST_MakeLine(ST_MakePoint(:lon1,:lat), "
                    "ST_MakePoint(:lon2,:lat)),4326)) returning id"
                ),
                {"lon1": LON - 0.001, "lon2": LON + 0.001, "lat": LAT},
            )
            legacy_id = await session.scalar(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, "
                    "occurred_at, road_segment_id, order_source) values "
                    "(:key, 'URMIND_ROAD_D40', 'photo', :occurred_at, :segment_id, "
                    "'legacy_backfill') returning id"
                ),
                {
                    "key": f"integration-legacy-{uuid.uuid4().hex}",
                    "occurred_at": NOW - timedelta(days=2),
                    "segment_id": segment_id,
                },
            )
            current_id = await session.scalar(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, "
                    "occurred_at, road_segment_id) values "
                    "(:key, 'URMIND_ROAD_D40', 'photo', :occurred_at, :segment_id) returning id"
                ),
                {
                    "key": f"integration-current-{uuid.uuid4().hex}",
                    "occurred_at": NOW,
                    "segment_id": segment_id,
                },
            )
            repo = EventRepository(session)
            legacy = await repo.get(legacy_id)
            current = await repo.get(current_id)
            assert legacy is not None and current is not None
            assert legacy.order_source == "legacy_backfill"
            assert current.order_source == "serialized_commit_order"
            assert current.commit_order is not None
            assert legacy.commit_order is None
            assert await repo.previous_event_times(legacy) == (None, "legacy_order_uncertain")
            assert await repo.previous_event_times(current) == (None, "legacy_order_uncertain")
            with pytest.raises(Exception, match="order provenance is immutable"):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "update public.events set order_source='serialized_commit_order' where id=:id"
                        ),
                        {"id": legacy_id},
                    )
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_commit_order_serializes_concurrent_event_inserts(database):
    async with database.session() as setup:
        segment_id = await setup.scalar(
            text(
                "insert into public.road_segments(geom) values "
                "(ST_SetSRID(ST_MakeLine(ST_MakePoint(:lon1,:lat), "
                "ST_MakePoint(:lon2,:lat)),4326)) returning id"
            ),
            {"lon1": LON - 0.001, "lon2": LON + 0.001, "lat": LAT},
        )
    first_id = second_id = None
    try:
        async with database.sessionmaker() as first, database.sessionmaker() as second:
            first_id = await first.scalar(
                text(
                    "insert into public.events(event_key, urmind_class, evidence_mode, "
                    "occurred_at, road_segment_id) values "
                    "(:key, 'URMIND_ROAD_D40', 'photo', :occurred_at, :segment_id) returning id"
                ),
                {
                    "key": f"integration-serial-first-{uuid.uuid4().hex}",
                    "occurred_at": NOW - timedelta(days=2),
                    "segment_id": segment_id,
                },
            )
            second_pid = await second.scalar(text("select pg_backend_pid()"))
            started = asyncio.Event()

            async def insert_second():
                started.set()
                event_id = await second.scalar(
                    text(
                        "insert into public.events(event_key, urmind_class, evidence_mode, "
                        "occurred_at, road_segment_id) values "
                        "(:key, 'URMIND_ROAD_D40', 'photo', :occurred_at, :segment_id) returning id"
                    ),
                    {
                        "key": f"integration-serial-second-{uuid.uuid4().hex}",
                        "occurred_at": NOW,
                        "segment_id": segment_id,
                    },
                )
                await second.commit()
                return event_id

            task = asyncio.create_task(insert_second())
            await asyncio.wait_for(started.wait(), timeout=5)
            async with database.sessionmaker() as observer:
                for _ in range(60):
                    wait_type = await observer.scalar(
                        text("select wait_event_type from pg_stat_activity where pid=:pid"),
                        {"pid": second_pid},
                    )
                    if wait_type == "Lock":
                        break
                    await asyncio.sleep(0.05)
                blocked_before_commit = wait_type == "Lock" and not task.done()
            await first.commit()
            second_id = await asyncio.wait_for(task, timeout=5)
            assert blocked_before_commit
        async with database.sessionmaker() as verify:
            repo = EventRepository(verify)
            earlier = await repo.get(first_id)
            later = await repo.get(second_id)
            assert earlier is not None and later is not None
            assert earlier.commit_order < later.commit_order
            assert await repo.previous_event_times(later) == (
                [NOW - timedelta(days=2)],
                "serialized_commit_order",
            )
    finally:
        async with database.session() as cleanup:
            for event_id in (first_id, second_id):
                if event_id is not None:
                    await cleanup.execute(
                        text("delete from public.events where id=:id"), {"id": event_id}
                    )
            await cleanup.execute(
                text("delete from public.road_segments where id=:id"), {"id": segment_id}
            )


@pytest.mark.asyncio
async def test_supabase_auth_real_roles_login_and_jwks():
    import httpx

    from app.auth import decode_token

    settings = get_settings()
    assert settings.supabase_url and settings.supabase_secret_key
    assert settings.supabase_publishable_key and settings.supabase_jwks_url
    admin_headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    public_headers = {"apikey": settings.supabase_publishable_key}
    created_ids = []
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            for role in ("viewer", "reviewer", "admin"):
                email = f"urmind-integration-{uuid.uuid4().hex}@example.invalid"
                password = secrets.token_urlsafe(24)
                response = await client.post(
                    f"{settings.supabase_url}/auth/v1/admin/users",
                    headers=admin_headers,
                    json={
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                        "app_metadata": {"urmind_role": role},
                    },
                )
                assert response.status_code in (200, 201), (
                    f"admin create HTTP {response.status_code}"
                )
                user_id = response.json()["id"]
                created_ids.append(user_id)
                login = await client.post(
                    f"{settings.supabase_url}/auth/v1/token?grant_type=password",
                    headers=public_headers,
                    json={"email": email, "password": password},
                )
                assert login.status_code == 200, f"login HTTP {login.status_code}"
                claims = decode_token(login.json()["access_token"], settings)
                assert claims["sub"] == user_id
                assert claims["app_metadata"]["urmind_role"] == role
        finally:
            for user_id in created_ids:
                response = await client.delete(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers=admin_headers,
                )
                assert response.status_code in (200, 204), (
                    f"admin delete HTTP {response.status_code}"
                )


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

        nearby = await service.events_nearby(NearbyQuery(latitude=LAT, longitude=LON, radius_m=200))
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
    from app.services.context import ContextResult

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
            dataset_id = await session.scalar(
                text(
                    "insert into public.dataset_versions(name, version, source) "
                    "values (:name, 'integration', 'test_fixture') returning id"
                ),
                {"name": f"test-dataset-{suffix}"},
            )
            model_id = await session.scalar(
                text(
                    "insert into public.model_versions(name, kind, version, dataset_version_id) "
                    "values (:name, 'vision', 'integration', :dataset_id) returning id"
                ),
                {"name": f"test-model-{suffix}", "dataset_id": dataset_id},
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
                            model_version_id=model_id,
                        ),
                        DetectionCreate(
                            urmind_class=UrmindClass.ROAD_D00,
                            confidence=0.10,  # abaixo do mínimo: não vira evento
                            bbox={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                            model_version_id=model_id,
                        ),
                    ],
                )

            first = await service.register_capture(capture(1, 0.00003))
            result = await service.consolidate_capture(first["id"])
            (outcome,) = result["events"]
            assert outcome["created"] is True
            assert outcome["risk"]["severity"] in {"low", "medium", "high"}
            assert outcome["risk"]["responsible"].startswith("DNIT")
            assert outcome["risk"]["action"] == "sinalizacao_temporaria"
            assert outcome["risk"]["ruleset_version"] == "urmind-risk-rules-v1"
            before_context = await service.event_features(outcome["event_id"])
            assert before_context["feature_schema_version"] == "urmind-features-v1"
            assert before_context["context"]["near_school"] is None
            assert before_context["history"]["previous_events_same_segment"] == 0
            refreshed = await service.apply_context(
                outcome["event_id"],
                [
                    ContextResult(
                        source="overpass_pois",
                        status="ok",
                        fetched_at=NOW.isoformat(),
                        provenance={"provider": "integration_fixture"},
                        data={
                            "nearest": {
                                "school": {"distance_m": 80},
                                "health": None,
                                "crossing": None,
                            }
                        },
                    ),
                    ContextResult(
                        source="open_meteo_rain",
                        status="context_unavailable",
                        fetched_at=NOW.isoformat(),
                        provenance={"provider": "integration_fixture"},
                        data={},
                        error="controlled_fixture_unavailable",
                    ),
                ],
            )
            assert refreshed["risk"]["ruleset_version"] == "urmind-risk-rules-v1"
            with_context = await service.event_features(outcome["event_id"])
            assert with_context["context"]["near_school"] is True
            assert with_context["context"]["rain_mm_24h"] is None
            assert with_context["missingness"]["context_unavailable"]["open_meteo_rain"]
            stored_risk = await service.decisions.latest_risk(outcome["event_id"])
            assert stored_risk.factors["phase5"]["feature_schema_version"] == "urmind-features-v1"
            assert stored_risk.priority_score is None
            initial = await service.reproduce_assessment(outcome["risk"]["assessment_id"])
            assert initial["replay_matches"] is True
            assert initial["snapshot"]["context_records"] == []
            observed = await service.reproduce_assessment(refreshed["risk"]["assessment_id"])
            assert observed["replay_matches"] is True
            assert observed["snapshot"]["features"]["context"]["near_school"] is True
            assert observed["snapshot"]["context_records"][1]["payload"]["status"] == "ok"
            assert (
                observed["snapshot"]["context_records"][0]["payload"]["status"]
                == "context_unavailable"
            )
            assert observed["snapshot"]["context_records"][1]["ingested_at"] is not None
            changed = await service.apply_context(
                outcome["event_id"],
                [
                    ContextResult(
                        source="overpass_pois",
                        status="ok",
                        fetched_at=(NOW + timedelta(days=1)).isoformat(),
                        provenance={"provider": "integration_fixture"},
                        data={"nearest": {"school": None, "health": None, "crossing": None}},
                    )
                ],
            )
            updated = await service.reproduce_assessment(changed["risk"]["assessment_id"])
            assert updated["replay_matches"] is True
            assert updated["snapshot"]["features"]["context"]["near_school"] is False
            assert (
                updated["snapshot"]["context_records"][1]["temporal_status"] == "post_event_context"
            )
            assert (
                updated["snapshot"]["features"]["history"]
                == observed["snapshot"]["features"]["history"]
            )
            assert (
                await service.reproduce_assessment(refreshed["risk"]["assessment_id"])
            ) == observed
            assert initial["assessment_id"] != observed["assessment_id"] != updated["assessment_id"]
            historical = await service.apply_context(
                outcome["event_id"],
                [
                    ContextResult(
                        source="open_meteo_rain",
                        status="ok",
                        fetched_at=(NOW + timedelta(days=2)).isoformat(),
                        provenance={"provider": "integration_fixture"},
                        data={
                            "endpoint": "archive",
                            "window_start": (NOW - timedelta(days=1)).isoformat(),
                            "window_end": NOW.isoformat(),
                            "rain_mm_24h": 2.0,
                        },
                    )
                ],
            )
            historical_snapshot = await service.reproduce_assessment(
                historical["risk"]["assessment_id"]
            )
            assert historical_snapshot["replay_matches"] is True
            assert (
                historical_snapshot["snapshot"]["context_records"][0]["temporal_status"]
                == "historical_source"
            )
            assert observed["snapshot"]["features"]["context"]["rain_mm_24h"] is None
            with pytest.raises(Exception, match="snapshot assessment is immutable"):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "update public.risk_assessments set factors = "
                            "jsonb_set(factors, '{phase4_snapshot,feature_schema_version}', "
                            "'\"fabricated\"'::jsonb) where id=:id"
                        ),
                        {"id": observed["assessment_id"]},
                    )
            with pytest.raises(Exception, match="snapshot assessment is immutable"):
                async with session.begin_nested():
                    await session.execute(
                        text("update public.risk_assessments set severity='low' where id=:id"),
                        {"id": observed["assessment_id"]},
                    )
            with pytest.raises(Exception, match="archived assessment cannot be deleted"):
                async with session.begin_nested():
                    await session.execute(
                        text("delete from public.events where id=:id"),
                        {"id": outcome["event_id"]},
                    )

            again = await service.consolidate_capture(first["id"])
            assert again["events"][0] == {
                "event_id": outcome["event_id"],
                "created": False,
                "deduplicated": False,
            }

            second = await service.register_capture(capture(2, 0.00005))  # ~2 m do primeiro
            dedup = await service.consolidate_capture(second["id"])
            assert dedup["events"][0]["event_id"] == outcome["event_id"]
            assert dedup["events"][0]["deduplicated"] is True

            dossier = await service.event_dossier(outcome["event_id"])
            assert len(dossier["factors"]["evidence"]["capture_ids"]) == 2
            assert dossier["road_segment_id"] is not None
            assert "DNIT" in dossier["report"]
            assert "Regra aplicada: urmind-risk-rules-v1" in dossier["report"]
            assert "Incerteza:" not in dossier["report"]
            assert "Fatores disponíveis:" not in dossier["report"]
            assert dossier["detections"] and all(
                d["confidence"] >= 0.25 for d in dossier["detections"]
            )

            review = await service.review_event(
                outcome["event_id"],
                ReviewCreate(decision=ReviewDecision.CORRECT, corrected_class=UrmindClass.ROAD_D20),
                reviewer="revisor-teste",
                reviewer_role="reviewer",
            )
            assert review["status"] == "review"
            assert review["ground_truth_status"] == "requires_second_review"
            audit = await session.execute(
                text("select before_data, after_data from public.audit_log where entity_id = :id"),
                {"id": outcome["event_id"]},
            )
            before, after = audit.one()
            assert before["urmind_class"] == "URMIND_ROAD_D40"  # inferência original preservada
            assert after["corrected_class"] == "URMIND_ROAD_D20"
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_ground_truth_consensus_export_with_real_storage(database, tmp_path):
    from PIL import Image

    from app.repositories.core import DecisionRepository
    from app.schemas.core import DetectionCreate, ReviewCreate, ReviewDecision
    from app.services.review_export import (
        eligible_candidates,
        verify_candidate_objects,
        write_batch,
    )

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 20, 10)).save(buffer, format="JPEG")
    image = validate_image(buffer.getvalue())
    path = object_path("integration-fixture", image, datetime.now(UTC), upload_id=uuid.uuid4().hex)
    storage = StorageClient(get_settings())
    uploaded = await storage.upload(path, image)
    assert uploaded
    async with database.sessionmaker() as session:
        try:
            suffix = uuid.uuid4().hex
            dataset_id = await session.scalar(
                text(
                    "insert into public.dataset_versions(name, version, source) "
                    "values (:name, 'integration', 'test_fixture') returning id"
                ),
                {"name": f"test-dataset-{suffix}"},
            )
            model_id = await session.scalar(
                text(
                    "insert into public.model_versions(name, kind, version, dataset_version_id) "
                    "values (:name, 'vision', 'integration', :dataset_id) returning id"
                ),
                {"name": f"test-model-{suffix}", "dataset_id": dataset_id},
            )
            service = CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            )
            capture = await service.register_capture(
                CaptureCreate(
                    capture_key=f"integration-gt-{suffix}",
                    source=CaptureSource.PWA_PHOTO,
                    source_location=LocationSource.GPS_DEVICE,
                    captured_at=NOW,
                    storage_path=path,
                    coordinate=Coordinate(latitude=LAT, longitude=LON, accuracy_m=5),
                    quality={"sha256": image.sha256, "integration_fixture": True},
                    detections=[
                        DetectionCreate(
                            urmind_class=UrmindClass.ROAD_D40,
                            confidence=0.91,
                            bbox={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.2},
                            model_version_id=model_id,
                        )
                    ],
                )
            )
            detection_id = await session.scalar(
                text("select id from public.detections where capture_id=:capture_id"),
                {"capture_id": capture["id"]},
            )
            event = await service.register_event(
                EventCreate(
                    event_key=f"integration-gt-event-{suffix}",
                    capture_id=capture["id"],
                    urmind_class=UrmindClass.ROAD_D40,
                    evidence_mode=EvidenceMode.PHOTO,
                    occurred_at=NOW,
                    coordinate=Coordinate(latitude=LAT, longitude=LON, accuracy_m=5),
                    visual_confidence=0.91,
                    model_version_id=model_id,
                    factors={
                        "evidence": {
                            "capture_ids": [str(capture["id"])],
                            "detection_ids": [str(detection_id)],
                        }
                    },
                )
            )
            first = await service.review_event(
                event["id"],
                ReviewCreate(decision=ReviewDecision.CONFIRM),
                reviewer=f"integration-reviewer-1-{suffix}",
                reviewer_role="reviewer",
            )
            assert first["ground_truth_status"] == "requires_second_review"
            assert eligible_candidates(await service.decisions.dataset_candidates()) == []
            second = await service.review_event(
                event["id"],
                ReviewCreate(decision=ReviewDecision.CONFIRM),
                reviewer=f"integration-reviewer-2-{suffix}",
                reviewer_role="reviewer",
            )
            assert second["ground_truth_status"] == "consensus"
            records = eligible_candidates(await service.decisions.dataset_candidates())
            assert len(records) == 1
            assert records[0]["label"]["inferred_class"] == UrmindClass.ROAD_D40.value
            assert records[0]["inference_lineage"]["model_version_id"] == model_id
            assert records[0]["inference_lineage"]["dataset_version_id"] == dataset_id
            await verify_candidate_objects(records, storage)
            manifest = write_batch(records, tmp_path)
            assert manifest["records"] == 1
            assert manifest["retraining_triggered"] is False
            assert (tmp_path / manifest["file"]).is_file()
            conflicting = await service.review_event(
                event["id"],
                ReviewCreate(
                    decision=ReviewDecision.CORRECT,
                    corrected_class=UrmindClass.ROAD_D20,
                ),
                reviewer=f"integration-reviewer-3-{suffix}",
                reviewer_role="reviewer",
            )
            assert conflicting["ground_truth_status"] == "conflicted"
            assert eligible_candidates(await service.decisions.dataset_candidates()) == []
            adjudicated = await service.review_event(
                event["id"],
                ReviewCreate(decision=ReviewDecision.CONFIRM, adjudicate=True),
                reviewer=f"integration-admin-{suffix}",
                reviewer_role="admin",
            )
            assert adjudicated["ground_truth_status"] == "adjudicated"
            assert len(eligible_candidates(await service.decisions.dataset_candidates())) == 1
        finally:
            await session.rollback()
            await storage.delete(path)


@pytest.mark.asyncio
async def test_realtime_delivers_event_change_to_reviewer(database):
    import httpx
    from websockets.asyncio.client import connect

    settings = get_settings()
    assert settings.supabase_url and settings.supabase_secret_key
    assert settings.supabase_publishable_key
    admin_headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    email = f"urmind-integration-realtime-{uuid.uuid4().hex}@example.invalid"
    password = secrets.token_urlsafe(24)
    user_id = None
    event_key = f"integration-realtime-{uuid.uuid4().hex}"
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            created = await client.post(
                f"{settings.supabase_url}/auth/v1/admin/users",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": {"urmind_role": "reviewer"},
                },
            )
            assert created.status_code in (200, 201)
            user_id = created.json()["id"]
            login = await client.post(
                f"{settings.supabase_url}/auth/v1/token?grant_type=password",
                headers={"apikey": settings.supabase_publishable_key},
                json={"email": email, "password": password},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            websocket_url = (
                settings.supabase_url.replace("https://", "wss://")
                + "/realtime/v1/websocket?vsn=1.0.0&apikey="
                + settings.supabase_publishable_key
            )
            stage = "connect"
            try:
                async with connect(websocket_url, open_timeout=10) as socket:
                    stage = "join"
                    await socket.send(
                        json.dumps(
                            {
                                "topic": "realtime:urmind-integration",
                                "event": "phx_join",
                                "payload": {
                                    "config": {
                                        "postgres_changes": [
                                            {
                                                "event": "INSERT",
                                                "schema": "public",
                                                "table": "events",
                                            }
                                        ]
                                    },
                                    "access_token": token,
                                },
                                "ref": "1",
                                "join_ref": "1",
                            }
                        )
                    )
                    reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                    assert reply["event"] == "phx_reply"
                    assert reply["payload"]["status"] == "ok"
                    stage = "subscription_ready"
                    subscribed = False
                    for _ in range(10):
                        try:
                            status_message = json.loads(
                                await asyncio.wait_for(socket.recv(), timeout=2)
                            )
                        except TimeoutError:
                            continue
                        if (
                            status_message.get("event") == "system"
                            and status_message.get("payload", {}).get("status") == "ok"
                            and status_message.get("payload", {}).get("extension")
                            == "postgres_changes"
                        ):
                            subscribed = True
                            break
                    assert subscribed, "Realtime subscription did not become ready"
                    stage = "insert"
                    async with database.session() as session:
                        event_id = await session.scalar(
                            text(
                                "insert into public.events(event_key, urmind_class, "
                                "evidence_mode, occurred_at) values "
                                "(:key, 'URMIND_ROAD_D40', 'photo', now()) returning id"
                            ),
                            {"key": event_key},
                        )
                    delivered = False
                    stage = "receive_change"
                    for _ in range(12):
                        try:
                            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
                        except TimeoutError:
                            continue
                        if message.get("event") == "postgres_changes":
                            record = message["payload"]["data"]["record"]
                            if record.get("id") == str(event_id):
                                delivered = True
                                break
                    assert delivered, "Realtime did not deliver committed event"
            except (OSError, TimeoutError) as exc:
                raise AssertionError(f"Realtime {stage} failed: {type(exc).__name__}") from None
        finally:
            async with database.session() as session:
                await session.execute(
                    text("delete from public.events where event_key=:key"), {"key": event_key}
                )
            if user_id:
                deleted = await client.delete(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers=admin_headers,
                )
                assert deleted.status_code in (200, 204)
