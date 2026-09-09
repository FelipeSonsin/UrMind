"""Foto existente → Capture, com localização honesta (MASTER_PLAN §6.3, §25 passo 4).

Este módulo é a ponte entre o que a foto declara (`app.services.exif`) e o
contrato de captura do núcleo. Ele não toca banco nem Storage: recebe bytes e
devolve o `CaptureCreate` que o passo 3 vai persistir.

As três regras do §6.3 que ele implementa:

1. GPS válido no EXIF → `location_source = exif`.
2. Sem GPS → a interface **exige** que o usuário marque o ponto no mapa; o
   sistema não adivinha a rua pela aparência da foto.
3. Ponto marcado à mão → `location_source = manual`, e o que o EXIF dizia
   continua registrado para auditoria.

Sobre o instante: `Capture.captured_at` é obrigatório e a coluna é `timestamptz`.
Um `DateTimeOriginal` sem fuso não é um instante — é um horário de parede. Em vez
de gravar isso como se fosse UTC (o que deslocaria a foto em até três horas no
Brasil), a procedência do horário fica registrada em `quality.captured_at_source`
e quem consome sabe o que está lendo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum

from app.schemas.core import CaptureCreate, CaptureSource, Coordinate, LocationSource
from app.services.exif import ExifLocation, ExifStatus, read_exif_location

__all__ = ["CapturedAtSource", "PhotoIngest", "ingest_photo"]


class CapturedAtSource(StrEnum):
    """De onde veio o `captured_at` gravado. Vai para `quality`, e é auditável."""

    EXIF_WITH_OFFSET = "exif_with_offset"
    """O EXIF trouxe data e fuso. É o único caso em que o instante é do arquivo."""

    EXIF_LOCAL_TIME = "exif_local_time"
    """Data do EXIF sem fuso, completada com o fuso informado pelo cliente."""

    RECEIVED_AT = "received_at"
    """A foto não disse quando foi tirada; vale a hora de recebimento."""


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
    client_timezone: tzinfo | None = None,
    storage_path: str | None = None,
    source: CaptureSource = CaptureSource.EXIF_UPLOAD,
) -> PhotoIngest:
    """Monta o `CaptureCreate` de uma foto já existente.

    `manual_coordinate` é o ponto que o usuário marcou no mapa. Quando ele vem
    junto de um EXIF válido, **o manual vence**: uma pessoa olhando o mapa sabe
    mais que o receptor do celular, e o valor do EXIF continua em `quality` para
    a auditoria (§6.3).

    `client_timezone` é o fuso do dispositivo que enviou a foto. Sem ele, uma
    data EXIF sem offset não vira instante e o `captured_at` cai para
    `received_at`.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at precisa ter fuso; instante sem fuso não é instante")

    exif = read_exif_location(image_bytes)
    captured_at, captured_at_source = _resolve_captured_at(exif, received_at, client_timezone)

    coordinate, location_source = _resolve_location(exif, manual_coordinate)

    quality: dict[str, object] = {
        "exif_status": exif.status.value,
        "captured_at_source": captured_at_source.value,
    }
    if exif.detail:
        quality["exif_detail"] = exif.detail
    if exif.camera:
        quality["camera"] = exif.camera
    if exif.raw_tags:
        quality["exif_gps_tags"] = exif.raw_tags

    # O que o EXIF dizia fica registrado mesmo quando o humano corrige o ponto,
    # para que a correção seja rastreável e não uma sobrescrita silenciosa.
    if manual_coordinate is not None and exif.usable and exif.coordinate is not None:
        quality["exif_coordinate_overridden"] = {
            "latitude": exif.coordinate.latitude,
            "longitude": exif.coordinate.longitude,
            "accuracy_m": exif.coordinate.accuracy_m,
        }

    capture = CaptureCreate(
        capture_key=capture_key,
        source=source,
        source_location=location_source,
        captured_at=captured_at,
        storage_path=storage_path,
        coordinate=coordinate,
        quality=quality,
    )
    return PhotoIngest(
        capture=capture,
        exif=exif,
        requires_manual_location=coordinate is None,
    )


def _resolve_captured_at(
    exif: ExifLocation,
    received_at: datetime,
    client_timezone: tzinfo | None,
) -> tuple[datetime, CapturedAtSource]:
    if exif.captured_at is None:
        return received_at, CapturedAtSource.RECEIVED_AT
    if exif.timezone_known:
        return exif.captured_at, CapturedAtSource.EXIF_WITH_OFFSET
    if client_timezone is not None:
        return exif.captured_at.replace(tzinfo=client_timezone), CapturedAtSource.EXIF_LOCAL_TIME
    # Horário de parede sem fuso conhecido: não dá para transformar em instante
    # sem inventar até três horas de diferença. Fica o recebimento (§27).
    return received_at, CapturedAtSource.RECEIVED_AT


def _resolve_location(
    exif: ExifLocation,
    manual_coordinate: Coordinate | None,
) -> tuple[Coordinate | None, LocationSource]:
    if manual_coordinate is not None:
        return manual_coordinate, LocationSource.MANUAL
    if exif.usable and exif.coordinate is not None:
        return exif.coordinate, LocationSource.EXIF
    # Sem GPS e sem marcação: a captura existe, mas ainda não tem lugar no mundo.
    assert exif.status is not ExifStatus.OK or exif.coordinate is None
    return None, LocationSource.UNKNOWN
