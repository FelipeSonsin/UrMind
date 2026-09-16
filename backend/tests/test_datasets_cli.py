"""O CLI é o integrador real da camada de dataset — e não tinha um único teste.

`python -m app.datasets.cli` é por onde o sistema efetivamente encadeia
catálogo → inventário → adaptador → validação → payload de `dataset_versions`.
Todas as travas construídas nas camadas de baixo só valem se sobreviverem a esse
caminho: um portão que funciona em teste unitário e é contornado pelo comando que
as pessoas realmente executam não protege nada.

Estes testes exercitam o fluxo, não as funções isoladas. Onde o estado real deve
bloquear, o teste exige o bloqueio — nenhum sucesso é simulado.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.datasets.cli import build_parser, main

# ------------------------------------------------------------------ fixtures


def _chain(tmp_path: Path, *, boxes=None, stale=False, sem_manifesto=False) -> Path:
    """Cadeia derivada mínima do Urban Community, em `datasets/` temporário."""
    datasets = tmp_path / "datasets"
    raw = datasets / "raw" / "urban_community"
    raw.mkdir(parents=True)
    (datasets / "manifests").mkdir()
    (datasets / "reports").mkdir()
    (raw / "urban-community-issues.zip").write_bytes(b"pacote")

    scan = [
        {
            "folder": "pothole",
            "stem": "1",
            "image_relpath": (
                "datasets/raw/urban_community/Data_sets/Data_sets/pothole/images/1.jpg"
            ),
            "image_width": 100,
            "image_height": 100,
            "boxes": [{"index": 0, "class_id": 3}],
        }
    ]
    linha = {
        "dataset_id": "urban_community",
        "stem": "1",
        "folder": "pothole",
        "image_relpath": scan[0]["image_relpath"],
        "image_width": 100,
        "image_height": 100,
        "group": "folder:pothole",
        "annotation_completeness": "COMPLETE",
        "duplicate_review_status": "NOT_IN_DUPLICATE_PAIR",
        "boxes": boxes
        if boxes is not None
        else [
            {
                "source_label": "pothole",
                "geometry_valid": True,
                "source_category_validated": True,
                "quarantine_reasons": [],
                "human_decision": None,
                "xmin": 10,
                "ymin": 10,
                "xmax": 50,
                "ymax": 50,
            }
        ],
    }

    scan_path = datasets / "manifests" / "urban_community_scan.jsonl"
    boxes_path = datasets / "manifests" / "urban_community_boxes.jsonl"
    scan_path.write_text(json.dumps(scan[0]) + "\n", encoding="utf-8")
    if not sem_manifesto:
        boxes_path.write_text(json.dumps(linha) + "\n", encoding="utf-8")

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    (datasets / "reports" / "urban_community_conversion.json").write_text(
        json.dumps(
            {
                "mapping": {"semantic_mapping_approved": True},
                "integrity": {
                    "scan_manifest_sha256": sha(scan_path),
                    "boxes_manifest_sha256": ("0" * 64)
                    if stale
                    else (sha(boxes_path) if boxes_path.is_file() else None),
                },
            }
        ),
        encoding="utf-8",
    )
    (datasets / "reports" / "urban_community_validation.json").write_text(
        json.dumps(
            {
                "passed": True,
                "duplicates": {
                    "exact_sha256_groups": [],
                    "blocking_near_duplicate_pairs": [],
                },
                "integrity": {
                    "cross_source_valid": True,
                    "conversion_report_sha256": sha(
                        datasets / "reports" / "urban_community_conversion.json"
                    ),
                    "boxes_manifest_sha256": sha(boxes_path)
                    if boxes_path.is_file()
                    else None,
                },
            }
        ),
        encoding="utf-8",
    )
    return datasets / "raw"


APROVADA = {
    "source_label": "pothole",
    "geometry_valid": True,
    "source_category_validated": True,
    "quarantine_reasons": [],
    "human_decision": "approved_pothole",
    "xmin": 10,
    "ymin": 10,
    "xmax": 50,
    "ymax": 50,
}


# ------------------------------------------------------- parser e argumentos


def test_comando_valido_e_aceito():
    args = build_parser().parse_args(["read", "urban_community"])

    assert args.command == "read"
    assert args.dataset_id == "urban_community"


def test_comando_inexistente_e_recusado():
    with pytest.raises(SystemExit) as erro:
        build_parser().parse_args(["treinar", "urban_community"])

    assert erro.value.code == 2


def test_comando_obrigatorio_ausente_e_recusado():
    with pytest.raises(SystemExit) as erro:
        build_parser().parse_args([])

    assert erro.value.code == 2


def test_dataset_id_inexistente_e_recusado_pelo_parser():
    """O catálogo é a lista de escolhas: id fora dele nem chega ao comando."""
    with pytest.raises(SystemExit) as erro:
        build_parser().parse_args(["read", "dataset_que_nao_existe"])

    assert erro.value.code == 2


def test_dataset_id_valido_e_aceito():
    for dataset_id in ("rdd2022", "univali_br", "urban_community"):
        assert build_parser().parse_args(["read", dataset_id]).dataset_id == dataset_id


# --------------------------------------------- read: fail-closed da derivada


def test_read_sem_manifesto_autorizado_falha_e_nao_le_o_raw(tmp_path, capsys):
    raiz = _chain(tmp_path, sem_manifesto=True)

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1
    assert "ausente" in capsys.readouterr().err


def test_read_com_manifesto_desatualizado_falha(tmp_path, capsys):
    raiz = _chain(tmp_path, stale=True)

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1
    assert "diverge" in capsys.readouterr().err


def test_read_com_hash_divergente_apos_a_conversao_falha(tmp_path, capsys):
    raiz = _chain(tmp_path)
    manifesto = tmp_path / "datasets" / "manifests" / "urban_community_boxes.jsonl"
    manifesto.write_text(manifesto.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1
    assert "desatualizada" in capsys.readouterr().err


def test_read_com_revisao_humana_pendente_nao_produz_caixa(tmp_path, capsys):
    raiz = _chain(tmp_path)

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])
    saida = capsys.readouterr().out

    assert codigo == 0
    # O comando funciona; o que ele não faz é produzir classe da V1.
    assert "URMIND_ROAD_D40" not in saida


# ------------------------------------------- register: o portão que importa


def test_register_recusa_urban_community_sem_caixa_autorizada(tmp_path, capsys):
    """O estado real hoje: nenhuma caixa passou pelos portões, logo não registra."""
    raiz = _chain(tmp_path)

    codigo = main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])

    assert codigo == 2
    assert "nenhuma amostra" in capsys.readouterr().err


def test_register_nao_grava_manifesto_quando_recusa(tmp_path):
    raiz = _chain(tmp_path)

    main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum", "--write"])

    assert not (tmp_path / "datasets" / "manifests" / "urban_community.json").is_file()


def test_register_de_fonte_proibida_de_avaliar_nao_pede_validacao_nem_teste(tmp_path, capsys):
    """Com caixa aprovada, o registro precisa passar — com split só de treino.

    É o defeito que a revisão adversarial apontou: o comando usava as proporções
    padrão 70/15/15 para toda fonte TRAINING_V1, e o portão do §8.4 abortava o
    registro de uma fonte que o próprio catálogo proíbe de avaliar.
    """
    raiz = _chain(tmp_path, boxes=[APROVADA])

    codigo = main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])
    saida = capsys.readouterr().out

    assert codigo == 0
    payload = json.loads(saida)
    assert payload["split"]["split"]["counts"] == {"train": 1, "validation": 0, "test": 0}
    assert payload["classes"] == ["URMIND_ROAD_D40"]


def test_erro_em_etapa_intermediaria_interrompe_as_seguintes(tmp_path):
    """Cadeia quebrada não vira payload: o register para antes de montar nada."""
    raiz = _chain(tmp_path, sem_manifesto=True)

    codigo = main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])

    assert codigo == 1
    assert not (tmp_path / "datasets" / "manifests" / "urban_community.json").is_file()


# ------------------------------------------------ inventário e estado do disco


def test_inventory_json_lista_todas_as_fontes_do_catalogo(tmp_path, capsys):
    from app.datasets.catalog import SOURCES

    codigo = main(["--raw-root", str(tmp_path), "inventory", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert codigo == 0
    assert {i["dataset_id"] for i in payload} == {s.id for s in SOURCES}


def test_inventory_marca_fonte_ausente_em_vez_de_inventar_estado(tmp_path, capsys):
    codigo = main(["--raw-root", str(tmp_path), "inventory", "--json"])
    payload = {i["dataset_id"]: i for i in json.loads(capsys.readouterr().out)}

    assert codigo == 0
    assert payload["urban_community"]["total_bytes"] == 0
    assert payload["urban_community"]["state"] != "ready"


def test_cloud_only_nao_e_hidratado_pelo_inventario(tmp_path, capsys, monkeypatch):
    """Marcador do OneDrive não pode virar download por causa de um comando."""
    raiz = _chain(tmp_path)
    abertos: list[str] = []

    original = Path.read_bytes

    def espiao(self, *a, **k):
        abertos.append(self.name)
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", espiao)
    main(["--raw-root", str(raiz), "inventory"])

    assert "urban-community-issues.zip" not in abertos


# ------------------------------------------------------ UNIVALI não avalia


def test_univali_nao_esta_pronto_para_avaliacao_no_catalogo():
    """`register` não pode ser a porta que promove o candidato a teste externo."""
    from app.datasets.catalog import DatasetUsage, get_source

    fonte = get_source("univali_br")

    assert DatasetUsage.UNUSABLE in fonte.usage
    assert DatasetUsage.EXTERNAL_TEST in fonte.potential_usage
    assert DatasetUsage.EXTERNAL_TEST not in fonte.usage
    assert fonte.unlock_requirement


def test_split_do_univali_declara_que_nao_esta_pronto():
    caminho = (
        Path(__file__).resolve().parents[2]
        / "datasets"
        / "splits"
        / "univali_br_external_test_splits.json"
    )
    if not caminho.is_file():
        pytest.skip("split do UNIVALI ainda não gerado neste checkout")

    payload = json.loads(caminho.read_text(encoding="utf-8"))

    assert payload["usage"] == "CANDIDATE_EXTERNAL_TEST_BR"
    assert payload["evaluation_readiness"]["is_detection_evaluation_ready"] is False
    assert payload["evaluation_readiness"]["blockers"]


# -------- a cadeia precisa amarrar TAMBÉM o relatório de conversão


def _conversion_path(tmp_path: Path) -> Path:
    return tmp_path / "datasets" / "reports" / "urban_community_conversion.json"


def _relink(tmp_path: Path) -> None:
    """Regrava no validation o sha atual da conversão — cadeia íntegra."""
    conversao = _conversion_path(tmp_path)
    validacao = tmp_path / "datasets" / "reports" / "urban_community_validation.json"
    payload = json.loads(validacao.read_text(encoding="utf-8"))
    payload["integrity"]["conversion_report_sha256"] = hashlib.sha256(
        conversao.read_bytes()
    ).hexdigest()
    validacao.write_text(json.dumps(payload), encoding="utf-8")


def test_cadeia_integra_com_conversao_vinculada_funciona(tmp_path, capsys):
    raiz = _chain(tmp_path, boxes=[APROVADA])
    _relink(tmp_path)

    codigo = main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])

    assert codigo == 0
    assert json.loads(capsys.readouterr().out)["classes"] == ["URMIND_ROAD_D40"]


def test_conversao_alterada_depois_da_validacao_bloqueia(tmp_path, capsys):
    """Caixas intactas e validação aprovada não bastam: a conversão mudou."""
    raiz = _chain(tmp_path, boxes=[APROVADA])
    _relink(tmp_path)
    conversao = _conversion_path(tmp_path)
    payload = json.loads(conversao.read_text(encoding="utf-8"))
    payload["nota"] = "editado depois da validação"
    conversao.write_text(json.dumps(payload), encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1
    assert "conversão mudou depois da validação" in capsys.readouterr().err


def test_alterar_apenas_o_semantic_mapping_approved_bloqueia(tmp_path, capsys):
    """O ataque óbvio: ligar o portão de taxonomia editando um booleano."""
    raiz = _chain(tmp_path, boxes=[APROVADA])
    conversao = _conversion_path(tmp_path)
    payload = json.loads(conversao.read_text(encoding="utf-8"))
    payload["mapping"]["semantic_mapping_approved"] = False
    conversao.write_text(json.dumps(payload), encoding="utf-8")
    _relink(tmp_path)
    payload["mapping"]["semantic_mapping_approved"] = True
    conversao.write_text(json.dumps(payload), encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1
    assert "conversão mudou depois da validação" in capsys.readouterr().err


def test_sem_relatorio_de_validacao_nenhuma_caixa_e_autorizada(tmp_path, capsys):
    raiz = _chain(tmp_path, boxes=[APROVADA])
    (tmp_path / "datasets" / "reports" / "urban_community_validation.json").unlink()

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "URMIND_ROAD_D40" not in saida


def test_validacao_de_outra_versao_das_caixas_nao_autoriza(tmp_path, capsys):
    raiz = _chain(tmp_path, boxes=[APROVADA])
    _relink(tmp_path)
    validacao = tmp_path / "datasets" / "reports" / "urban_community_validation.json"
    payload = json.loads(validacao.read_text(encoding="utf-8"))
    payload["integrity"]["boxes_manifest_sha256"] = "c" * 64
    validacao.write_text(json.dumps(payload), encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "URMIND_ROAD_D40" not in saida


# --------------------- registro de fonte proibida não avisa falso


def test_registro_train_only_nao_carrega_aviso_contraditorio(tmp_path, capsys):
    raiz = _chain(tmp_path, boxes=[APROVADA])
    _relink(tmp_path)

    main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["split"]["split"]["warnings"] == []
    assert payload["split"]["split"]["expected_empty"] == ["validation", "test"]


# ------------ validação sem vínculo com a conversão não abre portão nenhum


def _validacao(tmp_path: Path) -> Path:
    return tmp_path / "datasets" / "reports" / "urban_community_validation.json"


@pytest.mark.parametrize(
    ("valor", "descricao"),
    [
        (None, "ausente"),
        ("", "vazio"),
        ("nao-e-um-sha", "malformado"),
        ("A" * 64, "maiúsculo fora do formato"),
    ],
)
def test_hash_da_conversao_invalido_bloqueia(tmp_path, capsys, valor, descricao):
    raiz = _chain(tmp_path, boxes=[APROVADA])
    validacao = _validacao(tmp_path)
    payload = json.loads(validacao.read_text(encoding="utf-8"))
    if valor is None:
        payload["integrity"].pop("conversion_report_sha256", None)
    else:
        payload["integrity"]["conversion_report_sha256"] = valor
    validacao.write_text(json.dumps(payload), encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "read", "urban_community"])

    assert codigo == 1, descricao
    assert "conversion_report_sha256" in capsys.readouterr().err


def test_validacao_antiga_sem_o_campo_nao_aproveita_resultados_aprovados(tmp_path, capsys):
    """`passed`, duplicata e contaminação aprovados não valem sem o vínculo."""
    raiz = _chain(tmp_path, boxes=[APROVADA])
    validacao = _validacao(tmp_path)
    payload = json.loads(validacao.read_text(encoding="utf-8"))
    del payload["integrity"]["conversion_report_sha256"]
    assert payload["passed"] and payload["integrity"]["cross_source_valid"]
    validacao.write_text(json.dumps(payload), encoding="utf-8")

    codigo = main(["--raw-root", str(raiz), "register", "urban_community", "--skip-checksum"])

    assert codigo == 1
    assert not (tmp_path / "datasets" / "manifests" / "urban_community.json").is_file()
