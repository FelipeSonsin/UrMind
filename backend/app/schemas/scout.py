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

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.core import Coordinate


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
