"""Split por grupo (§8.3 passo 4 e §8.4).

O teste que importa é um só, e os outros existem para sustentá-lo: nenhum grupo
pode aparecer em mais de um split. É essa garantia que impede a métrica de subir
sozinha por causa de frames vizinhos.
"""

from __future__ import annotations

import pytest

from app.ml.splits import SplitRatios, find_leakage, group_by_prefix, split_by_group


def frames(rota: str, quantidade: int) -> list[str]:
    return [f"{rota}/frame_{i:04d}.jpg" for i in range(quantidade)]


def dataset_sintetico(rotas: int = 20, por_rota: int = 30) -> list[str]:
    caminhos: list[str] = []
    for indice in range(rotas):
        caminhos.extend(frames(f"rota_{indice:02d}", por_rota))
    return caminhos


def rota_do_caminho(caminho: str) -> str:
    return caminho.split("/")[0]


# ------------------------------------------------------------- sem vazamento


def test_nenhum_grupo_aparece_em_mais_de_um_split():
    split = split_by_group(dataset_sintetico(), rota_do_caminho)

    assert find_leakage(split) == {}


def test_frames_da_mesma_rota_ficam_juntos():
    """O ponto do §8.4: frames vizinhos não podem se separar."""
    split = split_by_group(dataset_sintetico(rotas=9, por_rota=10), rota_do_caminho)

    por_split = {
        "train": {rota_do_caminho(c) for c in split.train},
        "validation": {rota_do_caminho(c) for c in split.validation},
        "test": {rota_do_caminho(c) for c in split.test},
    }
    assert por_split["train"] & por_split["validation"] == set()
    assert por_split["train"] & por_split["test"] == set()
    assert por_split["validation"] & por_split["test"] == set()


def test_nenhum_item_se_perde_nem_se_duplica():
    itens = dataset_sintetico()

    split = split_by_group(itens, rota_do_caminho)

    juntos = split.train + split.validation + split.test
    assert len(juntos) == len(itens)
    assert sorted(juntos) == sorted(itens)


# --------------------------------------------------------------- proporções


def test_proporcoes_se_aproximam_do_alvo_com_grupos_suficientes():
    split = split_by_group(dataset_sintetico(rotas=40, por_rota=25), rota_do_caminho)

    assert split.achieved_ratios["train"] == pytest.approx(0.70, abs=0.06)
    assert split.achieved_ratios["validation"] == pytest.approx(0.15, abs=0.06)
    assert split.achieved_ratios["test"] == pytest.approx(0.15, abs=0.06)


def test_proporcao_obtida_e_reportada_quando_nao_bate_com_o_alvo():
    """Três grupos gigantes não dão para dividir 70/15/15 — e isso tem que aparecer."""
    itens = frames("rota_a", 100) + frames("rota_b", 100) + frames("rota_c", 100)

    split = split_by_group(itens, rota_do_caminho)

    assert find_leakage(split) == {}
    assert sum(split.achieved_ratios.values()) == pytest.approx(1.0)
    # Grupos indivisíveis de 100 não chegam perto de 70/15/15; o que saiu fica
    # declarado em vez de a função fingir que atingiu o alvo.
    assert split.achieved_ratios != {"train": 0.70, "validation": 0.15, "test": 0.15}


def test_split_vazio_e_denunciado():
    """Sem os três conjuntos não há como treinar, escolher e medir (§8.3)."""
    itens = frames("rota_a", 100) + frames("rota_b", 100) + frames("rota_c", 100)

    split = split_by_group(itens, rota_do_caminho)

    assert "validation" in split.empty_splits
    assert split.warnings
    assert "não se dividem" in split.warnings[0]
    assert split.summary()["warnings"]


def test_split_saudavel_nao_gera_alerta():
    split = split_by_group(dataset_sintetico(rotas=30, por_rota=10), rota_do_caminho)

    assert split.empty_splits == []
    assert split.warnings == []


def test_proporcoes_customizadas_sao_respeitadas():
    ratios = SplitRatios(train=0.8, validation=0.1, test=0.1)

    split = split_by_group(dataset_sintetico(rotas=50, por_rota=10), rota_do_caminho, ratios)

    assert split.achieved_ratios["train"] == pytest.approx(0.8, abs=0.06)


def test_proporcoes_que_nao_somam_um_sao_recusadas():
    with pytest.raises(ValueError, match="somar 1,0"):
        SplitRatios(train=0.8, validation=0.3, test=0.1)


# ----------------------------------------------------------- reprodutibilidade


def test_mesma_semente_produz_o_mesmo_split():
    itens = dataset_sintetico()

    primeiro = split_by_group(itens, rota_do_caminho, seed=7)
    segundo = split_by_group(itens, rota_do_caminho, seed=7)

    assert primeiro.groups == segundo.groups


def test_sementes_diferentes_podem_produzir_splits_diferentes():
    itens = dataset_sintetico(rotas=30, por_rota=10)

    a = split_by_group(itens, rota_do_caminho, seed=1)
    b = split_by_group(itens, rota_do_caminho, seed=99)

    assert find_leakage(a) == {} and find_leakage(b) == {}
    assert a.groups != b.groups


# ------------------------------------------------------------- casos de borda


def test_dataset_vazio_nao_quebra():
    split = split_by_group([], rota_do_caminho)

    assert len(split) == 0
    assert find_leakage(split) == {}


def test_grupo_unico_vai_inteiro_para_um_split():
    split = split_by_group(frames("rota_unica", 50), rota_do_caminho)

    ocupados = [s for s in (split.train, split.validation, split.test) if s]
    assert len(ocupados) == 1
    assert len(ocupados[0]) == 50


def test_agrupamento_por_prefixo_de_origem_e_rota():
    """§8.3 passo 4: split por origem/rota, não por frame."""
    itens = [
        "japan/rota_12/frame_0001.jpg",
        "japan/rota_12/frame_0002.jpg",
        "india/rota_03/frame_0001.jpg",
    ]

    chave = group_by_prefix(parts=2)

    assert chave(itens[0]) == "japan/rota_12"
    assert chave(itens[0]) == chave(itens[1])
    assert chave(itens[2]) == "india/rota_03"


def test_resumo_serializa_para_dataset_versions():
    split = split_by_group(dataset_sintetico(rotas=10, por_rota=5), rota_do_caminho)

    resumo = split.summary()

    assert set(resumo["counts"]) == {"train", "validation", "test"}
    assert sum(resumo["counts"].values()) == 50
    assert set(resumo["groups"]) == {"train", "validation", "test"}
