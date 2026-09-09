"""Severidade e prioridade por regras versionadas (MASTER_PLAN §14.1 e §14.2).

Por que regras e não ML: o §14.2 é explícito — ainda não existe histórico de
"prioridade correta" suficiente para treinar um modelo com honestidade. Treinar
agora produziria um número com aparência de ciência e lastro nenhum. Então a V1
usa regras transparentes, versionadas e auditáveis, e cada resultado sai com a
conta aberta em `factors`, de forma que um humano possa discordar item a item.

Três limites que este módulo respeita e que valem mais que o resultado:

1. **Não existe centímetro a partir de pixel.** Sem calibração e sem depth, a
   área da bounding box mede o quanto o defeito ocupa do quadro, não o tamanho
   dele no mundo — um buraco pequeno de perto ocupa mais quadro que um grande de
   longe. Por isso a grandeza se chama `apparent_extent` e nunca vira medida
   física (§14.1, §27).

2. **Foto sozinha não emite `critical`.** Criticidade depende de evidência
   física que a fase foto-only não tem: profundidade real, impacto no IMU,
   inspeção. O teto do caminho visual é `high`; `critical` fica reservado para
   quando o Scout existir e o IMU estiver calibrado, ou para escalonamento por
   revisão humana (§14.1, §21.3).

3. **Fator ausente é ausente.** Contexto externo pode faltar (§13). Um fator sem
   dado não entra como zero — ele sai da conta, e o peso dele sai junto. O que
   sobra é registrado em `coverage`, para ninguém confundir "prioridade baixa"
   com "quase não sei nada sobre este evento".

Sobre desigualdade (§14.2): este ruleset **não recebe indicador socioeconômico
algum**. A regra do planejamento é que esses indicadores nunca reduzam
atendimento de área vulnerável; a forma mais segura de garantir isso na V1 é não
deixar essa variável entrar. Se um dia entrar, só poderá somar prioridade, nunca
subtrair — e há teste travando isso.

Os números aqui são **declarados, não aprendidos**. Estão reunidos em
`PROVISIONAL_THRESHOLDS` porque o §31.16 exige que limiar nasça de medição:
todos precisam ser recalibrados contra dados reais do piloto antes de qualquer
uso operacional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.schemas.core import EvidenceMode, UrmindClass

__all__ = [
    "PROVISIONAL_THRESHOLDS",
    "RULESET_VERSION",
    "ContextInput",
    "RiskInput",
    "RiskResult",
    "Severity",
    "assess",
]

RULESET_VERSION = "urmind-risk-v1"
"""Versão do conjunto de regras. Muda junto com qualquer peso ou limiar.

Vai gravada em `risk_assessments.factors` para que um resultado antigo continue
explicável depois que as regras mudarem.
"""


class Severity(StrEnum):
    """Valores aceitos por `risk_assessments.severity`."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER = (
    Severity.UNKNOWN,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)

# Severidade específica da classe (§14.1). O critério é o risco que o defeito
# oferece a quem passa, não a aparência dele na foto.
_BASE_SEVERITY: dict[UrmindClass, Severity] = {
    # Buraco: risco direto a moto e bicicleta, e o que mais gera dano a veículo.
    UrmindClass.ROAD_D40: Severity.HIGH,
    # Trinca em malha: fadiga estrutural, costuma virar buraco.
    UrmindClass.ROAD_D20: Severity.MEDIUM,
    # Trincas lineares: degradação inicial, sem risco imediato de trafegabilidade.
    UrmindClass.ROAD_D00: Severity.LOW,
    UrmindClass.ROAD_D10: Severity.LOW,
    # Classes V1.5/V2 (§0, §8.2): já mapeadas, mas só valem quando houver dataset.
    UrmindClass.MANHOLE: Severity.HIGH,
    UrmindClass.SIDEWALK: Severity.MEDIUM,
    UrmindClass.SIGNAGE: Severity.MEDIUM,
}

_SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.LOW: 0.25,
    Severity.MEDIUM: 0.50,
    Severity.HIGH: 0.75,
    Severity.CRITICAL: 1.0,
}

# Pesos da prioridade. Somam 1,0 quando todos os fatores estão disponíveis; com
# fatores ausentes, a soma é renormalizada pelos que sobraram.
_WEIGHTS: dict[str, float] = {
    "severity": 0.40,
    "confidence": 0.15,
    "sensitive_proximity": 0.15,
    "accessibility": 0.10,
    "recurrence": 0.10,
    "environment": 0.10,
}

PROVISIONAL_THRESHOLDS: dict[str, float] = {
    # Confiança mínima para a detecção sustentar a severidade base da classe.
    # Abaixo disso a severidade é rebaixada um nível, porque não se agrava um
    # diagnóstico em cima de uma detecção fraca.
    "min_confidence_for_base_severity": 0.60,
    # Fração do quadro ocupada pelo defeito a partir da qual ele é tratado como
    # extenso. NÃO é medida física — ver docstring do módulo.
    "large_apparent_extent": 0.15,
    # Quantidade de detecções da mesma classe no mesmo quadro que caracteriza
    # trecho degradado em vez de defeito pontual.
    "many_detections": 3,
    # Distância a equipamento sensível: até `_near` conta integral, a partir de
    # `_far` não conta. Entre as duas, decai linearmente.
    "sensitive_near_m": 50.0,
    "sensitive_far_m": 300.0,
    # Recorrências no mesmo trecho que saturam o fator.
    "recurrence_saturation": 5,
    # Chuva acumulada em 24 h que satura o fator ambiental (mm).
    "rain_saturation_mm": 20.0,
}
"""Limiares declarados, ainda não medidos.

O §31.16 é claro: limiar nasce de baseline, não de suposição. Nenhum destes foi
calibrado contra dado real — eles existem para o sistema ter comportamento
definido e testável agora, e devem ser revistos no piloto antes de qualquer
decisão operacional.
"""


@dataclass(frozen=True)
class ContextInput:
    """Contexto externo do evento (§13). `None` significa indisponível, não zero."""

    distance_to_school_m: float | None = None
    distance_to_health_m: float | None = None
    distance_to_crossing_m: float | None = None
    affects_accessibility: bool | None = None
    recurrence_on_segment: int | None = None
    rain_mm_24h: float | None = None

    @property
    def nearest_sensitive_m(self) -> float | None:
        """Menor distância entre escola, unidade de saúde e travessia."""
        distances = [
            value
            for value in (
                self.distance_to_school_m,
                self.distance_to_health_m,
                self.distance_to_crossing_m,
            )
            if value is not None and value >= 0
        ]
        return min(distances) if distances else None


@dataclass(frozen=True)
class RiskInput:
    """Tudo que o motor pode olhar. Nada além disto influencia o resultado."""

    urmind_class: UrmindClass
    evidence_mode: EvidenceMode
    visual_confidence: float | None = None
    apparent_extent: float | None = None
    """Fração do quadro ocupada pela maior detecção (0–1). Não é tamanho real."""

    detection_count: int = 0
    location_accuracy_m: float | None = None
    context: ContextInput = field(default_factory=ContextInput)


@dataclass(frozen=True)
class RiskResult:
    """Resultado auditável: o número vem sempre acompanhado da conta."""

    severity: Severity
    priority_score: float | None
    uncertainty: float
    coverage: float
    """Fração do peso total que tinha dado disponível. Baixa = pouco embasamento."""

    factors: dict[str, Any]
    limitations: list[str]
    ruleset_version: str = RULESET_VERSION

    def as_persisted(self) -> dict[str, Any]:
        """Formato para `risk_assessments` (severity, priority_score, factors)."""
        return {
            "severity": self.severity.value,
            "priority_score": self.priority_score,
            "uncertainty": self.uncertainty,
            "factors": {
                "ruleset_version": self.ruleset_version,
                "coverage": self.coverage,
                "limitations": self.limitations,
                **self.factors,
            },
        }


def _shift_severity(severity: Severity, steps: int) -> Severity:
    """Move a severidade na escala, sem sair dela."""
    if severity is Severity.UNKNOWN:
        return severity
    index = _SEVERITY_ORDER.index(severity)
    new_index = max(1, min(len(_SEVERITY_ORDER) - 1, index + steps))
    return _SEVERITY_ORDER[new_index]


def _linear_decay(value: float, near: float, far: float) -> float:
    """1,0 até `near`; 0,0 a partir de `far`; linear no meio."""
    if value <= near:
        return 1.0
    if value >= far:
        return 0.0
    return (far - value) / (far - near)


def _severity_from_visual(payload: RiskInput, limitations: list[str]) -> tuple[Severity, dict]:
    """Severidade da classe, ajustada só pelo que a imagem sustenta (§14.1)."""
    detail: dict[str, Any] = {}

    base = _BASE_SEVERITY.get(payload.urmind_class)
    if base is None:
        # URMIND_UNKNOWN é estado do sistema, não classe (§8.2/§31.8): sem classe
        # conhecida não há severidade específica de classe para afirmar.
        limitations.append(
            "classe não reconhecida pela taxonomia V1; severidade não pode ser afirmada"
        )
        return Severity.UNKNOWN, {"base_severity": None}

    detail["base_severity"] = base.value
    severity = base

    confidence = payload.visual_confidence
    if confidence is None:
        limitations.append("detecção sem confiança declarada; severidade mantida na base")
    elif confidence < PROVISIONAL_THRESHOLDS["min_confidence_for_base_severity"]:
        severity = _shift_severity(severity, -1)
        detail["downgraded_by_low_confidence"] = True
        limitations.append(
            "confiança visual abaixo do limiar; severidade rebaixada um nível"
        )

    # Agravantes só se a detecção for confiável — não se agrava no escuro.
    confident = confidence is not None and (
        confidence >= PROVISIONAL_THRESHOLDS["min_confidence_for_base_severity"]
    )
    if confident:
        extent = payload.apparent_extent
        if extent is not None and extent >= PROVISIONAL_THRESHOLDS["large_apparent_extent"]:
            severity = _shift_severity(severity, 1)
            detail["upgraded_by_apparent_extent"] = extent
        if payload.detection_count >= PROVISIONAL_THRESHOLDS["many_detections"]:
            severity = _shift_severity(severity, 1)
            detail["upgraded_by_detection_count"] = payload.detection_count

    # Teto do caminho visual: `critical` exige evidência física (§14.1).
    if severity is Severity.CRITICAL:
        severity = Severity.HIGH
        detail["capped_at_high"] = True
        limitations.append(
            "severidade limitada a 'high': criticidade exige evidência física "
            "(profundidade medida ou impacto de IMU calibrado), indisponível na fase foto"
        )

    if payload.apparent_extent is not None:
        detail["apparent_extent"] = payload.apparent_extent
        detail["apparent_extent_is_physical_measure"] = False

    return severity, detail


def assess(payload: RiskInput) -> RiskResult:
    """Calcula severidade, prioridade e incerteza a partir do que existe.

    Nunca levanta exceção por dado faltando: a ausência é o resultado.
    """
    limitations: list[str] = []

    if payload.evidence_mode is EvidenceMode.SENSOR_ONLY:
        # Sensor não afirma classe visual (§31.9); sem classe não há severidade.
        limitations.append(
            "evidência apenas de sensor; classe visual não pode ser afirmada, "
            "portanto não há severidade específica de classe"
        )
        return RiskResult(
            severity=Severity.UNKNOWN,
            priority_score=None,
            uncertainty=1.0,
            coverage=0.0,
            factors={"evidence_mode": payload.evidence_mode.value},
            limitations=limitations,
        )

    severity, severity_detail = _severity_from_visual(payload, limitations)

    if severity is Severity.UNKNOWN:
        # A prioridade do §14.2 parte de classe e severidade. Sem classe
        # afirmável não existe nota a dar — e um número aqui seria exatamente o
        # tipo de precisão fabricada que o §27 proíbe. Vai para triagem humana.
        limitations.append(
            "sem severidade de classe, a prioridade não é calculada; requer triagem"
        )
        return RiskResult(
            severity=Severity.UNKNOWN,
            priority_score=None,
            uncertainty=1.0,
            coverage=0.0,
            factors={
                "evidence_mode": payload.evidence_mode.value,
                "urmind_class": payload.urmind_class.value,
                "severity": severity_detail,
            },
            limitations=limitations,
        )

    context = payload.context
    factors: dict[str, Any] = {
        "evidence_mode": payload.evidence_mode.value,
        "urmind_class": payload.urmind_class.value,
        "severity": severity_detail,
    }

    # Cada entrada: (valor 0–1 ou None quando indisponível, detalhe auditável).
    contributions: dict[str, float | None] = {}
    detail: dict[str, Any] = {}

    contributions["severity"] = _SEVERITY_WEIGHT.get(severity)
    if contributions["severity"] is None:
        detail["severity"] = "indisponível: severidade desconhecida"

    contributions["confidence"] = payload.visual_confidence
    if payload.visual_confidence is None:
        detail["confidence"] = "indisponível: detecção sem confiança"

    nearest = context.nearest_sensitive_m
    if nearest is None:
        contributions["sensitive_proximity"] = None
        detail["sensitive_proximity"] = "indisponível: sem dado de escola/saúde/travessia"
    else:
        contributions["sensitive_proximity"] = _linear_decay(
            nearest,
            PROVISIONAL_THRESHOLDS["sensitive_near_m"],
            PROVISIONAL_THRESHOLDS["sensitive_far_m"],
        )
        detail["sensitive_proximity"] = {"nearest_m": nearest}

    if context.affects_accessibility is None:
        contributions["accessibility"] = None
        detail["accessibility"] = "indisponível: impacto em acessibilidade não avaliado"
    else:
        contributions["accessibility"] = 1.0 if context.affects_accessibility else 0.0

    if context.recurrence_on_segment is None:
        contributions["recurrence"] = None
        detail["recurrence"] = "indisponível: histórico do trecho não consultado"
    else:
        saturation = PROVISIONAL_THRESHOLDS["recurrence_saturation"]
        contributions["recurrence"] = min(1.0, max(0, context.recurrence_on_segment) / saturation)
        detail["recurrence"] = {"count": context.recurrence_on_segment}

    if context.rain_mm_24h is None:
        contributions["environment"] = None
        detail["environment"] = "indisponível: sem dado meteorológico"
    else:
        saturation = PROVISIONAL_THRESHOLDS["rain_saturation_mm"]
        contributions["environment"] = min(1.0, max(0.0, context.rain_mm_24h) / saturation)
        detail["environment"] = {"rain_mm_24h": context.rain_mm_24h}

    available = {name: value for name, value in contributions.items() if value is not None}
    available_weight = sum(_WEIGHTS[name] for name in available)
    coverage = available_weight / sum(_WEIGHTS.values())

    if not available:
        priority = None
        limitations.append("nenhum fator disponível; prioridade não pode ser calculada")
    else:
        # Renormaliza pelos pesos presentes: fator ausente não puxa a nota para
        # baixo como se valesse zero.
        priority = sum(_WEIGHTS[name] * value for name, value in available.items())
        priority = round(priority / available_weight, 4)

    missing = [name for name, value in contributions.items() if value is None]
    if missing:
        limitations.append(
            "prioridade calculada sem os fatores: " + ", ".join(sorted(missing))
        )

    uncertainty = _uncertainty(payload, coverage, severity, limitations)

    factors["weights"] = dict(_WEIGHTS)
    factors["contributions"] = {name: round(v, 4) for name, v in available.items()}
    factors["unavailable"] = detail
    factors["thresholds"] = dict(PROVISIONAL_THRESHOLDS)
    factors["thresholds_are_calibrated"] = False

    return RiskResult(
        severity=severity,
        priority_score=priority,
        uncertainty=uncertainty,
        coverage=round(coverage, 4),
        factors=factors,
        limitations=limitations,
    )


def _uncertainty(
    payload: RiskInput,
    coverage: float,
    severity: Severity,
    limitations: list[str],
) -> float:
    """Quanto o resultado deve ser lido com desconfiança (0 = firme, 1 = frágil).

    Três origens somadas: contexto que faltou, confiança visual baixa e
    coordenada imprecisa. Não é probabilidade calibrada — é um indicador
    declarado, e está dito assim nas limitações.
    """
    from_coverage = 1.0 - coverage

    confidence = payload.visual_confidence
    from_confidence = 1.0 if confidence is None else 1.0 - confidence

    accuracy = payload.location_accuracy_m
    if accuracy is None:
        from_location = 0.5
        limitations.append("precisão da localização desconhecida")
    else:
        # 0 m → 0; 100 m ou mais → 1. Escala declarada, não medida.
        from_location = min(1.0, accuracy / 100.0)

    if severity is Severity.UNKNOWN:
        return 1.0

    value = 0.5 * from_coverage + 0.3 * from_confidence + 0.2 * from_location
    return round(min(1.0, max(0.0, value)), 4)
