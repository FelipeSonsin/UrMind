"""Contratos do painel público (MASTER_PLAN §17, §27 do escopo público).

Fronteira de segurança: nada de ORM serializado direto. Cada campo exposto está
declarado aqui, e o que não está declarado não sai. Fora daqui ficam identificador
de usuário, revisor, caminho privado no Storage, auditoria, configuração e erro
interno.

Campo ausente é `None` com motivo declarado — o painel público mostra "não
disponível", nunca um valor inventado.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.issue_taxonomy import ResponsibilityDomain


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImageAvailability(PublicModel):
    """Imagem só é publicada depois de sanitização registrada (§17)."""

    available: bool
    privacy_redacted: bool
    reason: str | None = None
    url: str | None = None


class DetectionPublic(PublicModel):
    urmind_class: str
    confidence: float
    bbox: dict[str, float] | None = None
    """Só acompanha a imagem: sem imagem publicada, a caixa não tem sobre o que ser desenhada."""


class RoadPublic(PublicModel):
    name: str | None
    highway: str | None
    distance_m: float | None
    jurisdiction: str | None


class RiskFactorPublic(PublicModel):
    factor: str
    label: str
    value: float | None = None
    weight: float | None = None
    delta: float | None = None
    reason: str | None = None


class RiskExplanationPublic(PublicModel):
    increased: list[RiskFactorPublic] = Field(default_factory=list)
    decreased: list[RiskFactorPublic] = Field(default_factory=list)
    unavailable: list[RiskFactorPublic] = Field(default_factory=list)
    baseline: float | None = None


class RiskPublic(PublicModel):
    assessment_source: Literal["phase5", "legacy"] | None = None
    severity: str
    priority_score: float | None
    impact: list[str] = Field(default_factory=list)
    risk_level: str | None = None
    priority_lane: str | None = None
    uncertainty: float | None
    uncertainty_band: str
    coverage: float | None
    ruleset_version: str | None
    thresholds_are_calibrated: bool
    explanation: RiskExplanationPublic
    limitations: list[str] = Field(default_factory=list)
    assessed_at: datetime | None = None


class ActionPublic(PublicModel):
    code: str
    label: str
    version: str


class ResponsibilityPublic(PublicModel):
    status: Literal["assigned", "requires_triage"]
    responsible: str | None = None
    source: str | None = None
    version: str | None = None
    note: str | None = None


class ContextSourcePublic(PublicModel):
    source: str
    label: str
    status: str
    fetched_at: datetime | None = None
    attribution: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    """Resumo já filtrado pelo backend; nunca o payload cru do provider."""


class PredictionPublic(PublicModel):
    """Previsão só existe quando há `predictions` real (§24). RiskAssessment não é previsão."""

    available: bool
    reason: str | None = None
    task: str | None = None
    horizon_days: int | None = None
    value: float | None = None
    uncertainty: float | None = None
    model_version: str | None = None
    generated_at: datetime | None = None


class TraceStep(PublicModel):
    step: Literal["capture", "detection", "context", "risk", "decision", "action"]
    status: Literal["done", "unavailable"]
    title: str
    source: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime | None = None


class EventSummaryPublic(PublicModel):
    id: uuid.UUID
    occurred_at: datetime
    urmind_class: str
    status: str
    evidence_mode: str
    visual_confidence: float | None
    severity: str | None
    priority_score: float | None
    risk_level: str | None = None
    priority_lane: str | None = None
    latitude: float | None
    longitude: float | None
    snapped_latitude: float | None
    snapped_longitude: float | None
    road_name: str | None


class UrbanIdentificationPublic(PublicModel):
    issue_code: str
    display_name: str
    family: str | None
    model_support_status: str | None
    visual_confidence: float | None
    reviewed: bool


class PotentialConsequencePublic(PublicModel):
    domain: str
    statement: str
    conditional: Literal[True] = True
    source: Literal["persisted_phase5"] = "persisted_phase5"


class UrbanAnalysisProvenancePublic(PublicModel):
    taxonomy_version: str
    model_version: str | None
    model_stage: str | None
    dataset_version: str | None
    ruleset_version: str | None
    assessed_at: datetime | None
    assessment_source: Literal["persisted_phase5", "unavailable"]
    method: Literal["deterministic_template"] = "deterministic_template"


class UrbanAnalysisPublic(PublicModel):
    schema_version: Literal["urmind-urban-analysis-v1"] = "urmind-urban-analysis-v1"
    identification: UrbanIdentificationPublic
    description: str
    diagnosis: str
    potential_consequences: list[PotentialConsequencePublic] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    severity: str | None
    risk_level: str | None
    priority_lane: str | None
    action: ActionPublic | None
    responsibility: ResponsibilityPublic
    responsibility_domain: ResponsibilityDomain | None
    context: list[ContextSourcePublic] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: UrbanAnalysisProvenancePublic


class EventDetailPublic(EventSummaryPublic):
    analysis: UrbanAnalysisPublic | None = None
    distance_to_road_m: float | None
    location_accuracy_m: float | None
    road: RoadPublic | None
    detections: list[DetectionPublic] = Field(default_factory=list)
    image: ImageAvailability
    risk: RiskPublic | None
    action: ActionPublic | None
    responsibility: ResponsibilityPublic
    context: list[ContextSourcePublic] = Field(default_factory=list)
    prediction: PredictionPublic
    trace: list[TraceStep] = Field(default_factory=list)
    model_version: str | None
    model_stage: str | None
    dataset_version: str | None
    reviewed: bool
    """Houve revisão humana. Quem revisou não é público."""


class ScoutCameraPublic(PublicModel):
    mode: Literal["live_video", "live_snapshots", "unavailable"]
    reason: str | None = None
    stream_url: str | None = None
    frame_url: str | None = None
    latency_ms: float | None = None


class ScoutPublic(PublicModel):
    status: Literal["live", "degraded", "offline", "no_device"]
    device_code: str | None = None
    last_seen: datetime | None = None
    camera: ScoutCameraPublic
    telemetry: dict[str, Any] = Field(default_factory=dict)
    """Só sensores que existirem de verdade. Sem sensor, a chave não aparece."""

    mission: str | None = None


class ComponentStatus(PublicModel):
    name: str
    status: Literal["ok", "degraded", "unavailable"]
    detail: str | None = None


class StatusPublic(PublicModel):
    api: ComponentStatus
    database: ComponentStatus
    detector: ComponentStatus
    scout: ComponentStatus
    last_event_at: datetime | None = None
    events_total: int
    road_segments_total: int
    pilot_area: str | None = None
    checked_at: datetime


class ModelMetricsPublic(PublicModel):
    map50: float | None = None
    map50_95: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    per_class: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    not_computed: list[str] = Field(default_factory=list)
    samples: int | None = None


class ModelLatencyPublic(PublicModel):
    mean_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    fps_approx: float | None = None
    execution_provider: str | None = None
    hardware: str | None = None


class TransparencyPublic(PublicModel):
    model_name: str | None
    model_version: str | None
    stage: str | None
    stage_note: str | None
    classes: list[str] = Field(default_factory=list)
    input_size: list[int] = Field(default_factory=list)
    score_threshold: float | None = None
    metrics: ModelMetricsPublic
    latency: ModelLatencyPublic
    dataset_name: str | None = None
    dataset_version: str | None = None
    dataset_license: str | None = None
    dataset_source: str | None = None
    context_sources: list[dict[str, str]] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class IssueTaxonomyEntryPublic(PublicModel):
    """Classe versionada; `model_may_emit=False` nunca é "IA já reconhece"."""

    issue_code: str
    taxonomy_version: str
    family: str
    display_name_pt: str
    display_name_en: str
    description: str
    visual_definition: str
    included_examples: list[str]
    excluded_examples: list[str]
    model_support_status: str
    dataset_status: str
    review_status: str
    responsibility_domain: ResponsibilityDomain
    legacy_responsibility_domain: str
    possible_impact_domains: list[str]
    applicable_context_features: list[str]
    version: int
    related_legacy_codes: list[str]
    model_may_emit: bool
    risk_groups: list[str] = Field(default_factory=list)
    photo_detectable: bool | Literal["limited"] = "limited"
    limitations: list[str] = Field(default_factory=list)
    triage_priority_hint: str | None = None


class IssueTaxonomyPublic(PublicModel):
    taxonomy_version: str
    issues: list[IssueTaxonomyEntryPublic]
