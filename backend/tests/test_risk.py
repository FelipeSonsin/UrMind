"""Motor de severidade e prioridade (§14).

O que estes testes protegem não é o número em si — pesos declarados mudam quando
forem calibrados. É o comportamento que não pode mudar: não inventar, não
esconder ausência, não afirmar o que a foto não sustenta.
"""

from __future__ import annotations

import pytest

from app.schemas.core import EvidenceMode, UrmindClass
from app.services.risk import (
    PROVISIONAL_THRESHOLDS,
    RULESET_VERSION,
    ContextInput,
    RiskInput,
    Severity,
    assess,
)

CONTEXTO_COMPLETO = ContextInput(
    distance_to_school_m=80,
    distance_to_health_m=400,
    distance_to_crossing_m=120,
    affects_accessibility=False,
    recurrence_on_segment=2,
    rain_mm_24h=5,
)


def entrada(**kwargs) -> RiskInput:
    base = {
        "urmind_class": UrmindClass.ROAD_D40,
        "evidence_mode": EvidenceMode.PHOTO,
        "visual_confidence": 0.9,
        "location_accuracy_m": 8.0,
    }
    return RiskInput(**{**base, **kwargs})


# ------------------------------------------------------------------ severidade


def test_severidade_e_especifica_da_classe():
    """§14.1: buraco não tem a mesma gravidade de trinca longitudinal."""
    buraco = assess(entrada(urmind_class=UrmindClass.ROAD_D40)).severity
    malha = assess(entrada(urmind_class=UrmindClass.ROAD_D20)).severity
    trinca = assess(entrada(urmind_class=UrmindClass.ROAD_D00)).severity

    assert buraco is Severity.HIGH
    assert malha is Severity.MEDIUM
    assert trinca is Severity.LOW


def test_confianca_baixa_rebaixa_a_severidade():
    """Não se agrava um diagnóstico em cima de detecção fraca."""
    forte = assess(entrada(visual_confidence=0.95)).severity
    fraca = assess(entrada(visual_confidence=0.30)).severity

    assert forte is Severity.HIGH
    assert fraca is Severity.MEDIUM


def test_severidade_nunca_cai_abaixo_de_low():
    result = assess(entrada(urmind_class=UrmindClass.ROAD_D00, visual_confidence=0.05))
    assert result.severity is Severity.LOW


def test_foto_sozinha_nunca_emite_critical():
    """§14.1: criticidade exige evidência física que a fase foto não tem."""
    agravado = assess(
        entrada(
            urmind_class=UrmindClass.ROAD_D40,
            visual_confidence=0.99,
            apparent_extent=0.9,
            detection_count=20,
        )
    )

    assert agravado.severity is Severity.HIGH
    assert any("evidência física" in limite for limite in agravado.limitations)


def test_extensao_aparente_nao_e_tratada_como_medida_fisica():
    result = assess(entrada(apparent_extent=0.20, urmind_class=UrmindClass.ROAD_D20))

    detalhe = result.factors["severity"]
    assert detalhe["apparent_extent_is_physical_measure"] is False
    assert detalhe["upgraded_by_apparent_extent"] == pytest.approx(0.20)


def test_agravante_exige_deteccao_confiavel():
    """Extensão grande com confiança baixa não pode subir a severidade."""
    result = assess(
        entrada(urmind_class=UrmindClass.ROAD_D20, visual_confidence=0.2, apparent_extent=0.9)
    )

    assert result.severity is Severity.LOW  # rebaixada, não agravada
    assert "upgraded_by_apparent_extent" not in result.factors["severity"]


def test_classe_desconhecida_nao_produz_severidade_nem_prioridade():
    """URMIND_UNKNOWN é estado do sistema, não classe (§31.8)."""
    result = assess(entrada(urmind_class=UrmindClass.UNKNOWN))

    assert result.severity is Severity.UNKNOWN
    assert result.priority_score is None
    assert result.uncertainty == 1.0
    assert any("triagem" in limite for limite in result.limitations)


def test_evidencia_so_de_sensor_nao_afirma_classe():
    """§31.9: câmera define a classe; sensor é evidência complementar."""
    result = assess(entrada(evidence_mode=EvidenceMode.SENSOR_ONLY))

    assert result.severity is Severity.UNKNOWN
    assert result.priority_score is None
    assert result.coverage == 0.0


# ------------------------------------------------------------------ prioridade


def test_prioridade_fica_entre_zero_e_um():
    result = assess(entrada(context=CONTEXTO_COMPLETO))
    assert result.priority_score is not None
    assert 0.0 <= result.priority_score <= 1.0
    assert result.coverage == 1.0


def test_fator_ausente_sai_da_conta_em_vez_de_valer_zero():
    """O ponto central do §13: ausência não é o mesmo que valor baixo."""
    sem_contexto = assess(entrada(context=ContextInput()))
    com_contexto_zerado = assess(
        entrada(
            context=ContextInput(
                distance_to_school_m=10_000,
                affects_accessibility=False,
                recurrence_on_segment=0,
                rain_mm_24h=0,
            )
        )
    )

    assert sem_contexto.priority_score is not None
    assert com_contexto_zerado.priority_score is not None
    # Contexto ausente preserva a nota dos fatores que existem; contexto medido
    # como irrelevante realmente puxa a nota para baixo.
    assert sem_contexto.priority_score > com_contexto_zerado.priority_score
    assert sem_contexto.coverage < com_contexto_zerado.coverage


def test_cobertura_reflete_o_peso_disponivel():
    apenas_visual = assess(entrada(context=ContextInput()))

    # severity (0.40) + confidence (0.15) de um total de 1.0
    assert apenas_visual.coverage == pytest.approx(0.55)
    assert "prioridade calculada sem os fatores" in " ".join(apenas_visual.limitations)


def test_proximidade_de_escola_aumenta_a_prioridade():
    perto = assess(entrada(context=ContextInput(distance_to_school_m=30)))
    longe = assess(entrada(context=ContextInput(distance_to_school_m=1000)))

    assert perto.priority_score > longe.priority_score


def test_recorrencia_no_trecho_aumenta_a_prioridade():
    recorrente = assess(entrada(context=ContextInput(recurrence_on_segment=5)))
    isolado = assess(entrada(context=ContextInput(recurrence_on_segment=0)))

    assert recorrente.priority_score > isolado.priority_score


def test_acessibilidade_pesa_na_prioridade():
    afeta = assess(entrada(context=ContextInput(affects_accessibility=True)))
    nao_afeta = assess(entrada(context=ContextInput(affects_accessibility=False)))

    assert afeta.priority_score > nao_afeta.priority_score


def test_severidade_maior_gera_prioridade_maior_no_mesmo_contexto():
    buraco = assess(entrada(urmind_class=UrmindClass.ROAD_D40, context=CONTEXTO_COMPLETO))
    trinca = assess(entrada(urmind_class=UrmindClass.ROAD_D00, context=CONTEXTO_COMPLETO))

    assert buraco.priority_score > trinca.priority_score


# -------------------------------------------------------------------- incerteza


def test_incerteza_cresce_quando_falta_contexto():
    completo = assess(entrada(context=CONTEXTO_COMPLETO))
    vazio = assess(entrada(context=ContextInput()))

    assert vazio.uncertainty > completo.uncertainty


def test_incerteza_cresce_com_localizacao_imprecisa():
    precisa = assess(entrada(location_accuracy_m=3, context=CONTEXTO_COMPLETO))
    imprecisa = assess(entrada(location_accuracy_m=90, context=CONTEXTO_COMPLETO))

    assert imprecisa.uncertainty > precisa.uncertainty


def test_localizacao_sem_precisao_declarada_e_registrada_como_limitacao():
    result = assess(entrada(location_accuracy_m=None, context=CONTEXTO_COMPLETO))

    assert any("precisão da localização" in limite for limite in result.limitations)


# --------------------------------------------------- auditabilidade e política


def test_resultado_carrega_a_conta_aberta():
    """§14.2: regra transparente significa poder discordar item a item."""
    persistido = assess(entrada(context=CONTEXTO_COMPLETO)).as_persisted()

    assert persistido["severity"] in {s.value for s in Severity}
    factors = persistido["factors"]
    assert factors["ruleset_version"] == RULESET_VERSION
    assert factors["weights"]
    assert factors["contributions"]
    assert factors["thresholds"] == PROVISIONAL_THRESHOLDS
    # Ninguém pode ler esses limiares como se fossem calibrados (§31.16).
    assert factors["thresholds_are_calibrated"] is False


def test_ausencias_ficam_nomeadas_no_resultado():
    result = assess(entrada(context=ContextInput()))
    indisponiveis = result.factors["unavailable"]

    assert "indisponível" in indisponiveis["environment"]
    assert "indisponível" in indisponiveis["recurrence"]
    assert "indisponível" in indisponiveis["sensitive_proximity"]


def test_pesos_somam_um():
    from app.services.risk import _WEIGHTS

    assert sum(_WEIGHTS.values()) == pytest.approx(1.0)


def test_nenhum_indicador_socioeconomico_entra_no_motor():
    """§14.2: indicador socioeconômico não pode reduzir atendimento.

    A garantia da V1 é não deixar a variável existir. Este teste falha se alguém
    adicionar renda, vulnerabilidade ou censo à entrada sem rediscutir a regra.
    """
    proibidos = ("renda", "income", "vulnerab", "socio", "censo", "census", "idh", "classe_social")
    campos = set(RiskInput.__dataclass_fields__) | set(ContextInput.__dataclass_fields__)

    for campo in campos:
        assert not any(termo in campo.lower() for termo in proibidos), campo
