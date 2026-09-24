"""Foto existente → Capture, com localização honesta (MASTER_PLAN §6.3, §25 passo 4).

Este módulo é a ponte entre o que a foto declara (`app.services.exif`) e o
contrato de captura do núcleo. Ele não toca banco nem Storage: recebe bytes e
devolve o `CaptureCreate` que o passo 3 vai persistir.

As três regras do §6.3 que ele implementa:

1. GPS válido no EXIF → `location_source = exif`.
2. GPS do dispositivo tem prioridade sobre EXIF; divergência fica registrada.
3. Sem GPS válido → fallback manual; sem nenhum ponto a foto é preservada,
   mas não há marcador até confirmação. O sistema nunca adivinha a rua.

Sobre o instante: `Capture.captured_at` é o recebimento pelo servidor. Mesmo um
EXIF com offset é editável pelo remetente; sua alegação temporal fica em
`quality.exif_capture_time_unverified`, nunca como cronologia confiável.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import asin, cos, radians, sin, sqrt

from app.schemas.core import CaptureCreate, CaptureSource, Coordinate, LocationSource
from app.services.exif import ExifLocation, ExifStatus, read_exif_location

__all__ = ["CapturedAtSource", "PhotoIngest", "ingest_photo"]


class CapturedAtSource(StrEnum):
    """De onde veio o `captured_at` gravado. Vai para `quality`, e é auditável."""

    EXIF_WITH_OFFSET = "exif_with_offset"
    """Valor legado; novas capturas não usam EXIF como cronologia verificada."""

    EXIF_LOCAL_TIME = "exif_local_time"
    """Valor legado; novas capturas não aplicam o fuso atual do cliente."""

    RECEIVED_AT = "received_at"
    """Instante do servidor; EXIF e relógio do cliente são claims separados."""


@dataclass(frozen=True)
class PhotoIngest:
    """Resultado da ingestão: o que persistir e o que ainda falta perguntar."""

    capture: CaptureCreate
    exif: ExifLocation
    requires_manual_location: bool
    """Verdadeiro quando a captura entrou sem coordenada.

    A PWA precisa abrir o mapa e pedir a marcação antes que isso vire Event:
    sem coordenada não existe ocorrência geográfica (§11.1).
    """

    @property
    def has_location(self) -> bool:
        return self.capture.coordinate is not None


def ingest_photo(
    *,
    capture_key: str,
    image_bytes: bytes,
    received_at: datetime,
    manual_coordinate: Coordinate | None = None,
    manual_location_source: LocationSource = LocationSource.MANUAL,
    manual_overrides_exif: bool = False,
    client_captured_at: datetime | None = None,
    storage_path: str | None = None,
    source: CaptureSource = CaptureSource.EXIF_UPLOAD,
    user_description: str | None = None,
    location_conflict_distance_m: float = 500,
) -> PhotoIngest:
    """Monta o `CaptureCreate` de uma foto já existente.

    `manual_coordinate` é o ponto que o usuário marcou no mapa. Quando ele vem
    junto de um EXIF válido, o EXIF vence, salvo correção manual explícita.
    GPS do dispositivo tem prioridade sobre ambos. Todos são claims, não
    precisão metrológica comprovada.

    EXIF sem offset não prova um instante: o fuso atual do telefone que envia
    uma foto importada não é evidência do fuso no momento da captura.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at precisa ter fuso; instante sem fuso não é instante")

    exif = read_exif_location(image_bytes)
    # EXIF is editable by the sender, including timezone-bearing tags. Preserve
    # the claimed time below, but only server receipt is authoritative chronology.
    captured_at, captured_at_source = received_at, CapturedAtSource.RECEIVED_AT
    if client_captured_at is not None and client_captured_at.tzinfo is None:
        raise ValueError("client_captured_at exige fuso")

    if manual_location_source not in (LocationSource.MANUAL, LocationSource.GPS_DEVICE):
        raise ValueError("coordenada informada só pode ser manual ou GPS do dispositivo")
    if manual_overrides_exif and (
        manual_coordinate is None or manual_location_source is not LocationSource.MANUAL
    ):
        raise ValueError("correcao EXIF exige ponto manual confirmado")
    coordinate, location_source = _resolve_location(
        exif, manual_coordinate, manual_location_source, manual_overrides_exif
    )

    quality: dict[str, object] = {
        "exif_status": exif.status.value,
        "captured_at_source": captured_at_source.value,
        "location_conflict": False,
    }
    if exif.usable and exif.coordinate is not None:
        quality["exif_coordinate"] = exif.coordinate.model_dump()
        if manual_coordinate is not None and manual_location_source is LocationSource.GPS_DEVICE:
            a, b = manual_coordinate, exif.coordinate
            arc = (
                sin(radians(a.latitude - b.latitude) / 2) ** 2
                + cos(radians(a.latitude))
                * cos(radians(b.latitude))
                * sin(radians(a.longitude - b.longitude) / 2) ** 2
            )
            distance = 6371000 * 2 * asin(sqrt(min(1, max(0, arc))))
            quality["device_exif_distance_m"] = distance
            quality["location_conflict"] = distance > location_conflict_distance_m
    if source is CaptureSource.PWA_PHOTO and client_captured_at is not None:
        # A browser clock is self-reported. Preserve it, but never use it as
        # Event.occurred_at or trusted chronology merely because it was sent.
        quality["client_captured_at"] = client_captured_at.isoformat()
        quality["client_captured_at_trust"] = "unverified"
    if exif.detail:
        quality["exif_detail"] = exif.detail
    if exif.camera:
        quality["camera"] = exif.camera
    if exif.raw_tags:
        quality["exif_gps_tags"] = exif.raw_tags
    if exif.captured_at is not None:
        quality["exif_capture_time_unverified"] = exif.captured_at.isoformat()
        quality["exif_timezone_known"] = exif.timezone_known

    # O que o EXIF dizia fica registrado mesmo quando o humano corrige o ponto,
    # para que a correção seja rastreável e não uma sobrescrita silenciosa.
    if manual_coordinate is not None:
        quality["submitted_coordinate"] = manual_coordinate.model_dump()
        quality["submitted_location_source"] = manual_location_source.value
    if manual_overrides_exif:
        quality["manual_overrides_exif"] = True

    capture = CaptureCreate(
        capture_key=capture_key,
        source=source,
        source_location=location_source,
        captured_at=captured_at,
        storage_path=storage_path,
        user_description=user_description,
        coordinate=coordinate,
        quality=quality,
    )
    return PhotoIngest(
        capture=capture,
        exif=exif,
        requires_manual_location=coordinate is None,
    )


def _resolve_location(
    exif: ExifLocation,
    manual_coordinate: Coordinate | None,
    manual_location_source: LocationSource = LocationSource.MANUAL,
    manual_overrides_exif: bool = False,
) -> tuple[Coordinate | None, LocationSource]:
    if manual_coordinate is not None and manual_location_source is LocationSource.GPS_DEVICE:
        return manual_coordinate, manual_location_source
    if manual_overrides_exif and manual_coordinate is not None:
        return manual_coordinate, LocationSource.MANUAL
    if exif.usable and exif.coordinate is not None:
        return exif.coordinate, LocationSource.EXIF
    if manual_coordinate is not None:
        return manual_coordinate, LocationSource.MANUAL
    # Sem GPS e sem marcação: a captura existe, mas ainda não tem lugar no mundo.
    assert exif.status is not ExifStatus.OK or exif.coordinate is None
    return None, LocationSource.UNKNOWN
