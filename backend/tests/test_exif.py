"""Leitura de EXIF — o teste central é que nenhum GPS seja inventado (§6.3, §27).

As imagens são construídas aqui, byte a byte, em vez de virem de fixtures no
repositório: assim cada caso declara exatamente qual tag está sendo exercitada.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta, timezone

import pytest
from PIL import Image
from PIL.ExifTags import GPS, Base
from PIL.TiffImagePlugin import IFDRational as R

from app.schemas.core import LocationSource
from app.services.exif import ExifStatus, read_exif_location

# Av. Paulista, altura do MASP: 23°33'40.56"S, 46°39'21.60"W.
PAULISTA_LAT = -(23 + 33 / 60 + 40.56 / 3600)
PAULISTA_LON = -(46 + 39 / 60 + 21.60 / 3600)


def build_jpeg(
    *,
    gps: dict[int, object] | None = None,
    datetime_original: str | None = "2026:09:04 12:34:56",
    offset: str | None = None,
    make: str | None = None,
    model: str | None = None,
) -> bytes:
    """JPEG mínimo com exatamente as tags que o caso de teste precisa."""
    image = Image.new("RGB", (8, 8), "gray")
    exif = image.getexif()
    if datetime_original is not None:
        exif[Base.DateTimeOriginal.value] = datetime_original
    if offset is not None:
        exif[Base.OffsetTimeOriginal.value] = offset
    if make is not None:
        exif[Base.Make.value] = make
    if model is not None:
        exif[Base.Model.value] = model
    if gps:
        ifd = exif.get_ifd(0x8825)
        ifd.update(gps)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif if len(exif) or gps else None)
    return buffer.getvalue()


def gps_block(
    lat_deg: tuple[int, int, float] = (23, 33, 40.56),
    lat_ref: str = "S",
    lon_deg: tuple[int, int, float] = (46, 39, 21.60),
    lon_ref: str = "W",
    accuracy: float | None = None,
) -> dict[int, object]:
    block: dict[int, object] = {
        GPS.GPSLatitudeRef.value: lat_ref,
        GPS.GPSLatitude.value: tuple(R(int(v * 100), 100) for v in lat_deg),
        GPS.GPSLongitudeRef.value: lon_ref,
        GPS.GPSLongitude.value: tuple(R(int(v * 100), 100) for v in lon_deg),
    }
    if accuracy is not None:
        block[GPS.GPSHPositioningError.value] = R(int(accuracy * 100), 100)
    return block


# --------------------------------------------------------------- caminho feliz


def test_extrai_coordenada_do_hemisferio_sul_e_oeste():
    """Refs S/W precisam virar negativo; trocar o sinal joga o ponto noutro continente."""
    result = read_exif_location(build_jpeg(gps=gps_block()))

    assert result.status is ExifStatus.OK
    assert result.usable
    assert result.coordinate is not None
    assert result.coordinate.latitude == pytest.approx(PAULISTA_LAT, abs=1e-6)
    assert result.coordinate.longitude == pytest.approx(PAULISTA_LON, abs=1e-6)
    assert result.location_source is LocationSource.EXIF


def test_hemisferio_norte_e_leste_ficam_positivos():
    result = read_exif_location(
        build_jpeg(gps=gps_block(lat_deg=(48, 51, 29.6), lat_ref="N",
                                lon_deg=(2, 17, 40.2), lon_ref="E"))
    )

    assert result.status is ExifStatus.OK
    assert result.coordinate is not None
    assert result.coordinate.latitude > 0
    assert result.coordinate.longitude > 0


def test_accuracy_vem_da_tag_e_nao_e_arbitrada():
    com_tag = read_exif_location(build_jpeg(gps=gps_block(accuracy=12.5)))
    sem_tag = read_exif_location(build_jpeg(gps=gps_block()))

    assert com_tag.coordinate is not None
    assert com_tag.coordinate.accuracy_m == pytest.approx(12.5)
    # Sem a tag a precisão é desconhecida — e desconhecida não vira número (§11.1).
    assert sem_tag.coordinate is not None
    assert sem_tag.coordinate.accuracy_m is None


def test_camera_e_registrada_quando_declarada():
    result = read_exif_location(build_jpeg(gps=gps_block(), make="Marca", model="Modelo X"))
    assert result.camera == "Marca Modelo X"


# ------------------------------------------------------------------- instante


def test_datetime_com_offset_fica_ciente_do_fuso():
    result = read_exif_location(build_jpeg(gps=gps_block(), offset="-03:00"))

    assert result.timezone_known is True
    assert result.captured_at == datetime(
        2026, 9, 4, 12, 34, 56, tzinfo=timezone(-timedelta(hours=3))
    )
    assert result.captured_at.astimezone(UTC).hour == 15


def test_datetime_sem_offset_nao_recebe_fuso_inventado():
    """Assumir UTC aqui deslocaria a foto em até 3 horas no Brasil."""
    result = read_exif_location(build_jpeg(gps=gps_block()))

    assert result.timezone_known is False
    assert result.captured_at is not None
    assert result.captured_at.tzinfo is None


def test_datetime_corrompido_nao_derruba_a_leitura_do_gps():
    result = read_exif_location(build_jpeg(gps=gps_block(), datetime_original="nao-e-data"))

    assert result.status is ExifStatus.OK
    assert result.captured_at is None
    assert result.coordinate is not None


# ------------------------------------------------- ausência e dados suspeitos


def test_sem_bloco_gps_exige_marcacao_manual():
    result = read_exif_location(build_jpeg(gps=None))

    assert result.status is ExifStatus.NO_GPS
    assert result.usable is False
    assert result.coordinate is None
    assert result.location_source is LocationSource.MANUAL
    assert "manual" in (result.detail or "")


def test_imagem_sem_exif_nenhum():
    image = Image.new("RGB", (8, 8), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")

    result = read_exif_location(buffer.getvalue())

    assert result.status is ExifStatus.NO_EXIF
    assert result.coordinate is None
    assert result.location_source is LocationSource.MANUAL


def test_coordenada_zero_zero_vai_para_confirmacao():
    """(0, 0) é quase sempre receptor sem fix, não um ponto no Atlântico."""
    result = read_exif_location(
        build_jpeg(gps=gps_block(lat_deg=(0, 0, 0.0), lat_ref="N",
                                lon_deg=(0, 0, 0.0), lon_ref="E"))
    )

    assert result.status is ExifStatus.INVALID_GPS
    assert result.coordinate is None
    assert result.location_source is LocationSource.MANUAL


def test_referencia_de_hemisferio_ausente_invalida_a_coordenada():
    block = gps_block()
    del block[GPS.GPSLatitudeRef.value]

    result = read_exif_location(build_jpeg(gps=block))

    assert result.status is ExifStatus.INVALID_GPS
    assert result.coordinate is None


def test_minutos_fora_de_faixa_sao_rejeitados():
    result = read_exif_location(build_jpeg(gps=gps_block(lat_deg=(23, 99, 0.0))))

    assert result.status is ExifStatus.INVALID_GPS
    assert result.coordinate is None


def test_latitude_acima_de_90_e_rejeitada():
    result = read_exif_location(build_jpeg(gps=gps_block(lat_deg=(120, 0, 0.0), lat_ref="N")))

    assert result.status is ExifStatus.INVALID_GPS
    assert result.coordinate is None


def test_arquivo_que_nao_e_imagem():
    result = read_exif_location(b"isto nao e uma imagem")

    assert result.status is ExifStatus.UNREADABLE
    assert result.coordinate is None
    assert result.location_source is LocationSource.MANUAL


def test_jpeg_truncado():
    truncado = build_jpeg(gps=gps_block())[:60]

    result = read_exif_location(truncado)

    assert result.status in (ExifStatus.UNREADABLE, ExifStatus.NO_EXIF)
    assert result.coordinate is None


# ------------------------------------------------------------------ auditoria


def test_tags_cruas_ficam_registradas_para_auditoria():
    result = read_exif_location(build_jpeg(gps=gps_block(accuracy=8.0)))

    assert result.raw_tags["GPSLatitudeRef"] == "S"
    assert result.raw_tags["GPSLongitudeRef"] == "W"
    assert "GPSHPositioningError" in result.raw_tags


def test_nenhum_status_de_falha_produz_coordenada():
    """Invariante do §27: só o caminho OK entrega ponto."""
    entradas = [
        b"lixo",
        build_jpeg(gps=None),
        build_jpeg(gps=gps_block(lat_deg=(0, 0, 0.0), lat_ref="N",
                                 lon_deg=(0, 0, 0.0), lon_ref="E")),
        build_jpeg(gps=gps_block(lat_deg=(23, 99, 0.0))),
    ]
    for dados in entradas:
        result = read_exif_location(dados)
        if result.status is not ExifStatus.OK:
            assert result.coordinate is None
            assert result.usable is False
            assert result.location_source is LocationSource.MANUAL
