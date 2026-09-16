"""Regras da conversão derivada do UNIVALI, exercitadas sem tocar no dataset real.

`convert()` decide o que vira caixa, o que é descartado e com que motivo. Cada
descarte silencioso aqui viraria uma métrica errada lá na frente, então os
motivos fazem parte do contrato e são testados como tal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from convert_univali_masks import NATIVE_LABEL, convert, validate_box


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


def scan_row(directory="1007599_RS_386_386RS289112_28920", *, components=(), **overrides):
    row = {
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
    row.update(overrides)
    return row


# ------------------------------------------------------------------- geometria


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        (component(0, 0, 10, 10), []),
        ({"xmin": 5, "ymin": 0, "xmax": 5, "ymax": 10}, ["x1 >= x2"]),
        ({"xmin": 0, "ymin": 7, "xmax": 10, "ymax": 7}, ["y1 >= y2"]),
        ({"xmin": -1, "ymin": 0, "xmax": 10, "ymax": 10}, ["x1 fora de [0, largura)"]),
        ({"xmin": 0, "ymin": 0, "xmax": 2000, "ymax": 10}, ["x2 fora de (0, largura]"]),
        ({"xmin": 0, "ymin": 700, "xmax": 10, "ymax": 800}, ["y1 fora de [0, altura)", "y2 fora de (0, altura]"]),
    ],
)
def test_invariantes_geometricas(box, expected):
    assert validate_box(box, 1024, 640) == expected


# ------------------------------------------------------------------- conversão


def test_cada_regiao_desconectada_vira_uma_caixa():
    rows = [scan_row(components=[component(10, 10, 40, 40), component(100, 100, 130, 130)])]

    manifest, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert len(manifest) == 1
    assert len(manifest[0]["boxes"]) == 2
    assert summary["class_counts"] == {NATIVE_LABEL: 2}


def test_caixa_nao_recebe_classe_da_v1_sem_evidencia():
    """§8.2: promover POTHOLE a D40 por semelhança de nome é proibido."""
    rows = [scan_row(components=[component(10, 10, 40, 40)])]

    manifest, _ = convert(rows, min_area=0, mapping_evidence=None)
    box = manifest[0]["boxes"][0]

    assert box["derived_label"] == NATIVE_LABEL
    assert box["urmind_class"] is None
    assert box["source_label"] == "POTHOLE"


def test_com_evidencia_registrada_a_classe_da_v1_e_aplicada():
    rows = [scan_row(components=[component(10, 10, 40, 40)])]
    evidencia = {"urmind_class": "URMIND_ROAD_D40"}

    manifest, _ = convert(rows, min_area=0, mapping_evidence=evidencia)

    assert manifest[0]["boxes"][0]["urmind_class"] == "URMIND_ROAD_D40"


def test_mascara_vazia_fica_registrada_e_nao_vira_negativo():
    """Sumir do manifesto escondia 1.671 de 2.235 imagens do domínio."""
    rows = [scan_row(components=[])]

    manifest, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert len(manifest) == 1
    assert manifest[0]["boxes"] == []
    assert manifest[0]["mask_status"] == "EMPTY_MASK_SEMANTICS_UNRESOLVED"
    assert summary["empty_mask_samples"] == 1
    assert summary["drops"] == {}


def test_imagem_ilegivel_e_descartada_com_motivo():
    rows = [scan_row(image_error="OSError: truncated")]

    manifest, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert manifest == []
    assert summary["drops"]["imagem ilegível ou ausente"] == 1


def test_mascara_desalinhada_da_imagem_e_descartada():
    row = scan_row(components=[component(10, 10, 40, 40)])
    row["masks"]["POTHOLE"]["observed"] = {"width": 800, "height": 600}

    manifest, summary = convert([row], min_area=0, mapping_evidence=None)

    assert manifest == []
    assert summary["drops"]["máscara desalinhada da imagem"] == 1


def test_caixa_fora_do_limite_e_descartada_e_nao_recortada():
    """Recortar em silêncio inventaria uma anotação que a fonte não fez."""
    row = scan_row(components=[component(1000, 10, 1200, 40)])

    manifest, summary = convert([row], min_area=0, mapping_evidence=None)

    # A amostra continua no manifesto — o que sumiu foi a caixa, com motivo.
    assert manifest[0]["boxes"] == []
    assert manifest[0]["mask_status"] == "POSITIVE_MASK"
    assert summary["images_foreground_without_box"] == 1
    assert any("caixa inválida" in motivo for motivo in summary["drops"])


def test_regiao_na_borda_e_mantida_e_marcada():
    rows = [scan_row(components=[component(0, 0, 30, 30, border=True)])]

    manifest, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert manifest[0]["boxes"][0]["touches_border"] is True
    assert summary["border_boxes"] == 1


def test_padrao_nao_descarta_por_area():
    """min_area=0 é o padrão declarado: nada some por ser pequeno."""
    rows = [scan_row(components=[component(5, 5, 6, 6, area=1)])]

    manifest, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert len(manifest[0]["boxes"]) == 1
    assert not any("área <" in motivo for motivo in summary["drops"])


def test_limiar_declarado_descarta_e_registra_o_motivo():
    rows = [scan_row(components=[component(5, 5, 6, 6, area=1), component(10, 10, 60, 60)])]

    manifest, summary = convert(rows, min_area=100, mapping_evidence=None)

    assert len(manifest[0]["boxes"]) == 1
    assert summary["drops"]["região com área < 100 px (limiar declarado)"] == 1


def test_custo_de_cada_limiar_candidato_e_medido():
    rows = [scan_row(components=[component(0, 0, 2, 2, area=4), component(10, 10, 60, 60, area=2500)])]

    _, summary = convert(rows, min_area=0, mapping_evidence=None)

    assert summary["threshold_cost"]["8"] == 1
    assert summary["threshold_cost"]["1"] == 0


def test_manifesto_referencia_caminho_e_nao_copia_imagem():
    rows = [scan_row(components=[component(10, 10, 40, 40)])]

    manifest, _ = convert(rows, min_area=0, mapping_evidence=None)
    entrada = manifest[0]

    assert entrada["derived"] is True
    assert entrada["image_relpath"].startswith("datasets/raw/univali_br/")
    assert entrada["mask_relpath"].endswith("_POTHOLE.png")
    assert "image_bytes" not in entrada


def test_trinca_e_faixa_ficam_registradas_como_recusadas():
    rows = [scan_row(components=[component(10, 10, 40, 40)])]

    manifest, _ = convert(rows, min_area=0, mapping_evidence=None)
    recusados = {r["label"] for r in manifest[0]["rejected_labels"]}

    assert recusados == {"CRACK", "LANE"}
    motivos = {r["label"]: r["reason"] for r in manifest[0]["rejected_labels"]}
    assert "D00/D10/D20" in motivos["CRACK"]


def test_grupos_de_split_sao_preservados_no_manifesto():
    rows = [scan_row(components=[component(10, 10, 40, 40)])]

    manifest, _ = convert(rows, min_area=0, mapping_evidence=None)

    assert manifest[0]["group_road"] == "RS_386"
    assert manifest[0]["group_segment"] == "RS_386_386RS289112"
    assert manifest[0]["group_uf"] == "RS"
