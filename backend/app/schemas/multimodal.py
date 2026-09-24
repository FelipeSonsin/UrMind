"""Contrato `urmind-multimodal-observation-v1` (Fase 12 — contrato, sem modelo).

Não existe modelo multimodal (§22): ele só pode ser treinado com eventos reais
pareados. Este contrato fixa o formato para o FeatureBuilder evoluir sem
quebrar o modo foto-only atual:

- cada modalidade é uma observação com fonte, instante, captura/dispositivo,
  qualidade, estado de ausência e proveniência;
- modalidade ausente é `MISSING` com motivo, nunca zero ou valor preenchido;
- a classe urbana continua visual: IMU e áudio nunca classificam sozinhos.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MULTIMODAL_CONTRACT_VERSION = "urmind-multimodal-observation-v1"


class ObservationKind(StrEnum):
    VISUAL = "visual"
    LOCATION = "location"
    IMU = "imu"
    AUDIO = "audio"
    CONTEXT = "context"


class Missingness(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"  # modality not captured at all
    UNAVAILABLE = "UNAVAILABLE"  # captured but failed/unusable


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ObservationKind
    source: str = Field(min_length=1)
    observed_at: datetime | None = None
    capture_id: str | None = None
    device_id: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    missingness: Missingness
    missing_reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def missing_means_empty(self) -> Observation:
        if self.missingness is Missingness.PRESENT:
            if self.observed_at is None or self.observed_at.tzinfo is None:
                raise ValueError("observação presente exige observed_at com fuso")
            if self.missing_reason is not None:
                raise ValueError("observação presente não tem missing_reason")
        else:
            if not self.missing_reason:
                raise ValueError("ausência exige motivo declarado")
            if self.payload:
                raise ValueError("observação ausente não carrega valores")
        return self


class VisualObservation(Observation):
    kind: ObservationKind = ObservationKind.VISUAL


class LocationObservation(Observation):
    kind: ObservationKind = ObservationKind.LOCATION


class IMUObservation(Observation):
    kind: ObservationKind = ObservationKind.IMU


class AudioObservation(Observation):
    kind: ObservationKind = ObservationKind.AUDIO

    @model_validator(mode="after")
    def no_uncalibrated_spl(self) -> AudioObservation:
        if "db_spl" in self.payload and not self.quality.get("spl_calibrated"):
            raise ValueError("dB SPL absoluto exige calibração metrológica (§21.4)")
        return self


class ContextObservation(Observation):
    kind: ObservationKind = ObservationKind.CONTEXT


class MultimodalObservationSet(BaseModel):
    """Todas as modalidades de um evento. A visual é obrigatória: define a classe."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(
        default=MULTIMODAL_CONTRACT_VERSION, pattern=r"^urmind-multimodal-observation-v1$"
    )
    visual: VisualObservation
    location: LocationObservation
    imu: IMUObservation
    audio: AudioObservation
    context: ContextObservation

    @model_validator(mode="after")
    def visual_defines_the_class(self) -> MultimodalObservationSet:
        if self.visual.missingness is not Missingness.PRESENT:
            for other in (self.imu, self.audio):
                if other.missingness is Missingness.PRESENT:
                    raise ValueError("IMU/áudio não afirmam classe sem evidência visual (§22)")
        return self

    @property
    def mode(self) -> str:
        present = [
            name
            for name in ("imu", "audio")
            if getattr(self, name).missingness is Missingness.PRESENT
        ]
        return "photo_only" if not present else "camera+" + "+".join(present)


def photo_only_observations(
    *,
    capture_id: str,
    captured_at: datetime,
    visual_quality: dict[str, Any],
    location: dict[str, Any] | None,
    context_status: dict[str, Any] | None = None,
) -> MultimodalObservationSet:
    """The current foto-first path expressed in the multimodal contract."""
    return MultimodalObservationSet(
        visual=VisualObservation(
            source="phone_camera",
            observed_at=captured_at,
            capture_id=capture_id,
            quality=visual_quality,
            missingness=Missingness.PRESENT,
        ),
        location=LocationObservation(
            source=str(location.get("location_source", "unknown")),
            observed_at=captured_at,
            capture_id=capture_id,
            missingness=Missingness.PRESENT,
            payload=location,
        )
        if location
        else LocationObservation(
            source="none",
            capture_id=capture_id,
            missingness=Missingness.MISSING,
            missing_reason="captura sem localização",
        ),
        imu=IMUObservation(
            source="none",
            capture_id=capture_id,
            missingness=Missingness.MISSING,
            missing_reason="foto de celular: sem IMU sincronizado",
        ),
        audio=AudioObservation(
            source="none",
            capture_id=capture_id,
            missingness=Missingness.MISSING,
            missing_reason="foto de celular: sem áudio",
        ),
        context=ContextObservation(
            source="event_context",
            observed_at=captured_at,
            capture_id=capture_id,
            missingness=Missingness.PRESENT,
            payload=context_status,
        )
        if context_status
        else ContextObservation(
            source="event_context",
            capture_id=capture_id,
            missingness=Missingness.UNAVAILABLE,
            missing_reason="contexto externo indisponível",
        ),
    )
