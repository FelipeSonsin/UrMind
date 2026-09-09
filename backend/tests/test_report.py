"""Resposta sem LLM (§15) — a camada anti-alucinação.

O que estes testes cobram do template não é redação, é honestidade: dado que não
existe não pode aparecer como número, e nenhuma frase pode afirmar mais do que a
entrada sustenta.
"""

from __future__ import annotations

import pytest

from app.schemas.core import EvidenceMode, UrmindClass
from app.services.report import (
    ActionSuggestion,
    ReportInput,
    ResponsibilitySuggestion,
    render_event_report,
)
from app.services.risk import ContextInput, RiskInput, assess

CONTEXTO = ContextInput(
    distance_to_school_m=60,
    affects_accessibility=True,
    recurrence_on_segment=3,
    rain_mm_24h=12,
)


def risco(**kwargs):
    base = {
        "urmind_class": UrmindClass.ROAD_D40,
        "evidence_mode": EvidenceMode.PHOTO,
        "visual_confidence": 0.87,
        "location_accuracy_m": 6.0,
        "context": CONTEXTO,
    }
    return assess(RiskInput(**{**base, **kwargs}))


def relatorio_completo(**kwargs) -> str:
    base = {
        "event_key": "EVT-2026-0001",
        "urmind_class": UrmindClass.ROAD_D40,
        "risk": risco(),
        "visual_confidence": 0.87,
        "model_version": "yolox-s@1.0.0",
        "latitude": -23.5613,
        "longitude": -46.6560,
        "location_accuracy_m": 6.0,
        "location_source": "exif",
        "road_segment_name": "Avenida Paulista",
        "distance_to_road_m": 3.4,
        "evidence": ["foto enviada pelo aplicativo"],
        "responsibility": ResponsibilitySuggestion(
            responsible="Prefeitura Municipal", source="competência municipal sobre via local"
        ),
        "action": ActionSuggestion(code="INSPECAO_TECNICA", label="Inspeção técnica no local"),
    }
    return render_event_report(ReportInput(**{**base, **kwargs}))


# ------------------------------------------------------------- caminho completo


def test_relatorio_completo_traz_as_secoes_do_planejamento():
    texto = relatorio_completo()

    for secao in ("LOCALIZAÇÃO", "EVIDÊNCIAS", "AVALIAÇÃO", "RESPONSÁVEL",
                  "AÇÃO SUGERIDA", "LIMITAÇÕES"):
        assert secao in texto


def test_valores_aparecem_como_foram_informados():
    texto = relatorio_completo()

    assert "buraco no pavimento" in texto
    assert "87%" in texto
    assert "-23.561300, -46.656000" in texto
    assert "±6 m" in texto
    assert "Avenida Paulista" in texto
    assert "yolox-s@1.0.0" in texto


def test_acao_sai_sempre_como_sugestao():
    """§14.4: o UrMind não substitui decisão técnica do órgão."""
    texto = relatorio_completo()

    assert "sugestão" in texto
    assert "decisão técnica e administrativa continua sendo do órgão" in texto


# ------------------------------------------------------------- dado que falta


def test_sem_confianca_o_texto_diz_nao_disponivel_em_vez_de_numero():
    texto = relatorio_completo(visual_confidence=None, risk=risco(visual_confidence=None))

    assert "Confiança da detecção: não disponível" in texto
    assert "%" not in texto.split("LOCALIZAÇÃO")[0].split("Confiança")[1].split("\n")[0]


def test_sem_coordenada_o_relatorio_admite_que_nao_ha_posicao():
    texto = relatorio_completo(
        latitude=None, longitude=None, location_accuracy_m=None,
        location_source=None, road_segment_name=None, distance_to_road_m=None,
    )

    assert "Coordenada: não disponível" in texto
    assert "não é georreferenciável" in texto


def test_sem_modelo_o_relatorio_nao_atribui_o_resultado_a_detector():
    """§9: sem modelo promovido, não existe resultado atribuível."""
    texto = relatorio_completo(model_version=None)

    assert "não veio de modelo promovido" in texto
    assert "não pode ser atribuído" in texto


def test_sem_regra_de_competencia_pede_triagem():
    """§14.3: sem base confiável, `requires_triage`."""
    texto = relatorio_completo(responsibility=None)

    assert "requer triagem" in texto
    assert "Sugerido:" not in texto


def test_sem_acao_no_catalogo_nao_inventa_recomendacao():
    texto = relatorio_completo(action=None)

    secao = texto.split("AÇÃO SUGERIDA")[1].split("LIMITAÇÕES")[0]
    assert "Não disponível." in secao


def test_sem_evidencias_a_lista_nao_e_preenchida():
    texto = relatorio_completo(evidence=[])

    secao = texto.split("EVIDÊNCIAS")[1].split("AVALIAÇÃO")[0]
    assert "não disponível" in secao


# ------------------------------------------------------------------- previsões


def test_previsao_so_aparece_quando_existe():
    """§15 e §24: previsão sem histórico é precisão fabricada."""
    sem = relatorio_completo()
    com = relatorio_completo(predictions=["recorrência estimada no trecho em 90 dias: 0,42"])

    assert "PREVISÕES" not in sem
    assert "PREVISÕES" in com
    assert "0,42" in com


# ----------------------------------------------------------------- limitações


def test_limitacoes_do_motor_de_risco_chegam_ao_texto():
    texto = relatorio_completo(risk=risco(context=ContextInput()))

    assert "prioridade calculada sem os fatores" in texto


def test_prioridade_nao_calculada_e_dita_explicitamente():
    texto = relatorio_completo(
        urmind_class=UrmindClass.UNKNOWN, risk=risco(urmind_class=UrmindClass.UNKNOWN)
    )

    assert "Prioridade: não calculada" in texto
    assert "Severidade: não determinada" in texto


def test_endereco_e_declarado_como_aproximado():
    """§12.1: Nominatim devolve o objeto mais próximo, não uma verdade."""
    texto = relatorio_completo(address="Avenida Paulista, Bela Vista", address_from_osm=True)

    assert "Endereço aproximado:" in texto
    assert "não é a fonte da coordenada" in texto


def test_atribuicao_osm_aparece_quando_ha_dado_de_via():
    com_via = relatorio_completo()
    sem_via = relatorio_completo(road_segment_name=None, distance_to_road_m=None)

    assert "OpenStreetMap" in com_via
    assert "OpenStreetMap" not in sem_via


# ------------------------------------------------------- garantias estruturais


def test_template_nao_aceita_campo_nao_declarado():
    """StrictUndefined: campo esquecido quebra o teste em vez de sumir do texto."""
    from jinja2 import UndefinedError

    from app.services.report import TEMPLATE_NAME, _environment

    template = _environment().get_template(TEMPLATE_NAME)
    with pytest.raises(UndefinedError):
        template.render(report={"event_key": "X"})


def test_nenhum_numero_aparece_quando_nada_foi_medido():
    """O caso mais perigoso: evento sem nada. O texto não pode fabricar valor."""
    texto = relatorio_completo(
        urmind_class=UrmindClass.UNKNOWN,
        risk=risco(urmind_class=UrmindClass.UNKNOWN, visual_confidence=None,
                   location_accuracy_m=None, context=ContextInput()),
        visual_confidence=None,
        model_version=None,
        latitude=None,
        longitude=None,
        location_accuracy_m=None,
        location_source=None,
        road_segment_name=None,
        distance_to_road_m=None,
        evidence=[],
        responsibility=None,
        action=None,
    )

    assert "Confiança da detecção: não disponível" in texto
    assert "Prioridade: não calculada" in texto
    assert "Coordenada: não disponível" in texto
    assert "requer triagem" in texto
    # Só a incerteza máxima e a cobertura zero podem aparecer como número.
    assert "Incerteza: 1.00" in texto
    assert "Fatores disponíveis: 0%" in texto
