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

from dataclasses import dataclass, field

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.config import BACKEND_DIR
from app.schemas.core import UrmindClass
from app.schemas.issue_taxonomy import TAXONOMY_VERSION, get_issue, model_may_emit
from app.schemas.public import (
    EventDetailPublic,
    PotentialConsequencePublic,
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
    risk = detail.risk if detail.risk and detail.risk.assessment_source == "phase5" else None
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
        description = f"O registro contém a classificação visual: {label}."
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
        limitations.extend(risk.limitations)
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
        severity=risk.severity if risk else None,
        risk_level=risk.risk_level if risk else None,
        priority_lane=risk.priority_lane if risk else None,
        action=detail.action,
        responsibility=detail.responsibility,
        responsibility_domain=issue.responsibility_domain if issue else None,
        context=detail.context,
        limitations=list(dict.fromkeys(limitations)),
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
    UrmindClass.ROAD_D20: "trinca em malha (couro de jacaré) no pavimento",
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

    evidence: list[str] = field(default_factory=list)
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
    risk = payload.risk

    limitations = list(risk.limitations) + list(payload.extra_limitations)
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
        "event_key": payload.event_key,
        "class_label": CLASS_LABELS.get(payload.urmind_class, payload.urmind_class.value),
        "visual_confidence": payload.visual_confidence,
        "model_version": payload.model_version,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "location_accuracy_m": payload.location_accuracy_m,
        "location_source": payload.location_source,
        "road_segment_name": payload.road_segment_name,
        "distance_to_road_m": payload.distance_to_road_m,
        "address": payload.address,
        "osm_attribution": payload.address_from_osm or payload.road_segment_name is not None,
        "evidence": payload.evidence,
        "severity_label": SEVERITY_LABELS[risk.severity],
        "priority_score": risk.priority_score,
        "uncertainty": risk.uncertainty,
        "coverage": risk.coverage,
        "ruleset_version": risk.ruleset_version,
        "responsible": payload.responsibility.responsible if payload.responsibility else None,
        "responsibility_source": payload.responsibility.source if payload.responsibility else None,
        "responsibility_version": (
            payload.responsibility.version if payload.responsibility else None
        ),
        "action_code": payload.action.code if payload.action else None,
        "action_label": payload.action.label if payload.action else None,
        "action_version": payload.action.version if payload.action else None,
        "predictions": payload.predictions,
        "limitations": limitations,
    }

    template = _environment().get_template(TEMPLATE_NAME)
    return template.render(report=context)
