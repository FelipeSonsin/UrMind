"""Auth (JWT do Supabase) e validação de upload. Sem rede: chaves e transporte locais."""

from __future__ import annotations

import io
import time

import httpx
import jwt
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
        ("post", "/api/v1/captures"),
        ("post", "/api/v1/captures/photo"),
        ("post", "/api/v1/events"),
        # Métricas de operação não são públicas: sem token, nem chegam ao banco.
        ("get", "/api/v1/ops/metrics"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path
    bad = client.get("/api/v1/events", headers={"Authorization": "Bearer nao.e.jwt"})
    assert bad.status_code in (401, 503)


def _jpeg(size=(32, 24)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 110, 100)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_imagem_valida_recebe_hash_mime_e_dimensoes() -> None:
    image = validate_image(_jpeg())
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


def test_path_e_gerado_pelo_sistema_sem_nome_do_cliente() -> None:
    from datetime import UTC, datetime

    image = validate_image(_jpeg())
    path = object_path("../../etc/passwd", image, datetime(2026, 9, 16, tzinfo=UTC))
    assert path == f"etcpasswd/2026/09/{image.sha256}.jpg"
    assert ".." not in path


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

    assert AuthenticatedUser(id="u", email=None, role="authenticated", urmind_role="reviewer").can_review
    assert not AuthenticatedUser(id="u", email=None, role="authenticated").can_review
    assert not AuthenticatedUser(id="u", email=None, role="authenticated", urmind_role="viewer").can_review


def test_registro_de_modelo_recusa_export_sem_metrica(tmp_path) -> None:
    import asyncio
    import json

    from app.ml.serving import register_model

    manifest = tmp_path / "export.json"
    manifest.write_text(json.dumps({"validation_metrics": None, "parity": {"passed": True}}))
    with pytest.raises(ValueError, match="VALIDATION"):
        asyncio.run(register_model(manifest, promote=True))
