"""Evidência fotográfica no Supabase Storage (MASTER_PLAN §4.4, §6.1, §17).

Bucket privado. O caminho é gerado aqui a partir do hash do conteúdo — o nome de
arquivo do usuário nunca entra no path. A secret key só existe neste processo.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import Settings

BUCKET = "captures"
MAX_BYTES = 10 * 1024 * 1024
# Formato decodificado pelo Pillow → MIME e extensão aceitos. O Content-Type
# declarado pelo cliente não é prova de nada: vale o que o conteúdo decodifica.
ALLOWED_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


class InvalidImageError(ValueError):
    """Conteúdo recusado: vazio, grande demais, formato não aceito ou corrompido."""


class StorageNotConfiguredError(RuntimeError):
    pass


class StorageError(RuntimeError):
    """Falha do Storage. A mensagem nunca carrega chave nem URL assinada."""


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    sha256: str
    mime: str
    extension: str
    width: int
    height: int


def validate_image(data: bytes) -> ValidatedImage:
    if not data:
        raise InvalidImageError("arquivo vazio")
    if len(data) > MAX_BYTES:
        raise InvalidImageError(f"arquivo acima de {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            image_format = probe.format
            probe.verify()
        # verify() invalida o objeto; reabrir e decodificar pega truncamento real.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImageError("conteúdo não é uma imagem válida") from exc
    if image_format not in ALLOWED_FORMATS:
        raise InvalidImageError("formato não aceito; use JPEG, PNG ou WebP")
    mime, extension = ALLOWED_FORMATS[image_format]
    return ValidatedImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=mime,
        extension=extension,
        width=width,
        height=height,
    )


def object_path(
    user_id: str, image: ValidatedImage, received_at: datetime, *, upload_id: str | None = None
) -> str:
    """`<usuário>/<AAAA>/<MM>/<sha256>.<ext>`: determinístico e sem nome do cliente."""
    safe_user = "".join(ch for ch in user_id if ch.isalnum() or ch == "-")
    if not safe_user:
        raise ValueError("id de usuário inválido para path de Storage")
    if upload_id is not None and (
        len(upload_id) != 32 or any(ch not in "0123456789abcdef" for ch in upload_id)
    ):
        raise ValueError("invalid upload_id")
    suffix = f"-{upload_id}" if upload_id else ""
    return f"{safe_user}/{received_at:%Y}/{received_at:%m}/{image.sha256}{suffix}.{image.extension}"


class StorageClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.supabase_url or not settings.supabase_secret_key:
            raise StorageNotConfiguredError("SUPABASE_URL/SUPABASE_SECRET_KEY ausentes")
        self._base = f"{settings.supabase_url}/storage/v1"
        self._key = settings.supabase_secret_key
        self._transport = transport

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}", **extra}

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            try:
                return await client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                raise StorageError(f"Storage indisponível: {type(exc).__name__}") from None

    async def upload(self, path: str, image: ValidatedImage) -> bool:
        """Envia o objeto. Devolve False quando ele já existia (mesmo hash → mesmo path)."""
        response = await self._request(
            "POST",
            f"{self._base}/object/{BUCKET}/{path}",
            content=image.data,
            headers=self._headers(**{"Content-Type": image.mime, "x-upsert": "false"}),
        )
        if response.status_code in (200, 201):
            return True
        if response.status_code == 409 or (
            response.status_code == 400 and "Duplicate" in response.text
        ):
            return False
        raise StorageError(f"upload recusado pelo Storage (HTTP {response.status_code})")

    async def signed_url(self, path: str, expires_in: int = 600) -> str:
        response = await self._request(
            "POST",
            f"{self._base}/object/sign/{BUCKET}/{path}",
            json={"expiresIn": expires_in},
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise StorageError(f"URL assinada recusada (HTTP {response.status_code})")
        signed = response.json().get("signedURL") or response.json().get("signedUrl")
        if not signed:
            raise StorageError("Storage não devolveu URL assinada")
        return f"{self._base}{signed}" if signed.startswith("/") else signed

    async def download(self, path: str) -> bytes:
        response = await self._request(
            "GET", f"{self._base}/object/authenticated/{BUCKET}/{path}", headers=self._headers()
        )
        if response.status_code != 200:
            raise StorageError(f"download recusado pelo Storage (HTTP {response.status_code})")
        return response.content

    async def delete(self, path: str) -> None:
        """Compensate a failed DB commit for an object unique to this upload."""
        response = await self._request(
            "DELETE", f"{self._base}/object/{BUCKET}/{path}", headers=self._headers()
        )
        if response.status_code not in (200, 204, 404):
            raise StorageError(f"Storage compensation refused (HTTP {response.status_code})")
