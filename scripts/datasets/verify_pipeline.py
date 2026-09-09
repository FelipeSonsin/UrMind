"""Verificações reais da cadeia de dados, sem treino, dados artificiais ou escritas em raw."""

import ast
import hashlib
import json
from unittest.mock import patch

from _budget import load_budget, preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    RAW_DIR,
    configure_stdout,
    file_sha256,
    output_path,
    require_local,
    write_json_report,
)
from _taxonomy import trainable_classes
from audit_rdd2022 import INVENTORY, read_inventory, summarize
from find_duplicates import UnionFind, detect
from make_splits import dividir, verify_splits
from propose_subset import SELECTION, select
from validate_portability import validate_path


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def load(path):
    return json.loads(require_local(path).read_text(encoding="utf-8-sig"))


def main():
    configure_stdout()
    rows = read_inventory()
    by_path = {r["rel_path"]: r for r in rows}
    check(len(by_path) == len(rows), "caminhos duplicados no inventário")
    proposal = load(DATASETS_DIR / "reports" / "rdd2022_subset_proposal.json")
    audit = load(DATASETS_DIR / "reports" / "rdd2022_audit.json")
    dup = load(DATASETS_DIR / "reports" / "rdd2022_duplicates.json")
    split_report = load(DATASETS_DIR / "splits" / "rdd2022_subset_splits.json")
    selected = [
        json.loads(line)
        for line in require_local(SELECTION).read_text(encoding="utf-8").splitlines()
    ]
    check(
        audit["inventory_sha256"] == file_sha256(INVENTORY),
        "hash do inventário diverge",
    )
    check(
        proposal["source_inventory_sha256"] == file_sha256(INVENTORY),
        "proposta desatualizada",
    )
    check(
        proposal["manifest_sha256"] == file_sha256(SELECTION),
        "manifesto de seleção alterado",
    )
    check(
        dup["source_inventory_sha256"] == file_sha256(INVENTORY),
        "duplicatas desatualizadas",
    )
    check(
        split_report["source_manifest_sha256"] == file_sha256(SELECTION),
        "split desatualizado",
    )
    check(
        split_report["duplicates_report_sha256"]
        == file_sha256(DATASETS_DIR / "reports" / "rdd2022_duplicates.json"),
        "clusters do split desatualizados",
    )
    check(summarize(rows) == audit["totals"], "estatísticas do inventário divergem")
    check(summarize(selected) == proposal["subset"], "estatísticas do subset divergem")
    allowed = set(trainable_classes())
    for r in rows:
        check(set(r["classes"]) <= allowed, "classe fora da taxonomia")
        check(
            r["n_objects"] == sum(r["classes"].values()),
            "contagem de objetos inconsistente",
        )
        check(
            not r["is_negative"]
            or (r["n_objects_original"] == 0 and not r["annotation_errors"]),
            "negativo inválido",
        )
    expected, _, _, _ = select(list(reversed(rows)), proposal["seed"])
    check(expected == selected, "seleção depende da ordem do sistema de arquivos")
    reconstructed, _, _ = dividir(
        list(reversed(selected)),
        dup,
        tuple(split_report["ratios_target"]),
        split_report["seed"],
    )
    actual = {}
    for name in ("train", "validation", "test"):
        path = DATASETS_DIR / "splits" / f"rdd2022_subset_{name}.txt"
        content = require_local(path).read_text(encoding="utf-8")
        paths = content.splitlines()
        check(
            hashlib.sha256(content.encode()).hexdigest()
            == split_report["splits"][name]["manifest_sha256"],
            "hash do split diverge",
        )
        check(
            paths == sorted(r["rel_path"] for r in reconstructed[name]),
            "split não reproduzível",
        )
        actual[name] = [by_path[p] for p in paths]
    check(
        sum(len(v) for v in actual.values()) == len(selected),
        "cobertura do split incorreta",
    )
    leakage = verify_splits(actual, dup)
    # Testes de fronteiras do orçamento; valores simulam recursos, não dados de treinamento.
    budget = load_budget()
    check(budget.max_total_ml_bytes == 40_000_000_000, "unidade do limite incorreta")
    with patch("_budget.free_disk_bytes", return_value=100_000_000_000):
        check(
            preflight(
                "boundary",
                1,
                1,
                current_datasets_bytes=39_999_999_999,
                current_ml_bytes=39_999_999_999,
            ).allowed,
            "fronteira exata recusada",
        )
        check(
            not preflight(
                "overflow",
                2,
                2,
                current_datasets_bytes=39_999_999_999,
                current_ml_bytes=39_999_999_999,
            ).allowed,
            "overflow aceito",
        )
        check(
            not preflight(
                "temporary_peak",
                0,
                2_000_000_000,
                current_datasets_bytes=39_000_000_000,
                current_ml_bytes=39_000_000_000,
            ).allowed,
            "pico ignorado",
        )
    with patch("_budget.free_disk_bytes", return_value=10_000_000_000):
        check(
            not preflight(
                "disk", 1, 1, current_datasets_bytes=0, current_ml_bytes=0
            ).allowed,
            "margem SO ignorada",
        )
    for value in (-1, float("nan")):
        try:
            preflight("bad", value, current_datasets_bytes=0, current_ml_bytes=0)
        except ValueError:
            pass
        else:
            raise AssertionError("estimativa inválida aceita")
    for bad in (RAW_DIR / "forbidden.txt", PROJECT_ROOT / "outside.txt"):
        try:
            output_path(bad)
        except RuntimeError:
            pass
        else:
            raise AssertionError("escrita fora das pastas derivadas permitida")
    for bad in ("../escape", "/escape", "Z:/escape", "datasets/../escape"):
        try:
            validate_path(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("caminho inseguro aceito")
    # Confere o índice visual contra força bruta em imagens REAIS, sem gerar dados.
    sample = [r for r in rows if r.get("dhash128") and r.get("gray_stddev", 0) >= 10][
        :400
    ]
    _, near, _ = detect(sample, 4)
    uf = UnionFind([r["rel_path"] for r in sample])
    for i, r in enumerate(sample):
        for other in sample[:i]:
            if (int(r["dhash128"], 16) ^ int(other["dhash128"], 16)).bit_count() <= 4:
                uf.union(r["rel_path"], other["rel_path"])
    for cluster in near:
        check(
            len({uf.find(p) for p in cluster["files"]}) == 1,
            "índice visual cria relação inexistente",
        )
    # Compatibilidade nominal de taxonomia com backend, sem importá-lo/executá-lo.
    schema = PROJECT_ROOT / "backend" / "app" / "schemas" / "core.py"
    schema_text = require_local(schema).read_text(encoding="utf-8")
    check(all(c in schema_text for c in allowed), "schema backend diverge da taxonomia")
    for path in sorted((PROJECT_ROOT / "scripts" / "datasets").glob("*.py")):
        ast.parse(require_local(path).read_text(encoding="utf-8-sig"))
    check(
        all(
            (DATASETS_DIR / name).is_dir()
            for name in ("raw", "metadata", "manifests", "splits", "reports")
        ),
        "estrutura efetiva incompleta",
    )
    check(
        all(
            not (DATASETS_DIR / name).exists() for name in ("processed", "annotations")
        ),
        "pasta vazia removida foi recriada",
    )
    downloads = DATASETS_DIR / "downloads"
    check(
        not downloads.exists() or any(p.is_file() for p in downloads.rglob("*")),
        "downloads deve existir somente quando tiver uso efetivo",
    )
    result = {
        "passed": True,
        "real_images_checked": len(rows),
        "selected_images_checked": len(selected),
        "checks": [
            "artifact_hash_chain",
            "summary_recalculation",
            "negative_definition",
            "taxonomy",
            "selection_order_independence",
            "split_order_independence",
            "split_coverage",
            "duplicate_group_isolation",
            "budget_boundaries",
            "peak_storage",
            "disk_reserve",
            "raw_write_refusal",
            "path_traversal_refusal",
            "visual_index_real_sample",
            "python_syntax",
            "active_directory_structure",
        ],
        "leakage": leakage,
        "backend_integration": "taxonomy IDs compatible; new manifests not yet consumed by legacy backend; no runtime/training tested",
    }
    preflight("relatório verificações", 100_000, 200_000, raise_on_block=True)
    write_json_report("pipeline_verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
