"""Split V2 do RDD2022: estratificado dentro de cada país, isolado por grupo de hash.

Motivo da mudança em relação ao V1 (`make_splits.py`): o V1 particionou por país
inteiro (train=India/Japan/Norway, validation=China, test=Czech/US). Isso é
máximamente seguro contra leakage, mas produziu uma validation com 5 negativos
(0,13%) e um deslocamento de domínio de ~40x no prior de D40 entre train e test,
o que tornou a seleção de checkpoint cega a falsos positivos. Evidência:
`datasets/reports/rdd2022_geometry.json` e `rdd2022_split_v2_policy.json`.

O V2 estratifica DENTRO de cada país para que train/validation/holdout compartilhem
o mesmo domínio, e mantém o isolamento de leakage por componentes conexos de
hash (SHA-256 exato + dHash near-duplicate). Sequência/rota não existe no
RDD2022 e a adjacência de índice de nome de arquivo foi medida e REJEITADA como
proxy de sessão (ver `sequence_proxy_evidence`), portanto não é usada.

Somente leitura sobre `datasets/raw`; nenhuma imagem é copiada ou movida.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime

import numpy as np
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    require_local,
    write_json_report,
    write_text_safe,
)

SELECTION = DATASETS_DIR / "manifests" / "rdd2022_subset_selection.jsonl"
SPLITS_DIR = DATASETS_DIR / "splits"
CLASSES = ("URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40")
ROLES = ("train", "validation", "frozen_test")
TARGET = {"train": 0.70, "validation": 0.15, "frozen_test": 0.15}
NEAR_THRESHOLD = 8
SEED = 20260920
PROBE_ROLE = "domain_shift_probe"
# O TEST do V1 (Czech + United_States inteiros) já foi observado e analisado:
# virou HISTORICAL_BASELINE_TEST e não pode alimentar treino, validation nem o
# holdout congelado do V2. A v1 deste script reservava só Czech e deixava 4805
# imagens de United_States do TEST V1 em train/validation/frozen_test. Agora a
# população inteira do TEST V1 vai para a sonda report-only, e qualquer grupo de
# hash que toque uma imagem dela vai junto (sem quase-duplicata vazando).
V1_TEST_SPLIT = SPLITS_DIR / "rdd2022_subset_test.txt"
DEFAULT_PROBE_COUNTRIES = ("Czech", "United_States")

POPCOUNT = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(axis=1).astype(np.uint8)


def load_rows() -> list[dict]:
    rows = []
    for line in require_local(SELECTION).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def hamming_edges(hashes: list[str], thresholds: tuple[int, ...]) -> dict[int, list[tuple[int, int]]]:
    """Pares com distância de Hamming <= t, por limiar. dHash de 128 bits."""
    usable = [(index, value) for index, value in enumerate(hashes) if value]
    indices = np.array([index for index, _ in usable], dtype=np.int64)
    packed = np.array(
        [list(bytes.fromhex(value)) for _, value in usable], dtype=np.uint8
    )
    found: dict[int, list[tuple[int, int]]] = {t: [] for t in thresholds}
    limit = max(thresholds)
    chunk = 512
    for start in range(0, len(packed), chunk):
        block = packed[start : start + chunk]
        distances = POPCOUNT[np.bitwise_xor(block[:, None, :], packed[None, :, :])].sum(axis=2)
        rows, cols = np.nonzero(distances <= limit)
        for row, col in zip(rows, cols):
            left, right = start + int(row), int(col)
            if left >= right:
                continue
            distance = int(distances[row, col])
            for t in thresholds:
                if distance <= t:
                    found[t].append((int(indices[left]), int(indices[right])))
    return found


def sequence_proxy_evidence(rows: list[dict]) -> dict:
    """Mede se índice consecutivo de nome de arquivo indica frames da mesma sessão."""
    bycountry = defaultdict(list)
    for row in rows:
        match = re.search(r"(\d+)$", row["rel_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0])
        if match and row.get("dhash128"):
            bycountry[row["country"]].append((int(match.group(1)), row["dhash128"]))
    evidence = {}
    generator = random.Random(SEED)
    for country, items in sorted(bycountry.items()):
        items.sort()
        consecutive = [
            (int(h1, 16) ^ int(h2, 16)).bit_count()
            for (i1, h1), (i2, h2) in itertools.pairwise(items)
            if i2 - i1 == 1
        ]
        sampled = [
            (int(a[1], 16) ^ int(b[1], 16)).bit_count()
            for a, b in (generator.sample(items, 2) for _ in range(min(3000, len(items) * 2)))
        ]
        if consecutive and sampled:
            evidence[country] = {
                "consecutive_pairs": len(consecutive),
                "consecutive_hamming_median": statistics.median(consecutive),
                "random_hamming_median": statistics.median(sampled),
                "consecutive_within_8_pct": round(
                    100 * sum(1 for d in consecutive if d <= 8) / len(consecutive), 3
                ),
            }
    verdict = (
        "REJECTED_AS_SESSION_PROXY"
        if all(
            item["consecutive_hamming_median"] >= 0.9 * item["random_hamming_median"]
            for item in evidence.values()
        )
        else "POSSIBLE_SIGNAL_REQUIRES_REVIEW"
    )
    return {
        "method": "distância de Hamming dHash128 entre índices consecutivos vs pares aleatórios do mesmo país",
        "verdict": verdict,
        "interpretation": (
            "Índices consecutivos são tão distantes quanto pares aleatórios: os nomes do "
            "RDD2022 não preservam ordem de captura. Agrupar por adjacência de índice "
            "seria inventar metadata inexistente, portanto não é usado."
        ),
        "by_country": evidence,
    }


def build_groups(rows: list[dict]) -> tuple[list[int], dict]:
    """Componentes conexos por SHA-256 exato e near-duplicate dHash."""
    union = UnionFind(len(rows))
    by_sha = defaultdict(list)
    for index, row in enumerate(rows):
        if row.get("sha256"):
            by_sha[row["sha256"]].append(index)
    exact_groups = 0
    for members in by_sha.values():
        if len(members) > 1:
            exact_groups += 1
            for other in members[1:]:
                union.union(members[0], other)
    thresholds = (4, NEAR_THRESHOLD, 12, 16)
    edges = hamming_edges([row.get("dhash128", "") for row in rows], thresholds)
    for left, right in edges[NEAR_THRESHOLD]:
        union.union(left, right)
    labels = [union.find(index) for index in range(len(rows))]
    sizes = Counter(labels)
    sensitivity = {
        f"threshold_{t}": {
            "edges": len(edges[t]),
            "images_touched": len({i for pair in edges[t] for i in pair}),
        }
        for t in thresholds
    }
    policy = {
        "controls_used": ["sha256_exact", f"dhash128_near_hamming_le_{NEAR_THRESHOLD}"],
        "controls_unavailable": ["route", "session", "gps_track", "capture_timestamp"],
        "near_threshold": NEAR_THRESHOLD,
        "near_threshold_rationale": (
            "O relatório herdado usou 4. Elevar para 8 amplia a margem contra "
            "quase-duplicatas sem colapsar grupos legítimos, e o custo medido é "
            "pequeno (ver threshold_sensitivity)."
        ),
        "threshold_sensitivity": sensitivity,
        "exact_duplicate_groups": exact_groups,
        "leakage_groups_total": len(sizes),
        "largest_group": max(sizes.values()),
        "groups_larger_than_one": sum(1 for size in sizes.values() if size > 1),
        "guarantee": (
            "Nenhum componente conexo de hash atravessa train/validation/frozen_test: "
            "a atribuição é feita por grupo, nunca por imagem."
        ),
        "residual_risk": (
            "Frames da mesma sessão com distância de Hamming > 8 permanecem "
            "indetectáveis: o RDD2022 não publica rota/sessão e a ordem de nome foi "
            "medida e rejeitada como proxy. Risco documentado, não eliminado."
        ),
    }
    return labels, policy


def load_v1_test_paths() -> set[str]:
    text = require_local(V1_TEST_SPLIT).read_text(encoding="utf-8-sig")
    return {line.strip() for line in text.splitlines() if line.strip()}


def stratify(
    rows: list[dict],
    labels: list[int],
    probe_countries: tuple[str, ...],
    excluded_paths: set[str],
) -> dict[int, str]:
    """Aloca grupos por país, priorizando classes raras e paridade de negativos.

    Os países da sonda e todo grupo que contenha uma imagem de `excluded_paths`
    (TEST V1) são retirados antes da estratificação: vão inteiros para
    `domain_shift_probe` e nunca entram em train/validation/holdout.
    """
    groups: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        groups[label].append(index)

    by_country: dict[str, list[int]] = defaultdict(list)
    probe_labels: list[int] = []
    for label, members in groups.items():
        country = rows[members[0]]["geographic_country"]
        if country in probe_countries or any(
            rows[index]["rel_path"] in excluded_paths for index in members
        ):
            probe_labels.append(label)
            continue
        by_country[country].append(label)
    missing = set(probe_countries) - {
        rows[groups[label][0]]["geographic_country"] for label in probe_labels
    }
    if missing:
        raise SystemExit(f"país da sonda inexistente na seleção: {sorted(missing)}")

    def stats(indices: list[int]) -> dict[str, float]:
        out = {"images": float(len(indices)), "negatives": 0.0}
        for cls in CLASSES:
            out[cls] = 0.0
        for index in indices:
            row = rows[index]
            out["negatives"] += float(bool(row.get("is_negative")))
            for cls, count in (row.get("classes") or {}).items():
                out[cls] += float(count)
        return out

    assignment: dict[int, str] = {}
    generator = random.Random(SEED)
    for country, labels_of_country in sorted(by_country.items()):
        totals = stats([i for label in labels_of_country for i in groups[label]])
        targets = {
            role: {key: value * TARGET[role] for key, value in totals.items()}
            for role in ROLES
        }
        current = {role: dict.fromkeys(totals, 0.0) for role in ROLES}
        # Grupos com classes raras primeiro: decidem o balanço e têm menos folga.
        ordered = sorted(
            labels_of_country,
            key=lambda label: (
                -stats(groups[label])[ "URMIND_ROAD_D40"],
                -len(groups[label]),
                generator.random(),
            ),
        )
        for label in ordered:
            group_stats = stats(groups[label])
            best_role, best_score = None, None
            for role in ROLES:
                # Déficit relativo agregado; normaliza cada eixo pelo próprio alvo.
                score = 0.0
                for key, target_value in targets[role].items():
                    if target_value <= 0:
                        continue
                    deficit = (target_value - current[role][key]) / target_value
                    weight = 3.0 if key == "URMIND_ROAD_D40" else 2.0 if key == "negatives" else 1.0
                    score += weight * deficit
                if best_score is None or score > best_score:
                    best_role, best_score = role, score
            assert best_role is not None
            assignment[label] = best_role
            for key, value in group_stats.items():
                current[best_role][key] += value
    for label in probe_labels:
        assignment[label] = PROBE_ROLE
    return assignment


def summarize(rows: list[dict], indices: list[int]) -> dict:
    boxes: Counter = Counter()
    images_with: Counter = Counter()
    negatives = 0
    for index in indices:
        row = rows[index]
        negatives += int(bool(row.get("is_negative")))
        for cls, count in (row.get("classes") or {}).items():
            boxes[cls] += count
            images_with[cls] += 1
    countries = Counter(rows[index]["geographic_country"] for index in indices)
    total_boxes = sum(boxes.values())
    return {
        "images": len(indices),
        "objects": total_boxes,
        "negative_images": negatives,
        "negative_pct": round(100 * negatives / len(indices), 3) if indices else 0.0,
        "class_boxes": {cls: boxes[cls] for cls in CLASSES},
        "images_per_class": {cls: images_with[cls] for cls in CLASSES},
        "class_distribution_pct": {
            cls: round(100 * boxes[cls] / total_boxes, 3) if total_boxes else 0.0
            for cls in CLASSES
        },
        "by_country": dict(sorted(countries.items())),
        "size_bytes": sum(rows[index]["size_bytes"] for index in indices),
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    probe_countries = DEFAULT_PROBE_COUNTRIES
    all_roles = (*ROLES, PROBE_ROLE)
    v1_test_paths = load_v1_test_paths()

    rows = load_rows()
    print(f"seleção: {len(rows)} imagens", flush=True)
    sequence_evidence = sequence_proxy_evidence(rows)
    print(f"proxy de sessão: {sequence_evidence['verdict']}", flush=True)
    labels, leakage_policy = build_groups(rows)
    print(
        f"grupos de leakage: {leakage_policy['leakage_groups_total']} "
        f"(>1 imagem: {leakage_policy['groups_larger_than_one']})",
        flush=True,
    )
    assignment = stratify(rows, labels, probe_countries, v1_test_paths)

    members: dict[str, list[int]] = {role: [] for role in all_roles}
    for index, label in enumerate(labels):
        members[assignment[label]].append(index)
    # Trava dura: nenhuma imagem do HISTORICAL_BASELINE_TEST fora da sonda.
    v1_test_leaks = sum(
        1 for role in ROLES for index in members[role] if rows[index]["rel_path"] in v1_test_paths
    )
    if v1_test_leaks:
        raise SystemExit(f"{v1_test_leaks} imagens do TEST V1 caíram em papéis do V2")

    # Verificação dura de leakage: nenhum grupo e nenhum hash atravessa papéis.
    group_roles = defaultdict(set)
    sha_roles = defaultdict(set)
    dhash_roles = defaultdict(set)
    for role, indices in members.items():
        for index in indices:
            group_roles[labels[index]].add(role)
            sha_roles[rows[index]["sha256"]].add(role)
            if rows[index].get("dhash128"):
                dhash_roles[rows[index]["dhash128"]].add(role)
    violations = {
        "groups_crossing_roles": sum(1 for roles in group_roles.values() if len(roles) > 1),
        "sha256_crossing_roles": sum(1 for roles in sha_roles.values() if len(roles) > 1),
        "identical_dhash_crossing_roles": sum(
            1 for roles in dhash_roles.values() if len(roles) > 1
        ),
    }

    manifest_files = {}
    if not args.no_report:
        for role, indices in members.items():
            path = SPLITS_DIR / f"rdd2022_v2_{role}.txt"
            write_text_safe(
                path,
                "".join(f"{rows[index]['rel_path']}\n" for index in sorted(indices, key=lambda i: rows[i]["rel_path"])),
            )
            manifest_files[role] = {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": file_sha256(path),
            }

    document = {
        "script": "scripts/datasets/make_splits_v2.py",
        "algorithm_version": 2,
        "algorithm_change": (
            "v2: a sonda passa a conter a população inteira do TEST V1 (Czech + "
            "United_States) e todo grupo de hash que a toque. A v1 deixava 4805 "
            "imagens de United_States do TEST V1 em train/validation/frozen_test."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": "rdd2022",
        "dataset_version_name": "rdd2022-model-v2-quality-rebuild",
        "supersedes": "datasets/splits/rdd2022_subset_splits.json",
        "supersedes_note": (
            "O split V1 permanece intacto e continua sendo a única referência do "
            "ModelVersion V1. Nada aqui reescreve arquivos do V1."
        ),
        "seed": SEED,
        "selection_manifest": SELECTION.relative_to(PROJECT_ROOT).as_posix(),
        "selection_manifest_sha256": file_sha256(SELECTION),
        "ratios_target": TARGET,
        "stratification": (
            "dentro de cada país; grupos ordenados por carga de D40 e alocados ao papel "
            "com maior déficit relativo ponderado (D40 3x, negativos 2x, demais 1x)"
        ),
        "roles": {
            "train": "TRAIN_V2",
            "validation": "VALIDATION_V2",
            "frozen_test": "FROZEN_INTERNAL_TEST_V2",
            PROBE_ROLE: "DOMAIN_SHIFT_PROBE_V2",
        },
        "domain_shift_probe": {
            "countries": list(probe_countries),
            "role": "DOMAIN_SHIFT_PROBE_V2",
            "usage": "REPORT_ONLY",
            "rationale": (
                "É exatamente a população do TEST V1 (HISTORICAL_BASELINE_TEST), já "
                "observada na PHASE 2. Fica fora de treino, validation e holdout do "
                "V2; como nenhum dos dois modelos treinou nesses países, serve só "
                "como sonda de domain shift em relatório final."
            ),
            "v1_test_split": V1_TEST_SPLIT.relative_to(PROJECT_ROOT).as_posix(),
            "v1_test_split_sha256": file_sha256(V1_TEST_SPLIT),
            "v1_test_images_outside_probe": 0,
            "is_v1_historical_test": True,
            "forbidden_uses": [
                "training",
                "hard-negative mining",
                "seleção de augmentation",
                "seleção de hiperparâmetro",
                "seleção de checkpoint",
                "calibração de confidence/NMS",
                "early stopping",
            ],
        },
        "leakage_group_policy": leakage_policy,
        "sequence_proxy_evidence": sequence_evidence,
        "leakage_violations": violations,
        "splits": {role: summarize(rows, members[role]) for role in all_roles},
        "manifest_files": manifest_files,
        "v1_comparability": {
            "status": "NOT_INDEPENDENT_FOR_V1_COMPARISON",
            "reason": (
                "O V1 treinou em India+Japan+Norway completos e selecionou checkpoint "
                "em China, logo imagens de VALIDATION_V2 e FROZEN_INTERNAL_TEST_V2 já "
                "foram vistas pelo V1. Os conjuntos são honestos para o V2 (congelados "
                "antes do treino V2), mas não medem generalização independente do V1."
            ),
        },
        "frozen_test_usage_policy": [
            "proibido em training",
            "proibido em seleção de augmentation",
            "proibido em seleção de hiperparâmetro",
            "proibido em seleção de checkpoint",
            "proibido em calibração de confidence/NMS",
            "abertura única após congelamento do operating point",
        ],
    }
    if not args.no_report:
        write_json_report("rdd2022_v2_splits.json", document, subdir="splits")
    for role in all_roles:
        summary = document["splits"][role]
        print(
            f"{role:<13} images={summary['images']:<6} neg={summary['negative_images']:<6} "
            f"({summary['negative_pct']}%) "
            + " ".join(f"{cls[-3:]}={summary['class_boxes'][cls]}" for cls in CLASSES)
        )
    print(f"violations: {violations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
