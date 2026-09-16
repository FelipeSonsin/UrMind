"""Splits por países e componentes de duplicatas; somente listas relativas."""

import argparse
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict

from _budget import preflight
from _core import (
    DATASETS_DIR,
    configure_stdout,
    file_sha256,
    require_local,
    timestamp,
    write_json_report,
    write_text_safe,
)
from audit_rdd2022 import INVENTORY, summarize
from find_duplicates import UnionFind
from propose_subset import SELECTION


def dividir(rows, duplicates, ratios, seed):
    if (
        len(ratios) != 3
        or any(not math.isfinite(r) or r <= 0 for r in ratios)
        or abs(sum(ratios) - 1) > 1e-9
    ):
        raise ValueError("três proporções positivas e finitas devem somar 1")
    by_path = {r["rel_path"]: r for r in rows}
    if len(by_path) != len(rows):
        raise ValueError("manifesto contém caminhos repetidos")
    groups = sorted({r["group"] for r in rows})
    if not groups or any(not g for g in groups):
        raise ValueError("grupos ausentes")
    uf = UnionFind(groups)
    for cluster in duplicates["exact_duplicates"] + duplicates["near_duplicates"]:
        related = sorted(
            {by_path[p]["group"] for p in cluster["files"] if p in by_path}
        )
        for g in related[1:]:
            uf.union(related[0], g)
    components = defaultdict(list)
    for row in rows:
        components[uf.find(row["group"])].append(row)
    names = sorted(components)
    if len(names) < 3:
        raise ValueError(
            "menos de três grupos independentes após união de duplicatas; splits bloqueados para revisão"
        )
    if len(names) > 12:
        raise ValueError(
            "muitos grupos para busca exaustiva; requer algoritmo de split apropriado"
        )
    total = len(rows)
    best = None
    # Poucos países: busca todas as atribuições, exige classes presentes em cada conjunto.
    full = summarize(rows)
    required = set(full["class_counts"])
    summaries = {name: summarize(items) for name, items in components.items()}
    require_negatives = any(row["is_negative"] for row in rows)
    for assignment in itertools.product(range(3), repeat=len(names)):
        if len(set(assignment)) != 3:
            continue
        sizes = [0, 0, 0]
        classes = [set(), set(), set()]
        negatives = [0, 0, 0]
        objects = [Counter(), Counter(), Counter()]
        for name, split in zip(names, assignment):
            sizes[split] += len(components[name])
            classes[split].update(summaries[name]["class_counts"])
            negatives[split] += summaries[name]["negative_images"]
            objects[split].update(summaries[name]["class_counts"])
        missing = sum(len(required - c) for c in classes)
        if require_negatives:
            missing += sum(n == 0 for n in negatives)
        error = sum((sizes[i] / total - ratios[i]) ** 2 for i in range(3))
        # Mesma escala de probabilidades: deriva de classes e taxa de negativos
        # têm peso agregado igual ao erro das proporções, sem igualar classes.
        distribution_error = (
            sum(
                sum(
                    (
                        objects[i].get(c, 0) / max(sum(objects[i].values()), 1)
                        - full["class_counts"][c] / full["objects"]
                    )
                    ** 2
                    for c in sorted(required)
                )
                + (negatives[i] / sizes[i] - full["negative_images"] / total) ** 2
                for i in range(3)
            )
            / 3
        )
        error += distribution_error
        tie = hashlib.sha256(f"{seed}:{assignment}".encode()).hexdigest()
        score = (missing, round(error, 12), tie)
        if best is None or score < best[0]:
            best = (score, assignment)
    result = {name: [] for name in ("train", "validation", "test")}
    for name, index in zip(names, best[1]):
        result[("train", "validation", "test")[index]].extend(components[name])
    return result, {g: uf.find(g) for g in groups}, best[0][:2]


def verify_splits(splits, duplicates):
    owner = {}
    group_owner = {}
    hash_owner = {}
    for split, rows in splits.items():
        for row in rows:
            path = row["rel_path"]
            if path in owner:
                raise ValueError("imagem em múltiplos splits")
            owner[path] = split
            for mapping, key in (
                (group_owner, row["group"]),
                (hash_owner, row["sha256"]),
            ):
                if key in mapping and mapping[key] != split:
                    raise ValueError("grupo/hash em múltiplos splits")
                mapping[key] = split
    for cluster in duplicates["exact_duplicates"] + duplicates["near_duplicates"]:
        if len({owner[p] for p in cluster["files"] if p in owner}) > 1:
            raise ValueError("cluster visual/exato em múltiplos splits")
    return {
        "passed": True,
        "groups_checked": len(group_owner),
        "sha256_checked": len(hash_owner),
        "near_clusters_checked": len(duplicates["near_duplicates"]),
        "scope": "grupos e duplicatas detectados; não garante ausência de relações não documentadas",
    }


def main():
    configure_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=20260908)
    p.add_argument("--train", type=float, default=0.70)
    p.add_argument("--val", type=float, default=0.15)
    p.add_argument("--test", type=float, default=0.15)
    p.add_argument("--no-report", action="store_true")
    a = p.parse_args()
    preflight("manifestos de split", 16_000_000, 32_000_000, raise_on_block=True)
    rows = [
        json.loads(line)
        for line in require_local(SELECTION).read_text(encoding="utf-8").splitlines()
    ]
    path = DATASETS_DIR / "reports" / "rdd2022_duplicates.json"
    dup = json.loads(require_local(path).read_text(encoding="utf-8"))
    if dup["source_inventory_sha256"] != file_sha256(INVENTORY):
        raise ValueError("relatório de duplicatas desatualizado")
    result, components, score = dividir(rows, dup, (a.train, a.val, a.test), a.seed)
    checks = verify_splits(result, dup)
    report = {
        # Proveniencia exigida de toda derivada
        # (artifact_contract.yaml#derived_manifest_contract).
        "script": "scripts/datasets/make_splits.py",
        "source_dataset": "rdd2022",
        "source_version": "figshare-21431547-v1 (2022-crddc)",
        "generated_at": timestamp(),
        "inputs": {"images": len(rows)},
        # Split é partição, não filtro: toda imagem do manifesto cai em
        # exatamente um lado. O campo fica explícito para não confundir
        # "nada foi descartado" com "ninguém contou".
        "dropped": {"images": 0},
        "drop_reasons": {
            "none": "partição: toda imagem do manifesto de seleção entra em um split"
        },
        "algorithm_version": 4,
        "seed": a.seed,
        "source_manifest_sha256": file_sha256(SELECTION),
        "duplicates_report_sha256": file_sha256(path),
        "ratios_target": [a.train, a.val, a.test],
        "algorithm": "união países/duplicatas; busca exaustiva: cobertura de classes/negativos, depois soma do erro quadrático de proporções + deriva média de classes/taxa de negativos",
        "country_components": components,
        "optimization_score": score,
        "leakage_check": checks,
        "status": "proposal_requires_human_review; freeze before future training",
        "physical_copy_created": False,
        "splits": {},
    }
    for name, items in result.items():
        text = "".join(
            r["rel_path"] + "\n" for r in sorted(items, key=lambda r: r["rel_path"])
        )
        report["splits"][name] = {
            **summarize(items),
            "groups": sorted({r["group"] for r in items}),
            "fraction": len(items) / len(rows),
            "manifest_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        if not a.no_report:
            write_text_safe(
                DATASETS_DIR / "splits" / f"rdd2022_subset_{name}.txt", text
            )
    if not a.no_report:
        write_json_report("rdd2022_subset_splits.json", report, "splits")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
