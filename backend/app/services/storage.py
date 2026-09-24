"""Evidência fotográfica no Supabase Storage (MASTER_PLAN §4.4, §6.1, §17).

Bucket privado. O caminho é gerado aqui a partir do hash do conteúdo — o nome de
arquivo do usuário nunca entra no path. A secret key só existe neste processo.
"""

from __future__ import annotations

import hashlib
import io
import time
import warnings
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from app.config import Settings
from app.schemas.core import PhotoGatePolicy
from app.services.exif import read_exif_location
from app.services.photo_reference import (
    ReferenceUnavailable,
    calibrated_policy,
    detect_faces,
    scene_similarity,
)

BUCKET = "captures"
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
# Formato decodificado pelo Pillow → MIME e extensão aceitos. O Content-Type
# declarado pelo cliente não é prova de nada: vale o que o conteúdo decodifica.
ALLOWED_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


class InvalidImageError(ValueError):
    """Conteúdo recusado: vazio, grande demais, formato não aceito ou corrompido."""


class PhotoRejectedError(InvalidImageError):
    def __init__(self, result: dict[str, Any], message: str) -> None:
        super().__init__(message)
        self.result = result


class StorageNotConfiguredError(RuntimeError):
    pass


class StorageError(RuntimeError):
    """Falha do Storage. A mensagem nunca carrega chave nem URL assinada."""


class UploadBusyError(ValueError):
    """Admission limit reached before image decoding (not a scientific threshold)."""


class UploadAdmission:
    """Bound attempts, including invalid/duplicate images, in one API process.

    DEV runs one API process. Persistent cross-process capture quotas remain in
    Postgres. The global deque bounds memory even if identities keep changing.
    """

    def __init__(self, per_user: int = 8, global_limit: int = 30, window_seconds: int = 60):
        self.per_user = per_user
        self.global_limit = global_limit
        self.window_seconds = window_seconds
        self._attempts: deque[tuple[float, str]] = deque()
        self._lock = Lock()

    def admit(self, owner: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._attempts and self._attempts[0][0] <= now - self.window_seconds:
                self._attempts.popleft()
            if (
                len(self._attempts) >= self.global_limit
                or sum(user == owner for _, user in self._attempts) >= self.per_user
            ):
                raise UploadBusyError("Muitas tentativas de upload; tente novamente em um minuto")
            self._attempts.append((now, owner))


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    sha256: str
    mime: str
    extension: str
    width: int
    height: int
    photo_quality: dict[str, Any] | None = None
    phash: str | None = None


def perceptual_hash(source: Image.Image) -> str:
    """64-bit low-frequency DCT hash; presentation orientation, no model/embedding."""
    pixels = np.asarray(
        ImageOps.exif_transpose(source).convert("L").resize((32, 32), Image.Resampling.LANCZOS),
        dtype=np.float64,
    )
    basis = np.cos(np.pi * np.arange(8)[:, None] * (2 * np.arange(32) + 1) / 64)
    low = (basis @ pixels @ basis.T).ravel()
    bits = low > np.median(low[1:])
    bits[0] = False
    return f"{int.from_bytes(np.packbits(bits).tobytes(), 'big'):016x}"


def validate_image(data: bytes) -> ValidatedImage:
    if not data:
        raise InvalidImageError("arquivo vazio")
    if len(data) > MAX_BYTES:
        raise InvalidImageError(f"arquivo acima de {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                if probe.width * probe.height > MAX_PIXELS:
                    raise InvalidImageError("imagem com dimensões acima do limite")
                probe.verify()
            # verify() invalida o objeto; reabrir e decodificar pega truncamento real.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                width, height = image.size
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
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


def validate_report_photo(data: bytes, *, policy: PhotoGatePolicy | None = None) -> ValidatedImage:
    """Conservative technical admission, not scene classification or detection.

    Bounded operational thresholds, not scientifically calibrated. Review stays
    mandatory while scene/face checks lack validated reference artifacts.
    Work is bounded by validate_image and the upload executor's two slots.
    """
    policy = policy if policy is not None else PhotoGatePolicy()
    image = validate_image(data)
    with Image.open(io.BytesIO(data)) as source:
        phash = perceptual_hash(source)
        gray = source.convert("L")
        gray.thumbnail((512, 512))
        pixels = np.asarray(gray, dtype=np.float32)
    brightness = float(pixels.mean())
    if min(pixels.shape) >= 3:
        laplacian = (
            pixels[1:-1, :-2]
            + pixels[1:-1, 2:]
            + pixels[:-2, 1:-1]
            + pixels[2:, 1:-1]
            - 4 * pixels[1:-1, 1:-1]
        )
        sharpness = float(laplacian.var())
    else:
        sharpness = 0.0
    reasons = []
    if min(image.width, image.height) < policy.min_side:
        reasons.append("resolution")
    if brightness < policy.brightness_min:
        reasons.append("underexposed")
    if brightness > policy.brightness_max:
        reasons.append("overexposed")
    if sharpness < policy.laplacian_min:
        reasons.append("blur")
    result: dict[str, Any] = {
        "version": "urmind-photo-quality-v1",
        "status": "REJECTED" if reasons else "NEEDS_REVIEW",
        "technical_status": "REJECTED" if reasons else "ACCEPTED",
        "scene_status": "NOT_VERIFIED",
        "face_status": "NOT_VERIFIED",
        "reasons": reasons,
        "metrics": {
            "width": image.width,
            "height": image.height,
            "brightness_mean": round(brightness, 3),
            "laplacian_variance": round(sharpness, 3),
        },
        "thresholds": {
            "min_side": policy.min_side,
            "brightness_min": policy.brightness_min,
            "brightness_max": policy.brightness_max,
            "laplacian_min": policy.laplacian_min,
        },
    }
    if reasons:
        hints = {
            "resolution": f"Foto pequena: use uma imagem com pelo menos {policy.min_side} pixels em cada lado.",
            "underexposed": "Foto muito escura: tente com mais luz.",
            "overexposed": "Foto muito clara: evite luz direta na câmera.",
            "blur": "Foto tremida ou desfocada: estabilize a câmera e ajuste o foco.",
        }
        raise PhotoRejectedError(result, hints[reasons[0]])
    exif = read_exif_location(data)
    result["warnings"] = []
    result["exif_timezone_known"] = exif.timezone_known
    if exif.captured_at is not None:
        now = datetime.now(UTC)
        # Unknown timezone stays unknown. A date-only comparison is deliberately
        # conservative (one extra day); this is a warning about a client claim.
        if exif.captured_at.tzinfo is None:
            old = (now.date() - exif.captured_at.date()).days > policy.old_photo_days + 1
        else:
            old = now - exif.captured_at > timedelta(days=policy.old_photo_days)
        if old:
            result["warnings"].append("old_exif_date_unverified")
    with Image.open(io.BytesIO(data)) as source:
        calibration = calibrated_policy(policy)
        if calibration is None:
            result["face_status"] = "UNCALIBRATED"
            result["scene_status"] = "UNCALIBRATED"
            return replace(image, photo_quality=result, phash=phash)
        try:
            faces = detect_faces(source)
        except ReferenceUnavailable as exc:
            result["face_status"] = "NOT_VERIFIED"
            result["face_unavailable_reason"] = str(exc)
        else:
            result["face_status"] = faces["status"]
            result["privacy"] = faces
            if faces["max_area_ratio"] > policy.dominant_face_ratio:
                result["status"] = "REJECTED"
                result["reasons"].append("dominant_face")
                raise PhotoRejectedError(
                    result,
                    "A foto deve mostrar o problema, não pessoas. Evite rostos em primeiro plano.",
                )
            if faces["boxes"]:
                result["warnings"].append("privacy_review_required")
        try:
            scene = scene_similarity(source)
        except ReferenceUnavailable as exc:
            result["scene_unavailable_reason"] = str(exc)
        else:
            result["scene"] = scene
            result["scene_status"] = "CALIBRATED"
            if calibration:
                if scene["margin"] < policy.scene_reject_margin:
                    result["status"] = "REJECTED"
                    result["reasons"].append("non_urban_scene")
                    raise PhotoRejectedError(
                        result, "A foto deve mostrar uma via pública. Enquadre a rua ou calçada."
                    )
                if (
                    scene["margin"] >= policy.scene_accept_margin
                    and result["face_status"] == "CHECKED"
                    and "privacy_review_required" not in result["warnings"]
                ):
                    result["status"] = "ACCEPTED"
            result["calibration_audit_id"] = calibration["activation_audit_id"]
    return replace(image, photo_quality=result, phash=phash)


def sanitize_public_image(data: bytes, *, policy: PhotoGatePolicy | None = None) -> ValidatedImage:
    """Create a metadata-free derivative; never mutate original evidence.

    Preserve raster coordinates so existing normalized boxes remain valid.
    Registered YuNet boxes are blurred with a margin. Plates and missed faces
    still require an explicit reviewer attestation about visible content.
    """
    validate_image(data)
    with Image.open(io.BytesIO(data)) as source:
        clean = Image.new("RGB", source.size, "white")
        converted = source.convert("RGBA")
        clean.paste(converted, mask=converted.getchannel("A"))
        try:
            if calibrated_policy(policy or PhotoGatePolicy()) is None:
                raise ReferenceUnavailable("UNCALIBRATED")
            faces = detect_faces(clean)
        except ReferenceUnavailable as exc:
            # Original publication gate always requires human privacy review.
            # Never claim automatic redaction when its artifact is unavailable.
            faces = {"status": "NOT_VERIFIED", "reason": str(exc), "boxes": []}
        blur_faces(clean, faces["boxes"])
        output = io.BytesIO()
        clean.save(output, format="JPEG", quality=90)
    return replace(
        validate_image(output.getvalue()),
        photo_quality={
            "face_redaction_status": faces["status"],
            "face_count": len(faces["boxes"]),
            "reference_sha256": faces.get("sha256"),
            "human_attestation_required": True,
        },
    )


def blur_faces(clean: Image.Image, boxes: list[list[float]]) -> None:
    """Shared redaction primitive for publication and external calibration inspection."""
    for left, top, right, bottom in boxes:
        margin_x, margin_y = (right - left) * 0.2, (bottom - top) * 0.2
        box = (
            int(max(0, left - margin_x) * clean.width),
            int(max(0, top - margin_y) * clean.height),
            int(min(1, right + margin_x) * clean.width),
            int(min(1, bottom + margin_y) * clean.height),
        )
        region = clean.crop(box)
        clean.paste(region.filter(ImageFilter.GaussianBlur(max(12, min(region.size) / 5))), box)


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
            "DELETE",
            f"{self._base}/object/{BUCKET}",
            json={"prefixes": [path]},
            headers=self._headers(),
        )
        if response.status_code not in (200, 204, 404):
            raise StorageError(f"Storage compensation refused (HTTP {response.status_code})")
