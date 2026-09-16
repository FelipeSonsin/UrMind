"""Primitivas da conversão máscara→caixa do UNIVALI (scripts/datasets/_univali.py).

A conversão é uma versão derivada de dataset: se ela errar a geometria, o erro
não aparece como exception — aparece como métrica. Estes testes fixam o
comportamento nos casos que o dataset real contém: região única, regiões
desconectadas, ponte diagonal, pixel isolado, região encostada na borda e
máscara vazia.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _univali import connected_components, parse_sample_name


def mask_from(rows: list[str]):
    """Máscara booleana a partir de um desenho ASCII: `#` é anotado."""
    return np.array([[c == "#" for c in row] for row in rows], dtype=bool)


# ------------------------------------------------------- nome da pasta / grupo


@pytest.mark.parametrize(
    ("directory", "uf", "road", "segment", "position"),
    [
        ("1007599_RS_386_386RS289112_28920", "RS", "386", "386RS289112", 28920),
        ("994588_RS_386_386RS191729_09705", "RS", "386", "386RS191729", 9705),
        # As quatro pastas do pacote com um campo extra antes da posição.
        ("1050564_DF_080_080BDF0050_1_00368", "DF", "080", "080BDF0050_1", 368),
    ],
)
def test_le_identificadores_do_nome_da_pasta(directory, uf, road, segment, position):
    name = parse_sample_name(directory)

    assert name.parsed
    assert (name.uf, name.road, name.segment, name.position) == (uf, road, segment, position)


def test_campo_extra_produz_trecho_distinto_mas_mesma_rodovia():
    """O campo extra vira um trecho diferente, e a fonte não diz se é o mesmo.

    Uni-los seria supor. Agrupar por rodovia resolve sem adivinhar — e é por
    isso que o split padrão do UNIVALI usa rodovia, não trecho (§8.4).
    """
    regular = parse_sample_name("1050001_DF_080_080BDF0050_00100")
    irregular = parse_sample_name("1050564_DF_080_080BDF0050_1_00368")

    assert regular.group_segment != irregular.group_segment
    assert regular.group_road == irregular.group_road == "DF_080"
    assert regular.group_uf == irregular.group_uf == "DF"


def test_nome_fora_do_padrao_nao_inventa_identificador():
    name = parse_sample_name("semunderline")

    assert not name.parsed
    assert name.uf is None and name.road is None and name.position is None
    # Sem identificador, o grupo é a própria pasta: isola em vez de agrupar errado.
    assert name.group_segment == name.group_road == "semunderline"


# ----------------------------------------------------------------- componentes


def test_regiao_unica_vira_uma_caixa_apertada():
    mask = mask_from(
        [
            ".....",
            ".##..",
            ".##..",
            ".....",
        ]
    )

    (box,) = connected_components(mask)

    # Meio-aberto: xmax/ymax exclusivos, então a largura sai em pixels.
    assert (box.xmin, box.ymin, box.xmax, box.ymax) == (1, 1, 3, 3)
    assert (box.width, box.height) == (2, 2)
    assert box.area_px == 4
    assert box.fill_ratio == 1.0
    assert not box.touches_border


def test_regioes_desconectadas_viram_caixas_separadas():
    mask = mask_from(
        [
            "#...#",
            "#...#",
            ".....",
        ]
    )

    boxes = connected_components(mask)

    assert len(boxes) == 2
    assert {(b.xmin, b.xmax) for b in boxes} == {(0, 1), (4, 5)}


def test_ponte_diagonal_une_com_8_e_separa_com_4():
    """A escolha de conectividade muda a contagem de objetos; fica registrada."""
    mask = mask_from(
        [
            "##..",
            "..##",
        ]
    )

    assert len(connected_components(mask, connectivity=8)) == 1
    assert len(connected_components(mask, connectivity=4)) == 2


def test_pixel_isolado_produz_caixa_valida_de_1x1():
    mask = mask_from(
        [
            "...",
            ".#.",
            "...",
        ]
    )

    (box,) = connected_components(mask)

    assert box.area_px == 1
    assert (box.width, box.height) == (1, 1)
    # Invariante exigida da derivada: x1 < x2 e y1 < y2 mesmo no menor caso.
    assert box.xmin < box.xmax and box.ymin < box.ymax


def test_regiao_na_borda_e_marcada_e_nao_extrapola():
    mask = mask_from(
        [
            "##.",
            "##.",
        ]
    )

    (box,) = connected_components(mask)

    assert box.touches_border
    assert box.xmin == 0 and box.ymin == 0
    assert box.xmax <= mask.shape[1] and box.ymax <= mask.shape[0]


def test_mascara_vazia_nao_produz_caixa():
    mask = mask_from(["...", "..."])

    assert connected_components(mask) == []


def test_regiao_diagonal_tem_fill_ratio_baixo():
    """Caixa envolvente de região alongada cobre muito fundo; precisa ser visível."""
    mask = mask_from(
        [
            "#...",
            ".#..",
            "..#.",
            "...#",
        ]
    )

    (box,) = connected_components(mask)

    assert box.area_px == 4
    assert box.box_area == 16
    assert box.fill_ratio == 0.25


def test_componentes_saem_ordenados_por_area_decrescente():
    mask = mask_from(
        [
            "#.###",
            "..###",
            ".....",
        ]
    )

    boxes = connected_components(mask)

    assert [b.area_px for b in boxes] == sorted((b.area_px for b in boxes), reverse=True)
