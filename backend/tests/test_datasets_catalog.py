"""Catálogo de fontes (§8.2, §8.3 passo 1)."""

from __future__ import annotations

import pytest

from app.datasets.adapters import ADAPTERS
from app.datasets.catalog import SOURCES, DatasetRole, get_source, sources_by_role


def test_todas_as_fontes_do_escopo_estao_declaradas():
    esperadas = {
        "rdd2022",
        "univali_br",
        "urban_community",
        "project_sidewalk",
        "rampnet",
        "camber",
        "global_streetscapes",
        "bdd100k",
    }
    assert {s.id for s in SOURCES} == esperadas


def test_ids_nao_se_repetem():
    ids = [s.id for s in SOURCES]
    assert len(ids) == len(set(ids))


def test_fonte_adiada_nao_tem_adaptador():
    """DEFERRED significa que ninguém pode lê-la achando que está pronta."""
    for source in sources_by_role(DatasetRole.DEFERRED):
        assert source.adapter is None
        assert not source.trainable


def test_toda_fonte_lida_aponta_para_um_adaptador_existente():
    for source in SOURCES:
        if source.adapter is not None:
            assert source.adapter in ADAPTERS


def test_fonte_de_treino_declara_taxonomia_e_grupo():
    """Sem nota de taxonomia e de grupo não há como auditar o §8.2 e o §8.4."""
    for source in sources_by_role(DatasetRole.TRAINING_V1):
        assert source.taxonomy_note
        assert source.group_note


def test_geo_reference_nao_e_treinavel():
    """Ponto com coordenada não é anotação de imagem."""
    for source in sources_by_role(DatasetRole.GEO_REFERENCE):
        assert not source.trainable


def test_checksums_declarados_tem_o_tamanho_certo():
    for source in SOURCES:
        for arquivo in source.expected_files:
            if arquivo.md5:
                assert len(arquivo.md5) == 32
            if arquivo.sha256:
                assert len(arquivo.sha256) == 64


def test_fonte_desconhecida_lista_as_conhecidas():
    with pytest.raises(KeyError, match="rdd2022"):
        get_source("nao_existe")
