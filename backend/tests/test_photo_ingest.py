"""Ingestão de foto existente — §6.3: nenhum GPS inventado, nenhuma sobrescrita muda."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.core import CaptureSource, Coordinate, LocationSource
from app.services.exif import ExifStatus
from app.services.photo_ingest import CapturedAtSource, ingest_photo
from tests.test_exif import PAULISTA_LAT, PAULISTA_LON, build_jpeg, gps_block

RECEBIDA_EM = datetime(2026, 9, 6, 18, 0, tzinfo=UTC)


def ingest(image_bytes: bytes, **kwargs):
    return ingest_photo(
        capture_key=kwargs.pop("capture_key", "cap-teste-0001"),
        image_bytes=image_bytes,
        received_at=kwargs.pop("received_at", RECEBIDA_EM),
        **kwargs,
    )


def test_foto_com_gps_valido_vira_capture_com_origem_exif():
    result = ingest(build_jpeg(gps=gps_block(), offset="-03:00"))

    assert result.requires_manual_location is False
    assert result.has_location
    capture = result.capture
    assert capture.source_location is LocationSource.EXIF
    assert capture.coordinate is not None
    assert capture.coordinate.latitude == pytest.approx(PAULISTA_LAT, abs=1e-6)
    assert capture.coordinate.longitude == pytest.approx(PAULISTA_LON, abs=1e-6)
    assert capture.quality["exif_status"] == ExifStatus.OK.value


def test_foto_sem_gps_fica_sem_coordenada_e_pede_marcacao():
    result = ingest(build_jpeg(gps=None))

    assert result.requires_manual_location is True
    assert result.capture.coordinate is None
    assert result.capture.source_location is LocationSource.UNKNOWN
    assert result.capture.quality["exif_status"] == ExifStatus.NO_GPS.value


def test_ponto_marcado_no_mapa_entra_como_manual():
    marcado = Coordinate(latitude=-23.5613, longitude=-46.6560, accuracy_m=5)

    result = ingest(build_jpeg(gps=None), manual_coordinate=marcado)

    assert result.requires_manual_location is False
    assert result.capture.source_location is LocationSource.MANUAL
    assert result.capture.coordinate == marcado


def test_device_exif_conflict_preserves_both_claims():
    device = Coordinate(latitude=-22, longitude=-45, accuracy_m=8)
    result = ingest(
        build_jpeg(gps=gps_block()),
        manual_coordinate=device,
        manual_location_source=LocationSource.GPS_DEVICE,
    )
    assert result.capture.coordinate == device
    assert result.capture.quality["location_conflict"] is True
    assert result.capture.quality["exif_coordinate"]["latitude"] == pytest.approx(PAULISTA_LAT)


def test_description_plain_text_and_length():
    from pydantic import ValidationError

    result = ingest(build_jpeg(gps=None), user_description="  <script>x</script>\x00  ")
    assert result.capture.user_description == "<script>x</script>"
    with pytest.raises(ValidationError):
        ingest(build_jpeg(gps=None), user_description="a" * 501)


def test_exif_tem_prioridade_sobre_fallback_manual():
    """Server EXIF precedes manual fallback; neither claim is silently discarded."""
    marcado = Coordinate(latitude=-23.5000, longitude=-46.6000, accuracy_m=8)

    result = ingest(build_jpeg(gps=gps_block()), manual_coordinate=marcado)

    assert result.capture.source_location is LocationSource.EXIF
    assert result.capture.coordinate.latitude == pytest.approx(PAULISTA_LAT)
    anterior = result.capture.quality["exif_coordinate"]
    assert anterior["latitude"] == pytest.approx(PAULISTA_LAT, abs=1e-6)
    assert anterior["longitude"] == pytest.approx(PAULISTA_LON, abs=1e-6)


def test_arquivo_ilegivel_nao_impede_a_captura_mas_exige_marcacao():
    result = ingest(b"nao e imagem")

    assert result.requires_manual_location is True
    assert result.capture.coordinate is None
    assert result.capture.quality["exif_status"] == ExifStatus.UNREADABLE.value


# ------------------------------------------------------------------ instante


def test_exif_com_offset_fica_como_claim_nao_verificada():
    result = ingest(build_jpeg(gps=gps_block(), offset="-03:00"))

    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.captured_at == RECEBIDA_EM
    assert result.capture.quality["exif_capture_time_unverified"]
    assert result.capture.quality["exif_timezone_known"] is True


def test_exif_sem_offset_nao_usa_fuso_atual_do_cliente():
    result = ingest(build_jpeg(gps=gps_block()))

    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.captured_at == RECEBIDA_EM
    assert result.capture.quality["exif_capture_time_unverified"]
    assert result.capture.quality["exif_timezone_known"] is False


def test_exif_sem_offset_e_sem_fuso_do_cliente_cai_para_o_recebimento():
    """Sem fuso, o horário de parede não vira instante — não se arbitra UTC."""
    result = ingest(build_jpeg(gps=gps_block()))

    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.captured_at == RECEBIDA_EM


def test_sem_data_no_exif_usa_o_recebimento():
    result = ingest(build_jpeg(gps=gps_block(), datetime_original=None))

    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.captured_at == RECEBIDA_EM


def test_received_at_sem_fuso_e_recusado():
    with pytest.raises(ValueError, match="fuso"):
        # O naive aqui é o próprio caso de teste: a função tem que recusá-lo.
        ingest(build_jpeg(gps=gps_block()), received_at=datetime(2026, 9, 6, 18, 0))  # noqa: DTZ001


def test_foto_pwa_preserva_instante_do_dispositivo_sem_alterar_ocorrencia():
    captured = datetime(2026, 9, 6, 17, 55, tzinfo=UTC)
    result = ingest(
        build_jpeg(gps=None),
        source=CaptureSource.PWA_PHOTO,
        client_captured_at=captured,
        manual_coordinate=Coordinate(latitude=-23.5, longitude=-46.6, accuracy_m=10),
        manual_location_source=LocationSource.GPS_DEVICE,
    )
    assert result.capture.captured_at == RECEBIDA_EM
    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.quality["client_captured_at"] == captured.isoformat()
    assert result.capture.quality["client_captured_at_trust"] == "unverified"
    assert result.capture.source_location is LocationSource.GPS_DEVICE


def test_pwa_com_exif_antigo_nao_reescreve_ocorrencia():
    result = ingest(build_jpeg(gps=gps_block(), offset="-03:00"), source=CaptureSource.PWA_PHOTO)
    assert result.capture.captured_at == RECEBIDA_EM
    assert result.capture.quality["captured_at_source"] == CapturedAtSource.RECEIVED_AT.value
    assert result.capture.quality["exif_capture_time_unverified"]


def test_foto_pwa_rejeita_instante_sem_fuso():
    with pytest.raises(ValueError, match="fuso"):
        ingest(
            build_jpeg(gps=None),
            source=CaptureSource.PWA_PHOTO,
            client_captured_at=datetime(2026, 9, 6, 17, 55),  # noqa: DTZ001
        )
