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
from app.services.risk import RiskResult, Severity

__all__ = ["ActionSuggestion", "ReportInput", "ResponsibilitySuggestion", "render_event_report"]

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
        limitations.append(
            "sem coordenada, o evento não é georreferenciável e não entra no mapa"
        )
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
