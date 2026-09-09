"""Leitura de EXIF de fotos já existentes (MASTER_PLAN §6.3 e §25 passo 4).

A regra que governa este módulo é uma só: **nenhum GPS é inventado**. Quando a
foto não traz coordenada, ou traz uma coordenada que não resiste a verificação,
a saída diz isso explicitamente e o fluxo passa a exigir marcação manual no mapa
(§6.3). O UrMind não tenta adivinhar a rua pela aparência da imagem, e este
módulo jamais preenche uma lacuna com estimativa.

O que ele extrai é só o que está gravado no arquivo: coordenada, precisão
declarada pelo receptor, instante da captura e identificação do equipamento.

Sobre a precisão: EXIF raramente carrega uma incerteza. Quando existe, vem na
tag `GPSHPositioningError`, em metros. Não havendo essa tag, `accuracy_m` fica
`None` — e `None` significa "desconhecida", nunca "precisa" (§11.1).

Sobre a ferramenta: o §3 prevê ExifTool e Pillow. Aqui está o caminho Pillow,
que cobre JPEG/TIFF sem depender de binário externo. Formatos que o Pillow não
lê (HEIC de iPhone, RAW de fabricante) caem em `UNREADABLE` e ficam para o
ExifTool, quando esse caminho for necessário.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPS, Base

from app.schemas.core import Coordinate, LocationSource

__all__ = ["ExifLocation", "ExifStatus", "read_exif_location"]

# IFD que guarda o bloco GPS dentro do EXIF.
_GPS_IFD = 0x8825

# Coordenada exatamente (0, 0) fica no Atlântico, na costa da África. É válida no
# papel, mas na prática quase sempre é receptor sem fix ou campo zerado por
# software. Vai para confirmação humana em vez de virar um ponto no mapa (§27).
_NULL_ISLAND_TOLERANCE = 1e-9


class ExifStatus(StrEnum):
    """Resultado da leitura. Estado explícito no lugar de silêncio (§26)."""

    OK = "ok"
    """Coordenada e instante extraídos e validados."""

    NO_EXIF = "no_exif"
    """Arquivo legível, mas sem bloco EXIF — comum em captura de tela e reencode."""

    NO_GPS = "no_gps"
    """Tem EXIF, não tem GPS. Câmera sem receptor ou usuário com o GPS desligado."""

    INVALID_GPS = "invalid_gps"
    """Bloco GPS presente mas inconsistente: fora de faixa, malformado ou (0, 0)."""

    UNREADABLE = "unreadable"
    """Não é imagem que o Pillow abra, ou o arquivo está corrompido."""


@dataclass(frozen=True)
class ExifLocation:
    """O que a foto declara sobre si mesma. Campo ausente permanece ausente."""

    status: ExifStatus
    coordinate: Coordinate | None = None
    captured_at: datetime | None = None
    timezone_known: bool = False
    """Falso quando o EXIF trouxe a data sem fuso.

    `DateTimeOriginal` sozinho é hora local sem fuso declarado. Assumir UTC
    inventaria até três horas de diferença no Brasil, então o instante fica
    marcado como incompleto e quem consome decide o que fazer.
    """

    camera: str | None = None
    detail: str | None = None
    """Motivo legível quando o status não é OK. Para log e para a tela de revisão."""

    raw_tags: dict[str, Any] = field(default_factory=dict)
    """Tags cruas aproveitadas, para auditoria do que a foto realmente dizia."""

    @property
    def usable(self) -> bool:
        return self.status is ExifStatus.OK and self.coordinate is not None

    @property
    def location_source(self) -> LocationSource:
        """Procedência a gravar em `Capture.source_location` (§5.1).

        Sem GPS confiável a resposta é `MANUAL`: a interface precisa pedir que o
        usuário marque o ponto no mapa (§6.3).
        """
        return LocationSource.EXIF if self.usable else LocationSource.MANUAL


def _to_float(value: Any) -> float | None:
    """Converte um racional EXIF em float, sem estourar em dado corrompido."""
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(number):  # descarta NaN e infinito vindos de racional quebrado
        return None
    return number


def _dms_to_degrees(dms: Any, ref: Any) -> float | None:
    """Graus/minutos/segundos + hemisfério → graus decimais.

    O EXIF guarda a posição como três racionais e uma letra de referência. O
    sinal vem da letra: sul e oeste são negativos.
    """
    if not isinstance(dms, (tuple, list)) or len(dms) != 3:
        return None
    degrees = _to_float(dms[0])
    minutes = _to_float(dms[1])
    seconds = _to_float(dms[2])
    if degrees is None or minutes is None or seconds is None:
        return None
    if not (0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    value = abs(degrees) + minutes / 60 + seconds / 3600
    reference = str(ref).strip().upper() if ref is not None else ""
    if reference in ("S", "W"):
        value = -value
    elif reference not in ("N", "E"):
        return None
    return value


def _parse_datetime(raw: Any, offset: Any) -> tuple[datetime | None, bool]:
    """`DateTimeOriginal` (+ `OffsetTimeOriginal` quando houver) → datetime."""
    if not isinstance(raw, str):
        return None, False
    try:
        # O datetime naive é proposital (ver `timezone_known`): carimbar um fuso
        # aqui inventaria o horário em que a foto foi tirada.
        moment = datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007
    except ValueError:
        return None, False

    if isinstance(offset, str) and len(offset.strip()) >= 6:
        text = offset.strip()
        try:
            sign = 1 if text[0] == "+" else -1 if text[0] == "-" else None
            if sign is not None:
                hours, minutes = int(text[1:3]), int(text[4:6])
                delta = timedelta(hours=hours, minutes=minutes)
                if delta < timedelta(hours=24):
                    return moment.replace(tzinfo=timezone(sign * delta)), True
        except (ValueError, IndexError):
            pass

    # Sem fuso declarado: devolve o horário local como está e avisa o chamador.
    return moment, False


def read_exif_location(image_bytes: bytes) -> ExifLocation:
    """Lê o que a foto declara. Nunca deduz o que ela não diz.

    Recebe os bytes da imagem para não depender de caminho em disco: o arquivo
    pode vir de um upload ou do Supabase Storage sem tocar o sistema de arquivos.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            exif = image.getexif()
            gps_ifd = exif.get_ifd(_GPS_IFD) if exif else {}
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return ExifLocation(status=ExifStatus.UNREADABLE, detail=f"imagem ilegível: {exc}")

    if not exif:
        return ExifLocation(status=ExifStatus.NO_EXIF, detail="arquivo sem bloco EXIF")

    captured_at, timezone_known = _parse_datetime(
        exif.get(Base.DateTimeOriginal.value) or exif.get(Base.DateTime.value),
        exif.get(Base.OffsetTimeOriginal.value),
    )
    make = exif.get(Base.Make.value)
    model = exif.get(Base.Model.value)
    camera = " ".join(str(part).strip() for part in (make, model) if part) or None

    common: dict[str, Any] = {
        "captured_at": captured_at,
        "timezone_known": timezone_known,
        "camera": camera,
    }

    if not gps_ifd:
        return ExifLocation(
            status=ExifStatus.NO_GPS,
            detail="EXIF presente, sem bloco GPS; exigir marcação manual no mapa",
            **common,
        )

    latitude = _dms_to_degrees(
        gps_ifd.get(GPS.GPSLatitude.value), gps_ifd.get(GPS.GPSLatitudeRef.value)
    )
    longitude = _dms_to_degrees(
        gps_ifd.get(GPS.GPSLongitude.value), gps_ifd.get(GPS.GPSLongitudeRef.value)
    )

    raw_tags = {
        "GPSLatitude": str(gps_ifd.get(GPS.GPSLatitude.value)),
        "GPSLatitudeRef": str(gps_ifd.get(GPS.GPSLatitudeRef.value)),
        "GPSLongitude": str(gps_ifd.get(GPS.GPSLongitude.value)),
        "GPSLongitudeRef": str(gps_ifd.get(GPS.GPSLongitudeRef.value)),
    }

    if latitude is None or longitude is None:
        return ExifLocation(
            status=ExifStatus.INVALID_GPS,
            detail="bloco GPS malformado ou com referência de hemisfério ausente",
            raw_tags=raw_tags,
            **common,
        )

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return ExifLocation(
            status=ExifStatus.INVALID_GPS,
            detail=f"coordenada fora de faixa: {latitude}, {longitude}",
            raw_tags=raw_tags,
            **common,
        )

    if abs(latitude) < _NULL_ISLAND_TOLERANCE and abs(longitude) < _NULL_ISLAND_TOLERANCE:
        return ExifLocation(
            status=ExifStatus.INVALID_GPS,
            detail="coordenada (0, 0): receptor sem fix ou campo zerado; confirmar no mapa",
            raw_tags=raw_tags,
            **common,
        )

    accuracy = _to_float(gps_ifd.get(GPS.GPSHPositioningError.value))
    if accuracy is not None:
        raw_tags["GPSHPositioningError"] = str(accuracy)
        if accuracy < 0:
            accuracy = None

    return ExifLocation(
        status=ExifStatus.OK,
        coordinate=Coordinate(latitude=latitude, longitude=longitude, accuracy_m=accuracy),
        raw_tags=raw_tags,
        **common,
    )
