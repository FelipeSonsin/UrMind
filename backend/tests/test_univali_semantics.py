"""O que o UNIVALI derivado afirma ser — e o que ele ainda não pode afirmar.

Três estados que um relatório anterior tratava como um só:

    tem máscara semântica        a fonte anota, e anota bem
    tem caixas candidatas        uma transformação geométrica produziu caixas
    está pronto para avaliar     as caixas têm instância, classe e cobertura

O UNIVALI chega ao segundo. Estes testes existem para que ninguém o promova ao
terceiro por descuido: o pipeline passa nos testes justamente afirmando menos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import convert_univali_masks as conv
import make_univali_splits as splits
import validate_univali_boxes as val


def component(xmin, ymin, xmax, ymax, area=None, border=False):
    width, height = xmax - xmin, ymax - ymin
    return {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "width": width,
        "height": height,
        "area_px": area if area is not None else width * height,
        "box_area_px": width * height,
        "fill_ratio": 1.0,
        "touches_border": border,
    }


def scan_row(directory="1007599_RS_386_386RS289112_28920", *, components=()):
    return {
        "directory": directory,
        "image_relpath": f"datasets/raw/univali_br/v1/{directory}/{directory}_RAW.jpg",
        "image_width": 1024,
        "image_height": 640,
        "image_error": None,
        "name_parsed": True,
        "image_id": "1007599",
        "uf": "RS",
        "road": "386",
        "segment": "386RS289112",
        "position": 28920,
        "group_segment": "RS_386_386RS289112",
        "group_road": "RS_386",
        "group_uf": "RS",
        "masks": {
            "POTHOLE": {
                "present": True,
                "error": None,
                "foreground_px": sum(c["area_px"] for c in components),
                "observed": {"width": 1024, "height": 640},
                "component_count": len(components),
                "components": list(components),
            }
        },
    }


# ------------------------------------------- componente não é instância provada


def test_caixa_sai_marcada_como_componente_nao_validado():
    manifest, _ = conv.convert(
        [scan_row(components=[component(10, 10, 40, 40)])], 0, None
    )
    entrada = manifest[0]

    assert entrada["derivation_status"] == "CANDIDATE_DERIVED_BOXES"
    assert entrada["instance_semantics_validated"] is False
    assert entrada["boxes"][0]["instance_status"] == "UNVALIDATED_COMPONENT"


def test_relatorio_declara_hipotese_sem_evidencia():
    _, resumo = conv.convert(
        [scan_row(components=[component(10, 10, 40, 40), component(100, 100, 130, 130)])],
        0,
        None,
    )

    assert resumo["overlapping_box_pairs"] == 0
    assert resumo["images_with_boxes"] == 1


def test_sobreposicao_entre_componentes_e_contada():
    """Caixas que se cruzam são o sintoma de um objeto partido em dois."""
    _, resumo = conv.convert(
        [scan_row(components=[component(10, 10, 60, 60), component(40, 40, 90, 90)])],
        0,
        None,
    )

    assert resumo["overlapping_box_pairs"] == 1


def test_componente_de_um_pixel_e_marcado_e_nao_removido():
    manifest, resumo = conv.convert(
        [scan_row(components=[component(5, 5, 6, 6, area=1), component(10, 10, 60, 60)])],
        0,
        None,
    )
    caixas = manifest[0]["boxes"]

    assert len(caixas) == 2, "nada é descartado por tamanho"
    assert resumo["single_pixel_components"] == 1
    assert any(b["single_pixel"] for b in caixas)
    assert any(b["noise_candidate"] for b in caixas)


# ------------------------------------------------ máscara vazia é indefinida


def test_mascara_vazia_fica_no_manifesto_com_status_pendente():
    manifest, resumo = conv.convert([scan_row(components=[])], 0, None)

    assert len(manifest) == 1, "a amostra não pode sumir do manifesto"
    assert manifest[0]["mask_status"] == "EMPTY_MASK_SEMANTICS_UNRESOLVED"
    assert manifest[0]["boxes"] == []
    assert resumo["empty_mask_samples"] == 1


def test_positivas_e_vazias_sao_contadas_separadamente():
    manifest, resumo = conv.convert(
        [
            scan_row("1_RS_386_386RS289112_1", components=[component(10, 10, 40, 40)]),
            scan_row("2_RS_386_386RS289112_2", components=[]),
        ],
        0,
        None,
    )

    assert len(manifest) == 2
    assert resumo["images_with_boxes"] == 1
    assert resumo["empty_mask_samples"] == 1


def test_vazia_nao_e_descartada_como_defeito():
    _, resumo = conv.convert([scan_row(components=[])], 0, None)

    assert resumo["drops"] == {}, "máscara vazia não é descarte"


# ------------------------------------ contaminação cruzada é fail-closed


def _inventario(tmp_path, linhas):
    caminho = tmp_path / "rdd2022_inventory.jsonl"
    caminho.write_text(
        "\n".join(json.dumps(linha) for linha in linhas) + "\n", encoding="utf-8"
    )
    return caminho


@pytest.fixture
def inventario(tmp_path, monkeypatch):
    monkeypatch.setattr(val, "require_local", lambda p: p)
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda _p: "sha-do-inventario")

    def instalar(linhas=None):
        caminho = tmp_path / "rdd2022_inventory.jsonl"
        if linhas is not None:
            _inventario(tmp_path, linhas)
        monkeypatch.setattr(val, "RDD_INVENTORY", caminho)
        return caminho

    return instalar


LINHA_RDD = {"sha256": "f" * 64, "dhash128": "0" * 32}


def test_skip_cross_source_invalida_a_verificacao(inventario):
    inventario([LINHA_RDD])

    resultado = val.cross_source_check({}, {}, 4, skip=True)

    assert resultado["skipped"] is True
    assert resultado["valid"] is False
    assert "skip-cross-source" in resultado["reason"]


def test_inventario_ausente_invalida(inventario):
    inventario(None)

    resultado = val.cross_source_check({}, {}, 4, skip=False)

    assert resultado["valid"] is False
    assert "não existe" in resultado["reason"]


def test_inventario_vazio_invalida(inventario, tmp_path):
    caminho = inventario([])
    caminho.write_text("", encoding="utf-8")

    resultado = val.cross_source_check({}, {}, 4, skip=False)

    assert resultado["valid"] is False
    assert "vazio" in resultado["reason"]


def test_inventario_ilegivel_invalida(inventario):
    caminho = inventario([LINHA_RDD])
    caminho.write_text("{ nao e json\n", encoding="utf-8")

    resultado = val.cross_source_check({}, {}, 4, skip=False)

    assert resultado["valid"] is False
    assert "linha inválida" in resultado["reason"]


def test_inventario_sem_campos_de_comparacao_invalida(inventario):
    inventario([{"rel_path": "x.jpg"}])

    resultado = val.cross_source_check({}, {}, 4, skip=False)

    assert resultado["valid"] is False
    assert "comparação impossível" in resultado["reason"]


def test_comparacao_completa_registra_hash_do_inventario(inventario):
    inventario([LINHA_RDD])

    resultado = val.cross_source_check({"a" * 64: ["x"]}, {"x": 0xFFFF}, 4, skip=False)

    assert resultado["skipped"] is False
    assert resultado["valid"] is True
    assert resultado["inventory_sha256"] == "sha-do-inventario"
    assert resultado["inventory_records"] == 1


def test_contaminacao_detectada_invalida(inventario):
    inventario([LINHA_RDD])

    resultado = val.cross_source_check({"f" * 64: ["x"]}, {"x": 0}, 4, skip=False)

    assert resultado["valid"] is False
    assert resultado["exact_sha256_matches"] == 1


# ------------------------- o split exige a verificação cruzada de verdade


def _validacao(**overrides) -> dict:
    base = {
        "passed": True,
        "generated_at": "2026-09-10T17:00:00-03:00",
        "integrity": {
            "boxes_manifest_sha256": "a" * 64,
            "rdd_inventory_sha256": "inv-sha",
        },
        "duplicates": {"exact_sha256_groups": [], "near_duplicate_pairs": []},
        "cross_source_contamination": {
            "skipped": False,
            "valid": True,
            "inventory_sha256": "inv-sha",
            "compared_against": "datasets/manifests/rdd2022_inventory.jsonl",
            "inventory_records": 47420,
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def relatorio(tmp_path, monkeypatch):
    caminho = tmp_path / "univali_box_validation.json"
    monkeypatch.setattr(splits, "VALIDATION_REPORT", caminho)
    monkeypatch.setattr(splits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(splits, "require_local", lambda p: p)
    monkeypatch.setattr(splits, "file_sha256", lambda _p: "sha-do-relatorio")

    def escrever(payload):
        caminho.write_text(json.dumps(payload), encoding="utf-8")
        return caminho

    return escrever


def test_split_aceita_quando_a_cruzada_foi_feita(relatorio):
    relatorio(_validacao())

    _, evidencia = splits.require_validation("a" * 64)

    assert evidencia["cross_source"]["performed"] is True
    assert evidencia["cross_source"]["inventory_sha256"] == "inv-sha"


def test_split_recusa_cruzada_pulada(relatorio):
    relatorio(
        _validacao(
            cross_source_contamination={"skipped": True, "reason": "--skip-cross-source"}
        )
    )

    with pytest.raises(SystemExit) as erro:
        splits.require_validation("a" * 64)

    assert "não foi executada" in str(erro.value)


def test_split_recusa_cruzada_invalida(relatorio):
    relatorio(
        _validacao(
            cross_source_contamination={
                "skipped": False,
                "valid": False,
                "reason": "inventário vazio",
                "inventory_sha256": "inv-sha",
            }
        )
    )

    with pytest.raises(SystemExit) as erro:
        splits.require_validation("a" * 64)

    assert "não é válida" in str(erro.value)


def test_split_recusa_cruzada_sem_hash_do_inventario(relatorio):
    relatorio(
        _validacao(
            cross_source_contamination={"skipped": False, "valid": True, "inventory_sha256": None}
        )
    )

    with pytest.raises(SystemExit) as erro:
        splits.require_validation("a" * 64)

    assert "SHA-256 do inventário" in str(erro.value)


def test_split_recusa_inventario_divergente_entre_blocos(relatorio):
    relatorio(_validacao(integrity={"boxes_manifest_sha256": "a" * 64, "rdd_inventory_sha256": "outro"}))

    with pytest.raises(SystemExit) as erro:
        splits.require_validation("a" * 64)

    assert "não é o mesmo usado na comparação" in str(erro.value)


def test_split_recusa_ausencia_do_bloco_de_cruzada(relatorio):
    payload = _validacao()
    del payload["cross_source_contamination"]
    relatorio(payload)

    with pytest.raises(SystemExit) as erro:
        splits.require_validation("a" * 64)

    assert "não foi executada" in str(erro.value)


# --------------------------------- o split não se declara pronto para avaliar


def test_split_e_candidato_e_nao_teste_operacional():
    assert splits.USAGE == "CANDIDATE_EXTERNAL_TEST_BR"


# ------------------------------- catálogo: o UNIVALI não é fonte de avaliação


def test_catalogo_nao_declara_univali_pronto_para_avaliacao():
    """Declarar EXTERNAL_TEST prometeria uma medição que o artefato não sustenta."""
    from app.datasets.catalog import DatasetUsage, get_source

    univali = get_source("univali_br")

    assert DatasetUsage.EXTERNAL_TEST not in univali.usage
    assert DatasetUsage.TEST not in univali.usage
    assert DatasetUsage.VALIDATION not in univali.usage
    assert "NOT_EVALUATION_READY" in univali.usage_note


def test_univali_nunca_alimenta_treino():
    from app.datasets.catalog import DatasetUsage, get_source

    univali = get_source("univali_br")

    assert DatasetUsage.TRAIN not in univali.usage
    assert not univali.feeds_training


def test_catalogo_lista_os_bloqueios_para_sair_de_candidato():
    from app.datasets.catalog import get_source

    requisito = get_source("univali_br").unlock_requirement

    assert requisito
    for pista in ("auditoria humana", "máscaras vazias", "D40"):
        assert pista in requisito, f"o bloqueio '{pista}' sumiu do catálogo"
