"""Contratos do Scout e do Gateway (MASTER_PLAN §21).

O Scout não inaugura um sistema paralelo: ele é mais uma origem de `Capture`
para o mesmo núcleo. O que estes envelopes fazem é dar forma ao que vem do
hardware — identidade, sessão, instante e qualidade — antes de virar captura.

As tabelas `devices`, `missions` e `sensor_assets` já existem desde a 0001 e
continuam sendo o registro persistente; aqui só se define o que trafega.

Nada aqui gera leitura: sem sensor conectado, o campo é ausente, não zero.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.core import CaptureCreate, CaptureSource, Coordinate, LocationSource


class DeviceKind(StrEnum):
    """Peças previstas na §0.1. A lista não cresce sem decisão de hardware."""

    ESP32_CAM = "esp32_cam"
    ESP32_DEVKIT = "esp32_devkit"
    GATEWAY = "gateway"


class Modality(StrEnum):
    IMAGE = "image"
    IMU = "imu"
    AUDIO = "audio"
    GNSS = "gnss"


class ScoutIdentity(BaseModel):
    """Quem enviou. `code` é estável e é a chave natural em `devices`."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=64)
    kind: DeviceKind
    firmware_version: str | None = None


class MissionRef(BaseModel):
    """Sessão de coleta. Sem missão declarada, a captura ainda é válida."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=64)
    operator: str | None = None


class FrameQuality(BaseModel):
    """O que o Gateway consegue afirmar sobre o quadro, e só isso."""

    model_config = ConfigDict(extra="forbid")

    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = None
    # Latência medida entre o pedido e o quadro completo, quando o transporte
    # permite medir. Não é latência de rede estimada.
    fetch_latency_ms: float | None = Field(default=None, ge=0)


class CameraFrameEnvelope(BaseModel):
    """Um quadro real da câmera do Scout, pronto para virar `Capture`.

    `captured_at` é o instante do Scout quando ele o informa; enquanto o
    TIMEPULSE do NEO-M8N não estiver ligado (§21.2), vale o instante em que o
    Gateway recebeu o quadro, e `clock_source` diz qual dos dois é.
    """

    model_config = ConfigDict(extra="forbid")

    device: ScoutIdentity
    mission: MissionRef | None = None
    captured_at: datetime
    clock_source: str = Field(description="gateway_receipt ou scout_gnss")
    sequence: int | None = Field(default=None, ge=0)
    coordinate: Coordinate | None = None
    quality: FrameQuality = Field(default_factory=FrameQuality)


class TelemetryEnvelope(BaseModel):
    """Janela de sensor (IMU, áudio, GNSS) associada por tempo.

    Fica definido agora porque o formato precisa ser estável antes do firmware
    existir. Nenhum produtor real envia isto ainda: o UrMind não publica
    telemetria que não tenha vindo de um sensor.
    """

    model_config = ConfigDict(extra="forbid")

    device: ScoutIdentity
    mission: MissionRef | None = None
    modality: Modality
    window_start: datetime
    window_end: datetime
    sample_rate_hz: float | None = Field(default=None, gt=0)
    samples: int | None = Field(default=None, ge=0)
    # Qualidade da sincronização com a grade de tempo, quando medida (§21.2).
    sync_quality: float | None = Field(default=None, ge=0, le=1)
    storage_path: str | None = None


class ScoutState(StrEnum):
    LIVE = "live"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    NO_DEVICE = "no_device"


class GatewayStatus(BaseModel):
    """Estado do Gateway, medido — nunca presumido a partir da configuração."""

    model_config = ConfigDict(extra="forbid")

    gateway_version: str
    camera_source: str | None = None
    scout_state: ScoutState
    last_frame_at: datetime | None = None
    frames_sent: int = 0
    frames_failed: int = 0
    last_error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------- scout-payload-v1
# Contrato de interface da Fase 11. Nenhum produtor real existe; o payload é
# validado e convertido para o mesmo `CaptureCreate` do núcleo (§1.1, §21) —
# não há segundo pipeline, e Detection/Event continuam sendo gerados pelo Worker.

SCOUT_PAYLOAD_VERSION = "scout-payload-v1"


class ClockSource(StrEnum):
    """De onde veio o instante. Relógio do Gateway nunca se passa por GPS."""

    GATEWAY_RECEIPT = "gateway_receipt"
    SCOUT_GNSS = "scout_gnss"
    SCOUT_RTC_UNSYNCED = "scout_rtc_unsynced"


class GnssFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Sem accuracy informada o fix não entra: o núcleo exige saber o erro (§11.1).
    accuracy_m: float = Field(gt=0)
    fix_type: str | None = None
    satellites: int | None = Field(default=None, ge=0)
    heading_deg: float | None = Field(default=None, ge=0, le=360)
    speed_mps: float | None = Field(default=None, ge=0)
    fixed_at: datetime


class ImuWindowMeta(BaseModel):
    """Metadados da janela de IMU; as amostras vão para SensorAsset, não para cá."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    sample_rate_hz: float = Field(gt=0)
    samples: int = Field(ge=0)
    calibrated: bool = False
    storage_path: str | None = None


class AudioWindowMeta(BaseModel):
    """INMP441 sem calibração metrológica: nunca carrega dB SPL absoluto (§21.4)."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    sample_rate_hz: float = Field(gt=0)
    samples: int = Field(ge=0)
    storage_path: str | None = None


class DeviceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    battery_v: float | None = Field(default=None, ge=0)
    temperature_c: float | None = None
    free_storage_bytes: int | None = Field(default=None, ge=0)
    uptime_s: int | None = Field(default=None, ge=0)
    errors: list[str] = Field(default_factory=list)


class ScoutPayloadV1(BaseModel):
    """Envelope completo de um quadro do Scout com suas evidências sincronizadas."""

    model_config = ConfigDict(extra="forbid")

    payload_version: str = Field(default=SCOUT_PAYLOAD_VERSION, pattern=r"^scout-payload-v1$")
    device_id: str = Field(min_length=3, max_length=64)
    firmware_version: str = Field(min_length=1, max_length=64)
    # Idempotência: o mesmo quadro reenviado (spool offline, §20) gera a mesma Capture.
    capture_id: str = Field(min_length=6, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    mission: MissionRef | None = None
    captured_at: datetime
    clock_source: ClockSource
    frame: FrameQuality
    gnss: GnssFix | None = None
    imu: ImuWindowMeta | None = None
    audio: AudioWindowMeta | None = None
    health: DeviceHealth = Field(default_factory=DeviceHealth)
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_times(self) -> ScoutPayloadV1:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at exige fuso")
        for window in (self.imu, self.audio):
            if window is not None and window.window_end < window.window_start:
                raise ValueError("janela de sensor com fim antes do início")
        if self.clock_source is ClockSource.SCOUT_GNSS and self.gnss is None:
            raise ValueError("clock_source=scout_gnss exige fix GNSS")
        if self.gnss is not None and self.gnss.fixed_at > self.captured_at:
            raise ValueError("fix GNSS posterior ao quadro não localiza este quadro")
        return self

    @property
    def idempotency_key(self) -> str:
        return f"scout-{self.device_id}-{self.capture_id}"


def scout_payload_to_capture(
    payload: ScoutPayloadV1, *, storage_path: str | None = None
) -> CaptureCreate:
    """Converge o Scout para o `CaptureCreate` canônico; detecções nunca vêm do dispositivo."""
    coordinate = (
        Coordinate(
            latitude=payload.gnss.latitude,
            longitude=payload.gnss.longitude,
            accuracy_m=payload.gnss.accuracy_m,
        )
        if payload.gnss
        else None
    )
    return CaptureCreate(
        capture_key=payload.idempotency_key,
        source=CaptureSource.SCOUT,
        source_location=LocationSource.GPS_SCOUT if coordinate else LocationSource.UNKNOWN,
        captured_at=payload.captured_at,
        storage_path=storage_path,
        coordinate=coordinate,
        heading_deg=payload.gnss.heading_deg if payload.gnss else None,
        speed_mps=payload.gnss.speed_mps if payload.gnss else None,
        quality={
            "payload_version": payload.payload_version,
            "device_id": payload.device_id,
            "firmware_version": payload.firmware_version,
            "clock_source": payload.clock_source.value,
            "frame": payload.frame.model_dump(exclude_none=True),
            "imu": payload.imu.model_dump(mode="json") if payload.imu else None,
            "audio": payload.audio.model_dump(mode="json") if payload.audio else None,
            "health": payload.health.model_dump(exclude_none=True),
            "provenance": payload.provenance,
            "location_attestation": "device_claim_unverified",
            # Age of the fix at frame time; an old fix is kept visible, never hidden.
            "gnss_fix_age_s": (
                (payload.captured_at - payload.gnss.fixed_at).total_seconds()
                if payload.gnss
                else None
            ),
        },
    )
