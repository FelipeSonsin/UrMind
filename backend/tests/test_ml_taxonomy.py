"""Mapeamento de rótulos externos (§8.2, §8.3 passo 2)."""

from __future__ import annotations

from app.ml.taxonomy import (
    RDD2022_TO_URMIND,
    LabelMapping,
    map_dataset_label,
    map_rdd2022_labels,
)
from app.schemas.core import UrmindClass


def test_as_quatro_classes_da_v1_sao_mapeadas():
    """§8.2: exatamente essas quatro categorias formam a taxonomia V1."""
    resultados = map_rdd2022_labels(["D00", "D10", "D20", "D40"])

    assert [r.urmind_class for r in resultados] == [
        UrmindClass.ROAD_D00,
        UrmindClass.ROAD_D10,
        UrmindClass.ROAD_D20,
        UrmindClass.ROAD_D40,
    ]
    assert all(r.accepted for r in resultados)


def test_rotulo_e_normalizado_antes_de_procurar():
    assert map_dataset_label(" d40 ", RDD2022_TO_URMIND).urmind_class is UrmindClass.ROAD_D40


def test_rotulo_fora_da_taxonomia_e_recusado_com_motivo():
    """§8.2: não reutilizar classe só porque 'parece parecida'."""
    resultado = map_dataset_label("D43", RDD2022_TO_URMIND)

    assert resultado.accepted is False
    assert resultado.urmind_class is None
    assert "sinalização horizontal" in resultado.reason


def test_tampa_do_rdd_nao_vira_urmind_manhole():
    """D50 é tampa no RDD; URMIND_MANHOLE exige dataset e protocolo próprios."""
    resultado = map_dataset_label("D50", RDD2022_TO_URMIND)

    assert resultado.accepted is False
    assert resultado.urmind_class is not UrmindClass.MANHOLE
    assert "protocolo" in resultado.reason


def test_rotulo_desconhecido_recebe_motivo_generico():
    resultado = map_dataset_label("XYZ", RDD2022_TO_URMIND)

    assert resultado.accepted is False
    assert "fora da taxonomia V1" in resultado.reason


def test_nenhum_rotulo_e_mapeado_para_unknown():
    """§31.8: URMIND_UNKNOWN é estado do sistema, nunca classe de treino."""
    assert UrmindClass.UNKNOWN not in RDD2022_TO_URMIND.values()

    candidatos = ["D00", "D10", "D20", "D40", "D01", "D11", "D43", "D44", "D50", "XYZ", ""]
    for mapeado in (map_dataset_label(c, RDD2022_TO_URMIND) for c in candidatos):
        assert mapeado.urmind_class is not UrmindClass.UNKNOWN


def test_classes_v15_e_v2_nao_entram_por_dataset_externo():
    """Bueiro, calçada e sinalização só entram com dataset próprio (§8.2)."""
    proibidas = {UrmindClass.MANHOLE, UrmindClass.SIDEWALK, UrmindClass.SIGNAGE}

    assert proibidas & set(RDD2022_TO_URMIND.values()) == set()


def test_recusa_preserva_o_rotulo_de_origem_para_auditoria():
    resultado = map_dataset_label("D43", RDD2022_TO_URMIND)

    assert isinstance(resultado, LabelMapping)
    assert resultado.source_label == "D43"
