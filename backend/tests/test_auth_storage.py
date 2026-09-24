"""Auth (JWT do Supabase) e validação de upload. Sem rede: chaves e transporte locais."""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image

from app.auth import AUDIENCE, decode_token
from app.config import JWKS_PATH, Settings
from app.services.storage import (
    MAX_BYTES,
    InvalidImageError,
    StorageClient,
    object_path,
    validate_image,
)

BASE = "https://refficticio.supabase.co"
SETTINGS = Settings.model_validate(
    {
        "SUPABASE_URL": BASE,
        "SUPABASE_JWKS_URL": BASE + JWKS_PATH,
        "SUPABASE_SECRET_KEY": "sb_secret_FICTICIA_nao_usar",
    }
)
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(autouse=True)
def isolated_upload_attempt_budget():
    from app.api.v1.core import get_photo_gate_policy, upload_admission
    from app.main import app
    from app.schemas.core import PhotoGatePolicy

    upload_admission.cache_clear()
    app.dependency_overrides[get_photo_gate_policy] = lambda: PhotoGatePolicy()
    yield
    app.dependency_overrides.pop(get_photo_gate_policy, None)
    upload_admission.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "reviewer"])
async def test_photo_gate_configuration_write_requires_admin(role):
    from fastapi import HTTPException, Response

    from app.api.v1.core import update_photo_gate
    from app.auth import AuthenticatedUser
    from app.schemas.core import PhotoGatePolicy

    with pytest.raises(HTTPException) as denied:
        await update_photo_gate(
            PhotoGatePolicy(),
            AuthenticatedUser("owner", None, "authenticated", urmind_role=role),
            SimpleNamespace(),
            Response(),
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_photo_gate_configuration_commit_and_audit_are_atomic():
    from fastapi import Response

    from app.api.v1.core import update_photo_gate
    from app.auth import AuthenticatedUser
    from app.schemas.core import PhotoGatePolicy

    decisions = SimpleNamespace(
        photo_gate_policy=AsyncMock(return_value=PhotoGatePolicy()),
        save_photo_gate_policy=AsyncMock(),
        add_audit=AsyncMock(),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = SimpleNamespace(decisions=decisions, captures=SimpleNamespace(session=session))
    admin = AuthenticatedUser("admin", None, "authenticated", urmind_role="admin")
    policy = PhotoGatePolicy(min_side=800)
    assert await update_photo_gate(policy, admin, service, Response()) == policy
    decisions.photo_gate_policy.assert_awaited_once_with(lock=True)
    assert decisions.add_audit.await_args.kwargs["after"]["min_side"] == 800
    session.commit.assert_awaited_once()
    session.commit.side_effect = RuntimeError("commit unavailable")
    with pytest.raises(RuntimeError):
        await update_photo_gate(policy, admin, service, Response())
    session.rollback.assert_awaited_once()


def test_photo_gate_uses_configured_resolution_and_reports_effective_threshold():
    from app.schemas.core import PhotoGatePolicy
    from app.services.storage import PhotoRejectedError, validate_report_photo

    with pytest.raises(PhotoRejectedError) as rejected:
        validate_report_photo(_jpeg(), policy=PhotoGatePolicy(min_side=4096))
    assert rejected.value.result["thresholds"]["min_side"] == 4096
    assert "resolution" in rejected.value.result["reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "operational_models",
        "operational_audit",
        "read_photo_gate",
        "operational_reports",
        "operational_ground_truth",
    ],
)
async def test_operational_reads_deny_customer_before_repository_access(endpoint):
    from fastapi import HTTPException, Response

    from app.api.v1 import core
    from app.auth import AuthenticatedUser

    with pytest.raises(HTTPException) as denied:
        await getattr(core, endpoint)(
            user=AuthenticatedUser("customer", None, "authenticated"),
            service=SimpleNamespace(),
            response=Response(),
        )
    assert denied.value.status_code == 403


def _token(key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "0b0e4c7e-1111-4222-8333-944445555666",
        "aud": AUDIENCE,
        "iss": f"{BASE}/auth/v1",
        "iat": now,
        "exp": now + 600,
        "role": "authenticated",
        "email": "pessoa@example.invalid",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="ES256")


def test_token_valido_e_aceito() -> None:
    claims = decode_token(_token(), SETTINGS, key=KEY.public_key())
    assert claims["sub"].startswith("0b0e4c7e")


def test_visitante_anonimo_nunca_recebe_papel_de_revisor() -> None:
    from app.auth import AuthenticatedUser

    visitor = AuthenticatedUser(
        id="visitor", email=None, role="authenticated", urmind_role="admin", is_anonymous=True
    )
    assert visitor.can_review is False


def test_public_copy_strips_metadata_without_changing_pixel_geometry() -> None:
    from app.services.storage import sanitize_public_image

    original = io.BytesIO()
    exif = Image.Exif()
    exif[271] = "private-device"
    exif[305] = "private-software"
    exif[274] = 6
    Image.new("RGB", (32, 16), "red").save(original, format="JPEG", exif=exif)
    raw = original.getvalue()
    sanitized = sanitize_public_image(raw)
    assert sanitized.sha256 != validate_image(raw).sha256
    with Image.open(io.BytesIO(sanitized.data)) as result:
        assert result.size == (32, 16)  # existing bbox coordinates remain applicable
        assert not result.getexif()
        assert "exif" not in result.info and "icc_profile" not in result.info
    assert b"private-device" not in sanitized.data
    assert b"private-software" not in sanitized.data


def test_upload_attempt_budget_includes_rejected_attempts_and_expires() -> None:
    from app.services.storage import UploadAdmission, UploadBusyError

    gate = UploadAdmission(per_user=2, global_limit=3, window_seconds=60)
    gate.admit("a", now=0)
    gate.admit("a", now=1)
    with pytest.raises(UploadBusyError):
        gate.admit("a", now=2)
    gate.admit("b", now=2)
    with pytest.raises(UploadBusyError):
        gate.admit("c", now=3)
    gate.admit("a", now=61)


@pytest.mark.asyncio
async def test_cancelled_request_keeps_decoder_slot_until_thread_finishes(monkeypatch):
    import asyncio
    import threading

    from fastapi import HTTPException

    from app.api.v1 import core

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(core, "_decode_slots", slots)
    started, finish = threading.Event(), threading.Event()

    def slow_decode(data):
        started.set()
        assert finish.wait(5)
        return validate_image(data)

    task = asyncio.create_task(core._decode_upload(_jpeg(), slow_decode))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(HTTPException) as error:
            await core._decode_upload(_jpeg())
        assert error.value.status_code == 429
    finally:
        finish.set()
        assert await asyncio.to_thread(slots.acquire, True, 2)
        slots.release()


@pytest.mark.asyncio
async def test_me_admin_capability_excludes_anonymous_users() -> None:
    from app.api.v1.core import me
    from app.auth import AuthenticatedUser

    admin = AuthenticatedUser("admin", None, "authenticated", "admin")
    assert (await me(admin))["can_admin"] is True
    visitor = AuthenticatedUser("visitor", None, "authenticated", "admin", True)
    assert (await me(visitor))["can_admin"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [None, "role", "review", "status", "attestation", "checksum", "commit", "changed_during_io"],
)
async def test_publication_requires_review_and_compensates_db_failure(failure):
    from fastapi import HTTPException

    from app.api.v1.core import publish_event
    from app.auth import AuthenticatedUser
    from app.schemas.core import PublicationRequest
    from app.services.storage import sanitize_public_image

    event_id, capture_id, review_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    raw = _jpeg()
    capture = SimpleNamespace(
        id=capture_id,
        storage_path="private/original.jpg",
        quality={"sha256": validate_image(raw).sha256},
    )
    event = SimpleNamespace(
        id=event_id, capture_id=capture_id, status="confirmed", factors={"original": True}
    )
    review = SimpleNamespace(id=review_id, reviewer="reviewer", decision="confirm")
    session = SimpleNamespace(refresh=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    service = SimpleNamespace(
        captures=SimpleNamespace(get=AsyncMock(return_value=capture), session=session),
        events=SimpleNamespace(get_for_review=AsyncMock(return_value=event)),
        decisions=SimpleNamespace(reviews=AsyncMock(return_value=[review]), add_audit=AsyncMock()),
    )
    storage = SimpleNamespace(
        download=AsyncMock(return_value=raw),
        upload=AsyncMock(return_value=True),
        delete=AsyncMock(),
    )

    async def download_without_transaction(path):
        assert session.rollback.await_count == 1, "Storage I/O must not retain database locks"
        return raw

    storage.download.side_effect = download_without_transaction
    if failure == "changed_during_io":

        async def change_review_during_upload(path, image):
            event.status = "rejected"
            return True

        storage.upload.side_effect = change_review_during_upload
    user = AuthenticatedUser(
        "reviewer", None, "authenticated", "reviewer" if failure != "role" else None
    )
    if failure == "review":
        review.reviewer = "other"
    if failure == "status":
        event.status = "detected"
    if failure == "checksum":
        capture.quality["sha256"] = "0" * 64
    if failure == "commit":
        session.commit.side_effect = RuntimeError("simulated-db-failure")
    payload = PublicationRequest(
        publish=True,
        review_id=review_id,
        visible_content_reviewed=failure != "attestation",
        reason="fixture privacy review",
    )
    if failure:
        with pytest.raises(RuntimeError if failure == "commit" else HTTPException):
            await publish_event(event_id, payload, user, service, storage)
        if failure in {"commit", "changed_during_io"}:
            storage.delete.assert_awaited_once()
            assert session.rollback.await_count == 2
        else:
            storage.upload.assert_not_awaited()
    else:
        result = await publish_event(event_id, payload, user, service, storage)
        assert result["publication_status"] == "published"
        assert event.factors["original"] is True
        assert capture.storage_path == "private/original.jpg"
        assert (
            event.factors["publication"]["public_image"]["sha256"]
            == sanitize_public_image(raw).sha256
        )
        assert "public_image" not in capture.quality  # one Capture can support several Events
        service.decisions.add_audit.assert_awaited_once()
        session.commit.assert_awaited_once()
        storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_auth_origin_expoe_apenas_url_publica(monkeypatch) -> None:
    from app.api.v1 import public

    monkeypatch.setattr(public, "get_settings", lambda: SETTINGS)
    assert await public.public_auth_origin() == {
        "auth_origin": BASE,
        "visitor_upload_enabled": False,
    }


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"exp": int(time.time()) - 3600}, KEY),
        ({"aud": "anon"}, KEY),
        ({"iss": "https://outro.supabase.co/auth/v1"}, KEY),
        ({"exp": None}, KEY),
        ({}, OTHER_KEY),
    ],
    ids=["expirado", "audience", "issuer", "sem-exp", "assinatura-forjada"],
)
def test_token_invalido_e_recusado(overrides, key) -> None:
    with pytest.raises(jwt.PyJWTError):
        decode_token(_token(key=key, **overrides), SETTINGS, key=KEY.public_key())


def test_algoritmo_none_e_recusado() -> None:
    forged = jwt.encode({"sub": "x", "aud": AUDIENCE}, key=None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        decode_token(forged, SETTINGS, key=KEY.public_key())


def test_rotas_de_dominio_exigem_autenticacao(client) -> None:
    assert client.get("/api/v1/health").status_code == 200
    for method, path in [
        ("get", "/api/v1/events"),
        ("get", "/api/v1/events/nearby?latitude=0&longitude=0"),
        ("post", "/api/v1/captures/photo"),
        # Métricas de operação não são públicas: sem token, nem chegam ao banco.
        ("get", "/api/v1/ops/metrics"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path
    bad = client.get("/api/v1/events", headers={"Authorization": "Bearer nao.e.jwt"})
    assert bad.status_code in (401, 503)


def test_rotas_que_bypassavam_worker_nao_estao_expostas(client) -> None:
    from app.api.v1.core import router

    registered = {(route.path, method) for route in router.routes for method in route.methods}
    for path in (
        "/api/v1/captures",
        "/api/v1/events",
        "/api/v1/captures/{capture_id}/consolidate",
    ):
        assert (path, "POST") not in registered


@pytest.mark.asyncio
async def test_limite_asgi_recusa_upload_chunked_antes_do_parser() -> None:
    from app.main import MAX_CAPTURE_MULTIPART_BYTES, CaptureBodyLimitMiddleware

    called = False

    async def downstream(_scope, receive, _send):
        nonlocal called
        called = True
        while True:
            message = await receive()
            if not message.get("more_body"):
                return

    chunks = iter(
        [
            {
                "type": "http.request",
                "body": b"x" * (MAX_CAPTURE_MULTIPART_BYTES // 2),
                "more_body": True,
            },
            {
                "type": "http.request",
                "body": b"x" * (MAX_CAPTURE_MULTIPART_BYTES // 2 + 1),
                "more_body": False,
            },
        ]
    )
    sent = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = CaptureBodyLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "path": "/api/v1/captures/photo",
            "headers": [(b"authorization", b"Bearer fixture")],
        },
        receive,
        send,
    )
    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_limite_asgi_recusa_content_length_antes_de_ler_corpo() -> None:
    from app.main import MAX_CAPTURE_MULTIPART_BYTES, CaptureBodyLimitMiddleware

    async def downstream(_scope, _receive, _send):
        raise AssertionError("parser deve ser bloqueado")

    async def receive():
        raise AssertionError("body nao deve ser lido")

    sent = []

    async def send(message):
        sent.append(message)

    middleware = CaptureBodyLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "path": "/api/v1/captures/photo",
            "headers": [
                (b"content-length", str(MAX_CAPTURE_MULTIPART_BYTES + 1).encode()),
                (b"authorization", b"Bearer fixture"),
            ],
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_limite_upload_na_app_real_sem_content_length() -> None:
    from app.main import MAX_CAPTURE_MULTIPART_BYTES, app

    async def body():
        yield (
            b'--fixture\r\nContent-Disposition: form-data; name="file"; '
            b'filename="fixture.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
        )
        yield b"x" * MAX_CAPTURE_MULTIPART_BYTES
        yield b"\r\n--fixture--\r\n"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/captures/photo",
            content=body(),
            headers={
                "Content-Type": "multipart/form-data; boundary=fixture",
                "Authorization": "Bearer fixture",
            },
        )
    assert response.status_code == 413, response.text


@pytest.mark.parametrize("location", ["manual", "gps_device", "missing"])
def test_upload_multipart_normal_atravessa_limite_asgi(client, location) -> None:
    from app.api.v1.core import get_core_service, get_storage
    from app.auth import AuthenticatedUser, require_user
    from app.main import app

    capture_id = uuid.uuid4()
    captures = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None),
        session=SimpleNamespace(commit=AsyncMock()),
    )
    service = SimpleNamespace(
        captures=captures,
        register_capture=AsyncMock(return_value={"id": capture_id, "created": True}),
    )
    storage = SimpleNamespace(upload=AsyncMock(return_value=True), delete=AsyncMock())
    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        id="fixture-reviewer", email=None, role="authenticated", urmind_role="reviewer"
    )
    app.dependency_overrides[get_core_service] = lambda: service
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        response = client.post(
            "/api/v1/captures/photo",
            files={"file": ("fixture.jpg", _jpeg(), "image/jpeg")},
            data=(
                {
                    "latitude": "-23.55",
                    "longitude": "-46.63",
                    "location_source": location,
                }
                if location != "missing"
                else {}
            )
            | {"user_description": " <script>plain</script>\x00 "},
            headers={"Authorization": "Bearer fixture"},
        )
    finally:
        for dependency in (require_user, get_core_service, get_storage):
            app.dependency_overrides.pop(dependency, None)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == str(capture_id)
    storage.upload.assert_awaited_once()
    assert service.register_capture.await_args.args[0].user_description == "<script>plain</script>"
    assert response.json()["requires_manual_location"] is (location == "missing")
    assert response.json()["location_source"] == ("unknown" if location == "missing" else location)


@pytest.mark.asyncio
async def test_capture_manual_location_is_owner_only_and_never_overwrites():
    from fastapi import HTTPException

    from app.api.v1.core import capture_location
    from app.auth import AuthenticatedUser
    from app.schemas.core import Coordinate

    repository = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(quality={"uploaded_by": "A"})),
        fill_missing_location=AsyncMock(return_value=False),
    )
    service = SimpleNamespace(captures=repository)
    with pytest.raises(HTTPException) as denied:
        await capture_location(
            uuid.uuid4(),
            Coordinate(latitude=-23, longitude=-46),
            AuthenticatedUser("B", None, "authenticated"),
            service,
        )
    assert denied.value.status_code == 404
    repository.fill_missing_location.assert_not_awaited()
    with pytest.raises(HTTPException) as conflict:
        await capture_location(
            uuid.uuid4(),
            Coordinate(latitude=-23, longitude=-46),
            AuthenticatedUser("A", None, "authenticated"),
            service,
        )
    assert conflict.value.status_code == 409


def test_upload_description_over_limit_is_422(client):
    from app.api.v1.core import get_core_service, get_storage
    from app.auth import AuthenticatedUser, require_user
    from app.main import app

    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        "test", None, "authenticated"
    )
    app.dependency_overrides[get_core_service] = lambda: None
    app.dependency_overrides[get_storage] = lambda: None
    try:
        response = client.post(
            "/api/v1/captures/photo",
            headers={"Authorization": "Bearer fixture"},
            files={"file": ("test.jpg", _jpeg(), "image/jpeg")},
            data={"user_description": "x" * 501},
        )
        assert response.status_code == 422
    finally:
        for dependency in (require_user, get_core_service, get_storage):
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.asyncio
async def test_upload_comum_rejeita_origem_gps_scout_antes_de_storage() -> None:
    from fastapi import HTTPException, UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.schemas.core import CaptureSource, LocationSource

    with pytest.raises(HTTPException) as error:
        await upload_photo(
            user=AuthenticatedUser(id="test-user", email=None, role="authenticated"),
            service=None,
            storage=None,
            file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
            source=CaptureSource.PWA_PHOTO,
            latitude=None,
            longitude=None,
            accuracy_m=None,
            location_source=LocationSource.GPS_SCOUT,
            tz_offset_minutes=None,
        )
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_upload_anonimo_falha_antes_de_storage_e_fila() -> None:
    from fastapi import HTTPException, UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser

    with pytest.raises(HTTPException) as error:
        await upload_photo(
            user=AuthenticatedUser(
                id="visitor", email=None, role="authenticated", is_anonymous=True
            ),
            service=None,
            storage=None,
            file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
        )
    assert error.value.status_code == 503


def _dev_visitor_settings() -> Settings:
    ref = "impmeitwtusjtwjouggy"
    return Settings.model_validate(
        {
            "SUPABASE_URL": f"https://{ref}.supabase.co",
            "DATABASE_POOLER_URL": (
                f"postgresql+psycopg://postgres.{ref}:fixture@pooler.invalid:5432/postgres"
            ),
            "SUPABASE_SECRET_KEY": "fixture-secret-not-real",
            "SUPABASE_PUBLISHABLE_KEY": "fixture-public-not-real",
        }
    )


@pytest.mark.asyncio
async def test_public_auth_origin_habilita_visitante_somente_no_dev(monkeypatch) -> None:
    from app.api.v1 import public

    monkeypatch.setattr(public, "get_settings", _dev_visitor_settings)
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"external": {"anonymous_users": True}})
    )
    monkeypatch.setattr(
        public.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    result = await public.public_auth_origin()
    assert result == {
        "auth_origin": "https://impmeitwtusjtwjouggy.supabase.co",
        "visitor_upload_enabled": True,
    }


@pytest.mark.asyncio
async def test_public_auth_origin_nao_anuncia_visitante_se_auth_desabilitado(monkeypatch) -> None:
    from app.api.v1 import public

    monkeypatch.setattr(public, "get_settings", _dev_visitor_settings)
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"external": {"anonymous_users": False}})
    )
    monkeypatch.setattr(
        public.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    assert (await public.public_auth_origin())["visitor_upload_enabled"] is False


def test_visitor_upload_falha_fechado_fora_do_dev() -> None:
    assert _dev_visitor_settings().visitor_upload_enabled is True
    assert (
        _dev_visitor_settings().model_copy(update={"app_env": "production"}).visitor_upload_enabled
        is False
    )
    assert SETTINGS.visitor_upload_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("anonymous", [True, False])
async def test_usuario_publico_dev_pode_criar_captura_com_quota_atomica(
    monkeypatch, anonymous: bool
) -> None:
    from fastapi import UploadFile

    from app.api.v1 import core
    from app.auth import AuthenticatedUser
    from app.schemas.core import LocationSource

    monkeypatch.setattr(core, "get_settings", _dev_visitor_settings)
    capture_id = uuid.uuid4()
    captures = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None),
        recent_public_uploads=AsyncMock(return_value=0),
        recent_owner_uploads=AsyncMock(return_value=0),
        lock_public_uploads=AsyncMock(),
        lock_owner_uploads=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    service = SimpleNamespace(
        captures=captures,
        register_capture=AsyncMock(return_value={"id": capture_id, "created": True}),
    )
    storage = SimpleNamespace(upload=AsyncMock(return_value=True), delete=AsyncMock())
    result = await core.upload_photo(
        user=AuthenticatedUser(
            id="visitor-1", email=None, role="authenticated", is_anonymous=anonymous
        ),
        service=service,
        storage=storage,
        file=UploadFile(filename="photo.jpg", file=io.BytesIO(_jpeg())),
        latitude=-23.55,
        longitude=-46.63,
        location_source=LocationSource.MANUAL,
    )
    assert result["id"] == capture_id
    assert service.register_capture.await_args.args[0].quality["public_upload"] is True
    assert captures.recent_owner_uploads.await_count == 2
    assert captures.recent_public_uploads.await_count == 2
    captures.lock_public_uploads.assert_awaited_once()
    captures.lock_owner_uploads.assert_awaited_once_with("visitor-1")
    captures.session.commit.assert_awaited_once()
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_limite_concorrente_compensa_objeto_storage(monkeypatch) -> None:
    from fastapi import HTTPException, UploadFile

    from app.api.v1 import core
    from app.auth import AuthenticatedUser
    from app.schemas.core import LocationSource

    monkeypatch.setattr(core, "get_settings", _dev_visitor_settings)
    captures = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None),
        recent_public_uploads=AsyncMock(return_value=0),
        recent_owner_uploads=AsyncMock(side_effect=[0, 4]),
        lock_public_uploads=AsyncMock(),
        lock_owner_uploads=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    service = SimpleNamespace(captures=captures, register_capture=AsyncMock())
    storage = SimpleNamespace(upload=AsyncMock(return_value=True), delete=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await core.upload_photo(
            user=AuthenticatedUser(
                id="visitor-1", email=None, role="authenticated", is_anonymous=True
            ),
            service=service,
            storage=storage,
            file=UploadFile(filename="photo.jpg", file=io.BytesIO(_jpeg())),
            latitude=-23.55,
            longitude=-46.63,
            location_source=LocationSource.MANUAL,
        )
    assert exc.value.status_code == 429
    service.register_capture.assert_not_awaited()
    storage.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_limite_global_bloqueia_nova_identidade_antes_de_storage(monkeypatch) -> None:
    from fastapi import HTTPException, UploadFile

    from app.api.v1 import core
    from app.auth import AuthenticatedUser
    from app.schemas.core import LocationSource

    monkeypatch.setattr(core, "get_settings", _dev_visitor_settings)
    captures = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None),
        recent_public_uploads=AsyncMock(return_value=60),
        recent_owner_uploads=AsyncMock(),
    )
    storage = SimpleNamespace(upload=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await core.upload_photo(
            user=AuthenticatedUser(
                id="new-visitor", email=None, role="authenticated", is_anonymous=True
            ),
            service=SimpleNamespace(captures=captures),
            storage=storage,
            file=UploadFile(filename="photo.jpg", file=io.BytesIO(_jpeg())),
            latitude=-23.55,
            longitude=-46.63,
            location_source=LocationSource.MANUAL,
        )
    assert exc.value.status_code == 429
    captures.recent_owner_uploads.assert_not_awaited()
    storage.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_pwa_preserva_claims_gps_sem_atestar_precisao() -> None:
    from fastapi import UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.schemas.core import CaptureSource, LocationSource

    capture_id = uuid.uuid4()
    captures = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None),
        session=SimpleNamespace(commit=AsyncMock()),
    )
    service = SimpleNamespace(
        captures=captures,
        register_capture=AsyncMock(
            return_value={"id": capture_id, "capture_key": "claim", "created": True}
        ),
    )
    storage = SimpleNamespace(upload=AsyncMock(return_value=True))
    recorded_at = datetime.now(UTC) - timedelta(minutes=1)

    response = await upload_photo(
        user=AuthenticatedUser(
            id="owner", email=None, role="authenticated", urmind_role="reviewer"
        ),
        service=service,
        storage=storage,
        file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
        source=CaptureSource.PWA_PHOTO,
        latitude=-23.55,
        longitude=-46.63,
        accuracy_m=1,
        location_source=LocationSource.GPS_DEVICE,
        captured_at=recorded_at,
        location_timestamp=recorded_at,
        heading_deg=90,
        speed_mps=2,
        tz_offset_minutes=None,
    )
    persisted = service.register_capture.await_args.args[0]
    assert response["id"] == capture_id
    assert "storage_path" not in response
    assert persisted.coordinate.accuracy_m == 1
    assert persisted.heading_deg == 90
    assert persisted.speed_mps == 2
    assert persisted.quality["location_timestamp"] == recorded_at.isoformat()
    assert persisted.quality["location_attestation"] == "unverified_client_claim"
    assert persisted.quality["public_upload"] is False
    assert persisted.captured_at != recorded_at


@pytest.mark.asyncio
async def test_upload_sem_gps_nem_exif_armazena_e_pede_local_manual():
    from fastapi import UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.schemas.core import CaptureSource, LocationSource

    service = SimpleNamespace(
        captures=SimpleNamespace(
            get_by_key=AsyncMock(return_value=None), session=SimpleNamespace(commit=AsyncMock())
        ),
        register_capture=AsyncMock(return_value={"id": uuid.uuid4(), "created": True}),
    )
    storage = SimpleNamespace(upload=AsyncMock(return_value=True), delete=AsyncMock())
    result = await upload_photo(
        user=AuthenticatedUser(
            id="owner", email=None, role="authenticated", urmind_role="reviewer"
        ),
        service=service,
        storage=storage,
        file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
        source=CaptureSource.PWA_PHOTO,
        latitude=None,
        longitude=None,
        accuracy_m=None,
        location_source=LocationSource.GPS_DEVICE,
        tz_offset_minutes=None,
    )
    assert result["requires_manual_location"] is True
    storage.upload.assert_awaited_once()
    assert service.register_capture.await_args.args[0].coordinate is None


@pytest.mark.asyncio
async def test_same_photo_from_two_users_does_not_share_capture_or_storage_path():
    from fastapi import UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.schemas.core import CaptureSource, LocationSource

    stored = {}

    class Captures:
        session = SimpleNamespace(commit=AsyncMock())

        async def get_by_key(self, key):
            return stored.get(key)

    class Service:
        captures = Captures()

        async def register_capture(self, payload):
            row = SimpleNamespace(
                id=uuid.uuid4(),
                capture_key=payload.capture_key,
                storage_path=payload.storage_path,
                quality=payload.quality,
                point=object(),
            )
            stored[payload.capture_key] = row
            return {"id": row.id, "capture_key": row.capture_key, "created": True}

    class Storage:
        async def upload(self, _path, _image):
            return True

    async def send(actor):
        return await upload_photo(
            user=AuthenticatedUser(
                id=actor, email=None, role="authenticated", urmind_role="reviewer"
            ),
            service=Service(),
            storage=Storage(),
            file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
            source=CaptureSource.PWA_PHOTO,
            latitude=-23.55,
            longitude=-46.63,
            accuracy_m=8,
            location_source=LocationSource.MANUAL,
            tz_offset_minutes=None,
        )

    first = await send("owner-a")
    second = await send("owner-b")
    assert first["id"] != second["id"]
    assert first["capture_key"] != second["capture_key"]
    assert stored[first["capture_key"]].storage_path != stored[second["capture_key"]].storage_path
    assert "storage_path" not in first and "storage_path" not in second


@pytest.mark.asyncio
async def test_usuario_comum_nao_le_eventos_internos_ou_url_privada() -> None:
    from fastapi import HTTPException

    from app.api.v1.core import event_detail, events_nearby, list_events
    from app.auth import AuthenticatedUser

    user = AuthenticatedUser(id="test-user", email=None, role="authenticated")
    calls = (
        list_events(service=None, user=user),
        events_nearby(service=None, user=user, latitude=0, longitude=0, radius_m=500, limit=10),
        event_detail(event_id=uuid.uuid4(), service=None, storage=None, user=user),
    )
    for call in calls:
        with pytest.raises(HTTPException) as error:
            await call
        assert error.value.status_code == 403


def test_detection_exige_lineage_de_model_version() -> None:
    from pydantic import ValidationError

    from app.schemas.core import DetectionCreate, UrmindClass

    with pytest.raises(ValidationError, match="model_version_id"):
        DetectionCreate(
            urmind_class=UrmindClass.ROAD_D40,
            confidence=0.9,
            bbox={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        )


def _jpeg(size=(640, 640)) -> bytes:
    buffer = io.BytesIO()
    y, x = np.indices((size[1], size[0]))
    Image.fromarray(((x * 73 + y * 151) % 256).astype("uint8")).convert("RGB").save(
        buffer, format="JPEG"
    )
    return buffer.getvalue()


def test_imagem_valida_recebe_hash_mime_e_dimensoes() -> None:
    image = validate_image(_jpeg((32, 24)))
    assert (image.mime, image.extension, image.width, image.height) == ("image/jpeg", "jpg", 32, 24)
    assert len(image.sha256) == 64


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a" + b"\x00" * 20, b"\xff\xd8\xff\xe0 not really a jpeg", b"x" * (MAX_BYTES + 1)],
    ids=["vazio", "gif-falso", "jpeg-corrompido", "grande"],
)
def test_conteudo_invalido_e_recusado(data: bytes) -> None:
    with pytest.raises(InvalidImageError):
        validate_image(data)


def test_jpeg_truncado_e_recusado() -> None:
    with pytest.raises(InvalidImageError):
        validate_image(_jpeg((256, 256))[:200])


def test_png_pequeno_com_dimensoes_declaradas_gigantes_e_recusado() -> None:
    import struct
    import zlib

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    data = bytearray(buffer.getvalue())
    data[16:24] = struct.pack(">II", 10_000, 10_000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    with pytest.raises(InvalidImageError):
        validate_image(bytes(data))


def test_path_e_gerado_pelo_sistema_sem_nome_do_cliente() -> None:
    from datetime import UTC, datetime

    image = validate_image(_jpeg())
    path = object_path("../../etc/passwd", image, datetime(2026, 9, 16, tzinfo=UTC))
    assert path == f"etcpasswd/2026/09/{image.sha256}.jpg"
    assert ".." not in path


def test_upload_id_isolates_storage_compensation() -> None:
    from datetime import UTC, datetime

    image = validate_image(_jpeg())
    moment = datetime(2026, 9, 16, tzinfo=UTC)
    first = object_path("reviewer", image, moment, upload_id="a" * 32)
    second = object_path("reviewer", image, moment, upload_id="b" * 32)
    assert first != second
    assert image.sha256 in first and image.sha256 in second


@pytest.mark.asyncio
async def test_storage_object_removed_when_capture_db_fails() -> None:
    from fastapi import UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.schemas.core import CaptureSource, LocationSource

    class Session:
        rolled_back = False

        async def rollback(self) -> None:
            self.rolled_back = True

    class Captures:
        session = Session()

        async def get_by_key(self, _key):
            return None

    class Service:
        captures = Captures()

        async def register_capture(self, _capture):
            raise RuntimeError("controlled DB failure")

    class Storage:
        uploaded = None
        deleted = None

        async def upload(self, path, _image):
            self.uploaded = path
            return True

        async def delete(self, path):
            self.deleted = path

    service = Service()
    storage = Storage()
    with pytest.raises(RuntimeError, match="controlled DB failure"):
        await upload_photo(
            user=AuthenticatedUser(
                id="test-user", email=None, role="authenticated", urmind_role="reviewer"
            ),
            service=service,
            storage=storage,
            file=UploadFile(filename="fixture.jpg", file=io.BytesIO(_jpeg())),
            source=CaptureSource.PWA_PHOTO,
            latitude=-23.55,
            longitude=-46.63,
            accuracy_m=None,
            location_source=LocationSource.MANUAL,
            tz_offset_minutes=None,
        )
    assert service.captures.session.rolled_back
    assert storage.deleted == storage.uploaded


@pytest.mark.asyncio
async def test_upload_repetido_nao_e_erro() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"Key": "captures/x"})
        return httpx.Response(400, json={"statusCode": "409", "error": "Duplicate"})

    storage = StorageClient(SETTINGS, transport=httpx.MockTransport(handler))
    image = validate_image(_jpeg())
    assert await storage.upload("u/2026/09/a.jpg", image) is True
    assert await storage.upload("u/2026/09/a.jpg", image) is False
    assert calls[0].headers["x-upsert"] == "false"
    assert calls[0].url.path == "/storage/v1/object/captures/u/2026/09/a.jpg"


def test_papel_de_revisor_vem_de_app_metadata() -> None:
    from app.auth import AuthenticatedUser

    assert AuthenticatedUser(
        id="u", email=None, role="authenticated", urmind_role="reviewer"
    ).can_review
    assert not AuthenticatedUser(id="u", email=None, role="authenticated").can_review
    assert not AuthenticatedUser(
        id="u", email=None, role="authenticated", urmind_role="viewer"
    ).can_review


def test_registro_de_modelo_recusa_export_sem_metrica(tmp_path) -> None:
    import asyncio
    import json

    from app.ml.serving import register_model

    manifest = tmp_path / "export.json"
    manifest.write_text(json.dumps({"validation_metrics": None, "parity": {"passed": True}}))
    with pytest.raises(ValueError, match="VALIDATION"):
        asyncio.run(register_model(manifest, promote=True))


def test_gate_de_promocao_exige_fechamento_completo_do_modelo() -> None:
    from app.ml.serving import validate_registration_manifest

    record = {
        "stage": "baseline_early",
        "checkpoint_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "validation_metrics": {"map50_95": 0.1},
        "parity": {"passed": True},
        "training": {"completed_epochs": 30, "contract_max_epoch": 300},
    }

    with pytest.raises(ValueError, match="TRAINING_NOT_COMPLETE"):
        validate_registration_manifest(record, promote=True)


def test_gate_de_promocao_exige_selecao_calibracao_e_test() -> None:
    from app.ml.serving import validate_registration_manifest

    record = {
        "stage": "final",
        "checkpoint": "best",
        "checkpoint_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "validation_metrics": {"map50_95": 0.1},
        "parity": {"passed": True},
        "training": {"completed_epochs": 300, "contract_max_epoch": 300},
    }

    with pytest.raises(ValueError, match="PROMOTION_GATE_FAIL"):
        validate_registration_manifest(record, promote=True)


def test_gate_de_promocao_recusa_json_legado_sem_closure_canonico() -> None:
    from app.ml.serving import validate_registration_manifest

    checkpoint_hash = "a" * 64
    per_class = {label: {"ap50_95": 0.1} for label in ("D00", "D10", "D20", "D40")}
    record = {
        "stage": "final",
        "checkpoint": "best",
        "checkpoint_sha256": checkpoint_hash,
        "onnx_sha256": "b" * 64,
        "model_contract_sha256": "c" * 64,
        "config_fingerprint": "d" * 64,
        "dataset_fingerprint": "e" * 64,
        "split_fingerprint": "f" * 64,
        "class_mapping_fingerprint": "1" * 64,
        "onnx_path": "models/serving/model.onnx",
        "opset": 17,
        "input_size": [640, 640],
        "class_names": [
            "URMIND_ROAD_D00",
            "URMIND_ROAD_D10",
            "URMIND_ROAD_D20",
            "URMIND_ROAD_D40",
        ],
        "validation_metrics": {"map50_95": 0.1, "per_class": per_class},
        "final_test_metrics": {"map50_95": 0.09, "per_class": per_class},
        "parity": {"passed": True},
        "training": {
            "completed_epochs": 300,
            "contract_max_epoch": 300,
            "run": {
                "mlflow_run_id": "run-1",
                "status": "FINISHED",
                "latest_train_epoch": 299.0,
            },
        },
        "checkpoint_selection": {
            "source": "VALIDATION",
            "primary_metric": "map50_95",
            "checkpoint_sha256": checkpoint_hash,
            "value": 0.1,
            "epoch": 29,
            "global_step": 89370,
        },
        "operating_point": {
            "source": "VALIDATION",
            "confidence_threshold": 0.2,
            "nms_threshold": 0.65,
        },
        "serving_score_threshold": 0.2,
    }

    with pytest.raises(ValueError, match="TRAINING_CONTRACT_MISMATCH|OPERATING_POINT_LOCK_INVALID"):
        validate_registration_manifest(record, promote=True)


def test_stage_final_depende_do_run_completo_nao_da_epoca_do_best() -> None:
    from app.ml.serving import training_contract_complete

    finished = {"status": "FINISHED", "latest_train_epoch": 299.0}
    running = {"status": "RUNNING", "latest_train_epoch": 299.0}

    assert training_contract_complete(finished, max_epoch=300)
    assert not training_contract_complete(running, max_epoch=300)


@pytest.mark.asyncio
async def test_storage_delete_uses_idempotent_exact_object_list():
    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/storage/v1/object/captures"
        assert json.loads(request.content) == {"prefixes": ["fixture/exact.jpg"]}
        return httpx.Response(200, json=[])

    storage = StorageClient(SETTINGS, transport=httpx.MockTransport(handler))
    await storage.delete("fixture/exact.jpg")
    await storage.delete("fixture/exact.jpg")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("small", "resolution"),
        ("dark", "underexposed"),
        ("bright", "overexposed"),
        ("blur", "blur"),
    ],
)
def test_report_photo_gate_rejects_technically_unusable_image(kind, expected):
    from app.services.storage import PhotoRejectedError, validate_report_photo

    size = (32, 24) if kind == "small" else (640, 640)
    color = {"dark": 0, "bright": 255}.get(kind, 120)
    buffer = io.BytesIO()
    Image.new("RGB", size, (color, color, color)).save(buffer, format="JPEG")
    with pytest.raises(PhotoRejectedError) as error:
        validate_report_photo(buffer.getvalue())
    assert error.value.result["status"] == "REJECTED"
    assert expected in error.value.result["reasons"]
    assert "data" not in error.value.result


def test_report_photo_gate_accepts_texture_but_never_claims_urban_scene_or_problem():
    from app.services.storage import validate_report_photo

    buffer = io.BytesIO()
    Image.effect_noise((640, 640), 30).convert("RGB").save(buffer, format="JPEG")
    image = validate_report_photo(buffer.getvalue())
    assert image.photo_quality["status"] == "NEEDS_REVIEW"
    assert image.photo_quality["technical_status"] == "ACCEPTED"
    assert image.photo_quality["scene_status"] == "NOT_VERIFIED"
    assert image.photo_quality["face_status"] == "NOT_VERIFIED"


@pytest.mark.asyncio
async def test_photo_rejection_is_audited_without_upload_or_capture():
    from fastapi import HTTPException, UploadFile

    from app.api.v1.core import upload_photo
    from app.auth import AuthenticatedUser
    from app.services.core import CoreService

    storage = SimpleNamespace(upload=AsyncMock())
    captures = SimpleNamespace(session=SimpleNamespace(commit=AsyncMock()), create=AsyncMock())
    decisions = SimpleNamespace(add_audit=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await upload_photo(
            user=AuthenticatedUser("owner", None, "authenticated"),
            service=CoreService(captures, SimpleNamespace(), decisions),
            storage=storage,
            file=UploadFile(filename="small.jpg", file=io.BytesIO(_jpeg((32, 24)))),
        )
    assert error.value.status_code == 422
    storage.upload.assert_not_awaited()
    captures.create.assert_not_awaited()
    audit = decisions.add_audit.await_args.kwargs
    assert audit["operation"] == "photo_gate_rejected"
    assert audit["after"]["reasons"] == ["resolution"]
    assert "sha256" not in audit["after"]
    captures.session.commit.assert_awaited_once()
