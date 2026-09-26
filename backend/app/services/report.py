"""Resposta do UrMind sem LLM (MASTER_PLAN §15).

Jinja2 transforma dado estruturado em texto. O template só pode dizer o que
existe no resultado; o que falta vira "não disponível" ou some da página. Essa é
a camada anti-alucinação do projeto — e ela funciona porque o texto **não é
gerado**, é preenchido.

O caminho é de mão única: `ReportInput` é montado a partir do que já foi
persistido (evento, avaliação de risco, contexto, regra de competência, catálogo
de ações) e o template não tem acesso a nada além disso. Nenhum campo é
inventado aqui, nenhum adjetivo entra sem lastro, e um valor `None` nunca vira
zero, "baixo" ou "provavelmente".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.config import BACKEND_DIR
from app.schemas.core import UrmindClass
from app.schemas.issue_taxonomy import TAXONOMY_VERSION, get_issue, model_may_emit
from app.schemas.public import (
    EventDetailPublic,
    PotentialConsequencePublic,
    ResponsibilityPublic,
    UrbanAnalysisProvenancePublic,
    UrbanAnalysisPublic,
    UrbanIdentificationPublic,
)
from app.services.risk import RiskResult, Severity

__all__ = [
    "ActionSuggestion",
    "ReportInput",
    "ResponsibilitySuggestion",
    "build_urban_analysis",
    "render_event_report",
]


def build_urban_analysis(detail: EventDetailPublic) -> UrbanAnalysisPublic:
    """Render safe public facts; never reassess risk, infer causes or select an agency.

    `assessment_source` is set by the public adapter only for persisted Phase 5
    output. Legacy assessments remain visible elsewhere, but cannot become this
    contract's ordinal assessment. No provider calls or current-time inputs occur.
    """
    issue = get_issue(detail.urmind_class)
    risk = (
        detail.risk
        if (
            detail.risk
            and detail.risk.assessment_source == "phase5"
            and detail.risk.assessment_id
            and detail.risk.assessed_at
            and detail.risk.ruleset_version
        )
        else None
    )
    label = issue.display_name_pt if issue else "Classe não identificada na taxonomia"
    limitations = [
        "Análise descritiva de dados registrados; não substitui inspeção técnica.",
        "A imagem não mede profundidade, dimensões físicas ou extensão real do dano.",
        "Causas não determinadas: não há evidência causal neste contrato.",
        "Consequências potenciais são condicionais, não previsões nem probabilidades.",
    ]
    if issue is None:
        description = "O registro não possui uma classe reconhecida pela taxonomia atual."
        diagnosis = "Diagnóstico indisponível; requer classificação e revisão."
    elif not model_may_emit(issue.issue_code):
        description = f"Categoria registrada: {label}. Não há detector habilitado para esta classe."
        diagnosis = "Categoria candidata; reconhecimento visual automático não disponível."
        limitations.append("Classe sem suporte de modelo; o registro não comprova detecção visual.")
    else:
        description = (
            f"Categoria relatada no registro: {label}. Não atribuída a um modelo."
            if detail.model_version is None
            else f"O registro contém a classificação visual: {label}."
        )
        diagnosis = (
            f"Descrição da categoria na taxonomia: {issue.visual_definition} "
            "A classificação não confirma causa, dimensão física ou gravidade."
        )
    if issue and issue.model_support_status.value == "EXPERIMENTAL_MODEL":
        limitations.append(
            "Classe com suporte experimental; o modelo não está validado para uso operacional."
        )
    if detail.model_version is None:
        limitations.append("Versão do modelo não disponível; origem visual não verificável.")
    if risk is None:
        limitations.append(
            "Avaliação persistida da Fase 5 não disponível; risco e prioridade ausentes."
        )
    else:
        # Free prose in persisted limitations is not scientific evidence.
        limitations.append("Avaliação ordinal registrada; exige revisão técnica independente.")
        if not risk.thresholds_are_calibrated:
            limitations.append("Regras ordinais provisórias, sem calibração de probabilidade.")

    # The domains come only from the stored assessment. Taxonomy applicability
    # is never substituted for observed context or an event impact assessment.
    consequences = [
        PotentialConsequencePublic(
            domain=domain,
            statement=(
                f"Se a ocorrência for confirmada e houver exposição, pode haver impacto "
                f"no domínio {domain}; a ocorrência desse impacto não foi demonstrada."
            ),
        )
        for domain in dict.fromkeys(risk.impact if risk else [])
        if domain in {"safety", "mobility", "accessibility", "environment", "infrastructure"}
    ]
    return UrbanAnalysisPublic(
        identification=UrbanIdentificationPublic(
            issue_code=detail.urmind_class,
            display_name=label,
            family=issue.family.value if issue else None,
            model_support_status=issue.model_support_status.value if issue else None,
            visual_confidence=detail.visual_confidence,
            reviewed=detail.reviewed,
        ),
        description=description,
        diagnosis=diagnosis,
        potential_consequences=consequences,
        possible_causes=[],
        severity=risk.severity
        if risk and risk.severity in {"unknown", "low", "medium", "high", "critical"}
        else None,
        risk_level=risk.risk_level
        if risk and risk.risk_level in {"unknown", "low", "medium", "high", "critical"}
        else None,
        priority_lane=risk.priority_lane
        if risk and risk.priority_lane in {"routine", "elevated", "expedited", "review_required"}
        else None,
        action=detail.action if risk else None,
        responsibility=detail.responsibility
        if risk
        else ResponsibilityPublic(status="requires_triage"),
        responsibility_domain=issue.responsibility_domain if issue else None,
        context=[item for item in detail.context if item.fetched_at],
        limitations=list(dict.fromkeys(limitations)),
        statement_evidence={
            "description": {
                "field": "Event.urmind_class",
                "source": f"Event:{detail.id}",
                "observed_at": detail.occurred_at.isoformat(),
                "kind": "reported" if detail.model_version is None else "inferred",
                "limitation": "Classificação registrada; não comprova causa ou medidas físicas.",
            },
            "diagnosis": {
                "field": "IssueDefinition.visual_definition",
                "source": TAXONOMY_VERSION,
                "observed_at": detail.occurred_at.isoformat(),
                "time_semantics": "event time; taxonomy version is the immutable rule reference",
                "kind": "rule",
                "limitation": "Definição de categoria; não é inspeção nem observação do evento.",
            },
            **{
                name: {
                    "field": field_name,
                    "source": f"Event:{detail.id}",
                    "observed_at": detail.occurred_at.isoformat(),
                    "kind": "reported",
                    "time_semantics": "event timestamp, not time of review or catalog publication",
                    "limitation": "Recorded values; review existence does not imply confirmation. Taxonomy fields are rules, not observations.",
                }
                for name, field_name in {
                    "identification": "Event.urmind_class, Event.visual_confidence; IssueDefinition; Review existence",
                    "provenance": "Event.model_version_id; ModelVersion.version; Capture.quality.inference",
                    "responsibility_domain": "IssueDefinition.responsibility_domain",
                }.items()
            },
            **{
                f"context.{index}": {
                    "field": "EventContext.payload",
                    "source": item.source,
                    "observed_at": item.fetched_at.isoformat() if item.fetched_at else None,
                    "kind": "reported",
                    "limitation": "Provider summary, not a diagnosis.",
                }
                for index, item in enumerate(item for item in detail.context if item.fetched_at)
            },
            **(
                {
                    name: {
                        "field": field_name,
                        "source": f"RiskAssessment:{risk.assessment_id}",
                        "observed_at": risk.assessed_at.isoformat() if risk.assessed_at else None,
                        "kind": "rule",
                        "ruleset_version": risk.ruleset_version,
                        "limitation": "Persisted ordinal rule or catalog selection; not independently validated scientific truth.",
                    }
                    for name, field_name in {
                        "severity": "RiskAssessment.severity",
                        "risk_level": "RiskAssessment.factors.phase5.risk.ordinal_level",
                        "priority_lane": "RiskAssessment.factors.phase5.priority.attention_lane",
                        "action": "RiskAssessment.action_id -> actions_catalog",
                        "responsibility": "RiskAssessment.responsibility_rule_id -> responsibility_rules",
                        "potential_consequences": "RiskAssessment.factors.phase5.impact.potential_domains",
                    }.items()
                }
                if risk
                else {}
            ),
            **(
                {
                    "assessment": {
                        "field": "RiskAssessment",
                        "source": f"RiskAssessment:{risk.assessment_id}",
                        "observed_at": risk.assessed_at.isoformat() if risk.assessed_at else None,
                        "kind": "rule",
                        "ruleset_version": risk.ruleset_version,
                        "limitation": "Regra ordinal; não representa probabilidade nem urgência técnica comprovada.",
                    }
                }
                if risk
                else {}
            ),
        },
        provenance=UrbanAnalysisProvenancePublic(
            taxonomy_version=TAXONOMY_VERSION,
            model_version=detail.model_version,
            model_stage=detail.model_stage,
            dataset_version=detail.dataset_version,
            ruleset_version=risk.ruleset_version if risk else None,
            assessed_at=risk.assessed_at if risk else None,
            assessment_source="persisted_phase5" if risk else "unavailable",
        ),
    )


TEMPLATES_DIR = BACKEND_DIR / "app" / "templates"
TEMPLATE_NAME = "event_report_pt_br.j2"

# Rótulos legíveis da taxonomia (§8.2). Descrição do defeito, não julgamento.
CLASS_LABELS: dict[UrmindClass, str] = {
    UrmindClass.ROAD_D00: "trinca longitudinal no pavimento",
    UrmindClass.ROAD_D10: "trinca transversal no pavimento",
    UrmindClass.ROAD_D20: "trinca em malha no pavimento",
    UrmindClass.ROAD_D40: "buraco no pavimento",
    UrmindClass.MANHOLE: "tampa de poço de visita",
    UrmindClass.SIDEWALK: "calçada danificada",
    UrmindClass.SIGNAGE: "problema de sinalização",
    UrmindClass.UNKNOWN: "não classificado pelo sistema",
}

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.UNKNOWN: "não determinada",
    Severity.LOW: "baixa",
    Severity.MEDIUM: "média",
    Severity.HIGH: "alta",
    Severity.CRITICAL: "crítica",
}


@dataclass(frozen=True)
class ResponsibilitySuggestion:
    """Saída de `responsibility_rules` (§14.3). Vem de tabela, nunca de modelo."""

    responsible: str
    source: str
    """Fundamento da competência: lei, decreto, contrato ou norma que a sustenta."""

    version: str = "v1"


@dataclass(frozen=True)
class ActionSuggestion:
    """Item do `actions_catalog` (§14.4). Sempre sugestão, nunca determinação."""

    code: str
    label: str
    version: str = "v1"


@dataclass(frozen=True)
class EvidenceReference:
    """A structured reference, not a channel for arbitrary factual prose."""

    field: str
    source: str
    observed_at: datetime
    kind: Literal["observed", "reported", "inferred", "rule"]
    limitation: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.field)
            or not self.source.strip()
            or not self.limitation.strip()
            or self.observed_at.tzinfo is None
            or self.kind not in {"observed", "reported", "inferred", "rule"}
            or not re.fullmatch(
                r"(?:Event|Capture|RoadSegment|RiskAssessment|actions_catalog|responsibility_rules):[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.]+)?",
                self.source,
            )
        ):
            raise ValueError("INCOMPLETE_EVIDENCE_REFERENCE")


@dataclass(frozen=True)
class ReportInput:
    """Tudo que o texto pode mencionar. O template não enxerga nada além disto."""

    event_key: str
    urmind_class: UrmindClass
    risk: RiskResult

    visual_confidence: float | None = None
    model_version: str | None = None

    latitude: float | None = None
    longitude: float | None = None
    location_accuracy_m: float | None = None
    location_source: str | None = None
    road_segment_name: str | None = None
    distance_to_road_m: float | None = None
    address: str | None = None
    address_from_osm: bool = False

    evidence: list[EvidenceReference] = field(default_factory=list)
    provenance: dict[str, EvidenceReference] = field(default_factory=dict)
    responsibility: ResponsibilitySuggestion | None = None
    action: ActionSuggestion | None = None
    predictions: list[str] = field(default_factory=list)
    extra_limitations: list[str] = field(default_factory=list)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        # StrictUndefined: variável não declarada estoura em vez de virar string
        # vazia. Um campo esquecido tem que quebrar o teste, não sumir do texto.
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_event_report(payload: ReportInput) -> str:
    """Monta o relatório em texto a partir do que existe. Nada além disso."""
    if (
        payload.predictions
        or payload.extra_limitations
        or any(not isinstance(item, EvidenceReference) for item in payload.evidence)
    ):
        raise ValueError("UNSUPPORTED_FREE_TEXT")
    required = {"event_key", "urmind_class", "risk"}
    required.update(
        name
        for name in (
            "visual_confidence",
            "model_version",
            "latitude",
            "longitude",
            "location_accuracy_m",
            "location_source",
            "road_segment_name",
            "distance_to_road_m",
            "address",
            "responsibility",
            "action",
        )
        if getattr(payload, name) is not None
    )
    if any(
        not isinstance(payload.provenance.get(name), EvidenceReference)
        or payload.provenance[name].field != name
        for name in required
    ):
        raise ValueError("MISSING_FACT_PROVENANCE")
    risk = payload.risk

    limitations = [
        "Valores registrados e regras ordinais; exigem revisão humana. Não comprovam causa, medidas físicas ou probabilidade."
    ]
    if risk.coverage is not None and risk.coverage < 1:
        limitations.append(
            "prioridade calculada sem os fatores indisponíveis; cobertura incompleta"
        )
    if payload.model_version is None:
        limitations.append(
            "sem versão de modelo registrada: o resultado não pode ser atribuído "
            "a um detector promovido"
        )
    if payload.latitude is None or payload.longitude is None:
        limitations.append("sem coordenada, o evento não é georreferenciável e não entra no mapa")
    if payload.address is not None:
        limitations.append(
            "o endereço é aproximado: vem do objeto mais próximo na base cartográfica, "
            "não é a fonte da coordenada"
        )

    context = {
        "event_key": json.dumps(payload.event_key, ensure_ascii=False),
        "class_label": CLASS_LABELS.get(payload.urmind_class, payload.urmind_class.value),
        "visual_confidence": payload.visual_confidence,
        "model_version": json.dumps(payload.model_version, ensure_ascii=False)
        if payload.model_version
        else None,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "location_accuracy_m": payload.location_accuracy_m,
        "location_source": json.dumps(payload.location_source, ensure_ascii=False)
        if payload.location_source
        else None,
        "road_segment_name": json.dumps(payload.road_segment_name, ensure_ascii=False)
        if payload.road_segment_name
        else None,
        "distance_to_road_m": payload.distance_to_road_m,
        "address": json.dumps(payload.address, ensure_ascii=False) if payload.address else None,
        "osm_attribution": payload.address_from_osm or payload.road_segment_name is not None,
        "evidence": [
            f"Referência registrada: {item.field}; fonte={item.source}; "
            f"instante={item.observed_at.isoformat()}; tipo={item.kind}; "
            f"limitação=registered reference; no independent causal diagnosis"
            for item in payload.evidence
        ],
        "severity_label": SEVERITY_LABELS[risk.severity],
        "priority_score": risk.priority_score,
        "uncertainty": risk.uncertainty,
        "coverage": risk.coverage,
        "ruleset_version": json.dumps(risk.ruleset_version, ensure_ascii=False),
        "responsible": "Valor relatado no catálogo: "
        + json.dumps(payload.responsibility.responsible, ensure_ascii=False)
        if payload.responsibility
        else None,
        "responsibility_source": "Referência textual do catálogo (não verificada): "
        + json.dumps(payload.responsibility.source, ensure_ascii=False)
        if payload.responsibility
        else None,
        "responsibility_version": (
            json.dumps(payload.responsibility.version, ensure_ascii=False)
            if payload.responsibility
            else None
        ),
        "action_code": json.dumps(payload.action.code, ensure_ascii=False)
        if payload.action
        else None,
        "action_label": "Sugestão registrada no catálogo, sujeita a revisão: "
        + json.dumps(payload.action.label, ensure_ascii=False)
        if payload.action
        else None,
        "action_version": json.dumps(payload.action.version, ensure_ascii=False)
        if payload.action
        else None,
        "predictions": payload.predictions,
        "limitations": limitations,
    }

    template = _environment().get_template(TEMPLATE_NAME)
    rendered = template.render(report=context)
    references = "\n".join(
        f"{name}: fonte={ref.source}; instante={ref.observed_at.isoformat()}; "
        f"tipo={ref.kind}; limitação=registered value; no independent causal diagnosis"
        for name, ref in sorted(payload.provenance.items())
        if name in required
    )
    return rendered + "\nPROVENIÊNCIA POR CAMPO\n" + references + "\n"
