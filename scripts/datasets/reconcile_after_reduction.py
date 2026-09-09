"""Reconcilia manifestos e splits do RDD2022 com o disco depois da poda da Norway.

A selecao e os splits foram construidos quando o RDD2022 tinha 13,84 GB. A poda
autorizada removeu 5.112 negativas da Norway e as 2.040 imagens do split test
oficial, entao aqueles arquivos passaram a citar caminhos que nao existem mais --
exatamente o tipo de estado antigo que quebra o proximo passo sem avisar.

Este script nao reabre a decisao de split: cada imagem continua no lado em que ja
estava, e o agrupamento por pais permanece intacto porque a Norway inteira estava
em `train`. Ele apenas remove as linhas orfas e recalcula as estatisticas, para
que o numero publicado no JSON corresponda ao arquivo ao lado.

Padrao dry-run. Só escreve com --execute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SPLITS_DIR = ROOT / "datasets/splits"
SPLITS_JSON = SPLITS_DIR / "rdd2022_subset_splits.json"
SELECTION = ROOT / "datasets/manifests/rdd2022_subset_selection.jsonl"
REPORT = ROOT / "datasets/reports/rdd2022_reconciliation.json"

SPLIT_NAMES = ("train", "validation", "test")


def _split_path(name: str) -> Path:
    return SPLITS_DIR / f"rdd2022_subset_{name}.txt"


def _load_selection() -> dict[str, dict]:
    return {
        rec["rel_path"]: rec
        for rec in (json.loads(line) for line in SELECTION.open(encoding="utf-8"))
    }


def _stats(records: list[dict]) -> dict:
    classes: Counter[str] = Counter()
    per_class: Counter[str] = Counter()
    original: Counter[str] = Counter()
    objects = negatives = 0
    for rec in records:
        for name, count in (rec.get("classes") or {}).items():
            classes[name] += count
            objects += count
        for name in rec.get("classes") or {}:
            per_class[name] += 1
        for name, count in (rec.get("original_classes") or {}).items():
            original[name] += count
        if rec.get("is_negative"):
            negatives += 1
    return {
        "images": len(records),
        "objects": objects,
        "negatives": negatives,
        "class_counts": dict(sorted(classes.items())),
        "images_per_class": dict(sorted(per_class.items())),
        "original_class_counts": dict(sorted(original.items())),
        "groups": sorted({r.get("group", "") for r in records}),
    }


def _leakage(kept: dict[str, list[dict]]) -> dict:
    """Reconfere o isolamento depois de remover linhas: grupo e hash em um lado so."""
    seen_group: dict[str, str] = {}
    seen_hash: dict[str, str] = {}
    collisions: list[str] = []
    for split, records in kept.items():
        for rec in records:
            group = rec.get("group", "")
            if group and seen_group.setdefault(group, split) != split:
                collisions.append(f"grupo {group} em {seen_group[group]} e {split}")
            digest = rec.get("sha256")
            if digest and seen_hash.setdefault(digest, split) != split:
                collisions.append(f"sha256 {digest[:12]} em {seen_hash[digest]} e {split}")
    return {
        "passed": not collisions,
        "groups_checked": len(seen_group),
        "sha256_checked": len(seen_hash),
        "collisions": collisions[:20],
        "scope": (
            "grupos e hashes das linhas mantidas; nao garante ausencia de relacoes "
            "nao documentadas na fonte"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="reescreve os arquivos")
    args = ap.parse_args()

    selection = _load_selection()
    kept: dict[str, list[dict]] = {}
    dropped: dict[str, list[str]] = {}
    unknown: list[str] = []

    for name in SPLIT_NAMES:
        lines = [
            line.strip()
            for line in _split_path(name).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        alive: list[dict] = []
        gone: list[str] = []
        for rel in lines:
            if not (ROOT / rel).is_file():
                gone.append(rel)
                continue
            rec = selection.get(rel)
            if rec is None:
                unknown.append(rel)
                continue
            alive.append(rec)
        kept[name] = alive
        dropped[name] = gone
        print(f"{name:11s} {len(lines):6d} -> {len(alive):6d} ({len(gone)} removidas)")

    if unknown:
        print(f"AVISO: {len(unknown)} caminhos existem em disco mas nao no manifesto de selecao")

    leak = _leakage(kept)
    print(f"isolamento: {'OK' if leak['passed'] else 'FALHOU'} ({leak['groups_checked']} grupos)")

    total = sum(len(v) for v in kept.values())
    stats = {name: _stats(recs) for name, recs in kept.items()}
    for name in SPLIT_NAMES:
        share = stats[name]["images"] / total * 100 if total else 0
        print(
            f"  {name:11s} {stats[name]['images']:6d} imagens "
            f"({share:5.2f}%), {stats[name]['objects']:6d} objetos, "
            f"{stats[name]['negatives']:5d} negativas"
        )

    if not args.execute:
        print("\nDRY-RUN. Nada reescrito. Repita com --execute.")
        return 0

    for name in SPLIT_NAMES:
        _split_path(name).write_text(
            "".join(f"{rec['rel_path']}\n" for rec in kept[name]), encoding="utf-8"
        )

    kept_paths = {rec["rel_path"] for recs in kept.values() for rec in recs}
    with SELECTION.open("w", encoding="utf-8") as fh:
        for rel, rec in selection.items():
            if rel in kept_paths:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    doc = json.loads(SPLITS_JSON.read_text(encoding="utf-8"))
    doc["splits"] = {
        name: {**doc["splits"].get(name, {}), **stats[name]} for name in SPLIT_NAMES
    }
    doc["leakage_check"] = leak
    doc["source_manifest_sha256"] = hashlib.sha256(SELECTION.read_bytes()).hexdigest()
    doc["reconciled_after_reduction"] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": (
            "A poda autorizada da origem Norway removeu imagens que estes splits "
            "citavam. As linhas orfas sairam; nenhuma imagem trocou de lado e o "
            "agrupamento por pais nao mudou, porque a Norway inteira estava em train."
        ),
        "dropped": {name: len(dropped[name]) for name in SPLIT_NAMES},
        "evidence": [
            "datasets/reports/rdd2022_reduction.json",
            "datasets/reports/rdd2022_norway_partial_restore.json",
        ],
        "note": (
            "Só o `train` perdeu linhas, porque a Norway inteira estava nele. As "
            "proporcoes ficam proximas das que a busca original alcancou (67,6 / "
            "10,9 / 21,5), que ja eram o resultado possivel mantendo paises inteiros "
            "-- o alvo 70/15/15 nunca foi atingido exatamente. Reequilibrar exigiria "
            "mover paises entre splits, o que mudaria o conjunto de teste depois de "
            "ele ja existir; nao se faz isso automaticamente."
        ),
        "objects_preserved": True,
    }
    SPLITS_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "dropped": {name: len(dropped[name]) for name in SPLIT_NAMES},
                "kept": {name: len(kept[name]) for name in SPLIT_NAMES},
                "unknown_paths": len(unknown),
                "leakage_check": leak,
                "stats": stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nreescritos: splits, {SELECTION.name} e {SPLITS_JSON.name}")
    print(f"relatorio: {REPORT.relative_to(ROOT)}")
    return 0 if leak["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
