"""Propõe seleção conservadora: preserva todos os exemplos íntegros, sem meta de preenchimento."""

import argparse
import hashlib
import json
from collections import defaultdict

from _budget import load_budget, preflight
from _core import (
    DATASETS_DIR,
    configure_stdout,
    file_sha256,
    timestamp,
    write_json_report,
    write_text_safe,
)
from audit_rdd2022 import INVENTORY, read_inventory, summarize

SELECTION = DATASETS_DIR / "manifests" / "rdd2022_subset_selection.jsonl"


def select(rows, seed):
    selected, review, unlabeled = [], [], []
    exact = defaultdict(list)
    for row in rows:
        if row["source_split"] != "train":
            unlabeled.append(row["rel_path"])
            continue
        if (
            row["annotation_status"] not in ("positive", "negative")
            or row["image_errors"]
        ):
            review.append(
                {
                    "path": row["rel_path"],
                    "reason": row["annotation_status"],
                    "image_errors": row["image_errors"],
                }
            )
            continue
        exact[row["sha256"]].append(row)
    exclusions = []
    for digest, members in sorted(exact.items()):
        # Mantém duplicatas com anotações diferentes para revisão, sem escolher rótulo silenciosamente.
        signatures = {r["annotation_sha256"] for r in members}
        if len(members) > 1 and len(signatures) > 1:
            chosen = members
        else:
            chosen = [
                min(
                    members,
                    key=lambda r: hashlib.sha256(
                        f"{seed}:{r['rel_path']}".encode()
                    ).hexdigest(),
                )
            ]
            exclusions.extend(
                {
                    "path": r["rel_path"],
                    "reason": "exact_duplicate_same_annotation",
                    "sha256": digest,
                }
                for r in members
                if r not in chosen
            )
        for row in chosen:
            item = dict(row)
            item["selection_reason"] = (
                "negativo confirmado: XML válido sem objetos"
                if row["is_negative"]
                else "preservar todas as classes, origens e exemplos íntegros; sem corte por bytes"
            )
            if row.get("small_objects"):
                item["selection_reason"] += "; inclui objetos pequenos"
            if row.get("difficult_objects"):
                item["selection_reason"] += "; difficult declarado no XML"
            if len(members) > 1 and len(signatures) > 1:
                item["selection_reason"] += (
                    "; duplicata com anotação distinta exige revisão"
                )
            selected.append(item)
    return sorted(selected, key=lambda r: r["rel_path"]), review, unlabeled, exclusions


def main():
    configure_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=20260908)
    p.add_argument("--no-report", action="store_true")
    a = p.parse_args()
    check = preflight(
        "manifesto seleção e revisão", 96_000_000, 128_000_000, raise_on_block=True
    )
    rows = read_inventory()
    selected, review, unlabeled, excluded = select(rows, a.seed)
    stats = summarize(selected)
    if stats["size_bytes"] > load_budget().max_local_dataset_bytes:
        raise RuntimeError("seleção acima do orçamento datasets")
    report = {
        # Proveniencia exigida de toda derivada
        # (artifact_contract.yaml#derived_manifest_contract).
        "script": "scripts/datasets/propose_subset.py",
        "source_dataset": "rdd2022",
        "source_version": "figshare-21431547-v1 (2022-crddc)",
        "generated_at": timestamp(),
        "transform": "seleção determinística de todas as imagens de treino estruturalmente válidas",
        "params": {"seed": a.seed, "algorithm_version": 2},
        "inputs": len(rows),
        "outputs": len(selected),
        "dropped": len(rows) - len(selected),
        "drop_reasons": {
            "official_test_unlabeled": len(unlabeled),
            "exact_copies": len(excluded),
            "invalid_or_out_of_scope": len(rows)
            - len(selected)
            - len(unlabeled)
            - len(excluded),
        },
        "algorithm_version": 2,
        "seed": a.seed,
        "algorithm": "all_valid_training_examples; exact byte duplicate + same annotation only; SHA256(seed:path) tie break",
        "source_inventory_sha256": file_sha256(INVENTORY),
        "physical_copy_created": False,
        "full_set": summarize([r for r in rows if r["source_split"] == "train"]),
        "subset": stats,
        "by_country": {
            c: summarize([r for r in selected if r["country"] == c])
            for c in sorted({r["country"] for r in selected})
        },
        "review_images": review,
        "excluded_exact_copies": excluded,
        "official_test_unlabeled": len(unlabeled),
        "selection_policy": "Preservar todos os exemplos válidos. Não balancear artificialmente. Near-duplicates são mantidos e agrupados no split.",
        "size_policy": "12–14 GB é secundário. ZIP não é imagem de treino. Nenhum arquivo original removido.",
        "limitations": [
            "Qualidade semântica das caixas e dificuldade visual exigem revisão humana.",
            "Danos fora da V1 não são negativos; ficam no inventário e na fila de revisão.",
            "Teste do desafio sem labels não é teste de avaliação supervisionada.",
            "Não há garantia de representatividade brasileira no RDD2022.",
        ],
        "preflight": check.as_dict(),
    }
    if not a.no_report:
        write_text_safe(
            SELECTION,
            "".join(
                json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in selected
            ),
        )
        report["manifest_sha256"] = file_sha256(SELECTION)
        write_json_report("rdd2022_subset_proposal.json", report)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
