"""Fundação do Scout (§21): envelopes e Gateway. Sem hardware e sem rede.

O que estes testes garantem é o que ainda dá para garantir sem o Scout físico:
o formato do que trafega, o carimbo de tempo honesto e o comportamento do
Gateway quando a câmera não existe ou falha.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.gateway import (
    CLOCK_GATEWAY,
    CoreClient,
    ScoutCameraError,
    describe_frame,
    envelope_for,
    run,
)
from app.schemas.scout import (
    DeviceKind,
    FrameQuality,
    MissionRef,
    Modality,
    ScoutIdentity,
    ScoutState,
    TelemetryEnvelope,
)

DEVICE = ScoutIdentity(code="SCOUT-PILOTO-01", kind=DeviceKind.ESP32_CAM, firmware_version="0.1.0")
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "9c6360000002000154a24f5f0000000049454e44ae426082"
)


class FakeSource:
    name = "fake://camera"

    def __init__(self, *frames: bytes | Exception) -> None:
        self._frames = list(frames)

    async def frame(self) -> tuple[bytes, FrameQuality]:
        item = self._frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, describe_frame(item, 12.5)


class FakeCore(CoreClient):
    def __init__(self) -> None:  # sem HTTP nestes testes
        self.sent: list[tuple[bytes, object]] = []

    async def send(self, data, frame):  # type: ignore[override]
        self.sent.append((data, frame))
        return {"id": "11111111-1111-4111-8111-111111111111", "created": True}


def test_medida_do_quadro_sai_do_proprio_arquivo() -> None:
    quality = describe_frame(PNG, 12.5)
    assert quality.bytes == len(PNG)
    assert quality.width == 1 and quality.height == 1
    assert quality.sha256 and len(quality.sha256) == 64
    assert quality.fetch_latency_ms == 12.5


def test_quadro_ilegivel_nao_recebe_dimensao_inventada() -> None:
    quality = describe_frame(b"isto nao e imagem")
    assert quality.width is None and quality.height is None
    assert quality.bytes == 17
    # Sem transporte medido, latência fica ausente em vez de zero.
    assert quality.fetch_latency_ms is None


def test_carimbo_declara_que_a_hora_e_do_gateway_e_nao_do_gps() -> None:
    frame = envelope_for(
        device=DEVICE,
        mission=MissionRef(code="piloto-fecap"),
        quality=describe_frame(PNG),
        coordinate=None,
        sequence=0,
    )
    assert frame.clock_source == CLOCK_GATEWAY
    assert frame.captured_at.tzinfo is not None
    # Enquanto o GNSS não entrega posição, o quadro vai sem coordenada.
    assert frame.coordinate is None


@pytest.mark.asyncio
async def test_sem_camera_o_gateway_declara_no_device() -> None:
    status = await run(
        source=None, core=None, device=DEVICE, mission=None, interval_s=0, once=True
    )
    assert status.scout_state is ScoutState.NO_DEVICE
    assert status.frames_sent == 0
    assert "câmera" in (status.last_error or "")


@pytest.mark.asyncio
async def test_quadro_real_chega_ao_nucleo_pelo_mesmo_endpoint_da_pwa() -> None:
    core = FakeCore()
    status = await run(
        source=FakeSource(PNG),
        core=core,
        device=DEVICE,
        mission=MissionRef(code="piloto-fecap"),
        interval_s=0,
        once=True,
    )
    assert status.scout_state is ScoutState.LIVE
    assert status.frames_sent == 1 and status.frames_failed == 0
    data, frame = core.sent[0]
    assert data == PNG
    assert frame.device.code == DEVICE.code and frame.mission.code == "piloto-fecap"


@pytest.mark.asyncio
async def test_falha_da_camera_vira_estado_e_nao_excecao_silenciosa() -> None:
    status = await run(
        source=FakeSource(ScoutCameraError("câmera inacessível: ConnectError")),
        core=FakeCore(),
        device=DEVICE,
        mission=None,
        interval_s=0,
        once=True,
    )
    assert status.scout_state is ScoutState.OFFLINE
    assert status.frames_failed == 1
    assert status.last_frame_at is None


def test_envelope_recusa_campo_fora_do_contrato() -> None:
    with pytest.raises(ValidationError):
        ScoutIdentity(code="SCOUT-01", kind=DeviceKind.ESP32_CAM, bateria_pct=80)  # type: ignore[call-arg]


def test_telemetria_existe_como_formato_mas_nao_inventa_leitura() -> None:
    window = TelemetryEnvelope(
        device=DEVICE,
        modality=Modality.IMU,
        window_start=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        window_end=datetime(2026, 9, 18, 12, 0, 5, tzinfo=UTC),
    )
    # Sem sensor conectado, taxa, amostras e sincronização ficam ausentes.
    assert window.sample_rate_hz is None
    assert window.samples is None
    assert window.sync_quality is None
