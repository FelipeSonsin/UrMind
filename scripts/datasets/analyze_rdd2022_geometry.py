"""Geometria por classe/país/split do RDD2022: base de evidência do D40_ROOT_CAUSE_ANALYSIS.

Somente leitura sobre `datasets/raw`. Reaproveita a seleção reconciliada
(`rdd2022_subset_selection.jsonl`) como inventário corrente; não reexecuta a
auditoria completa. Mede a caixa no espaço do modelo (letterbox 640) porque é
nele que o detector decide, e não no pixel original.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    require_local,
    write_json_report,
)
from _taxonomy import load_class_mapping

SELECTION = DATASETS_DIR / "manifests" / "rdd2022_subset_selection.jsonl"
SPLITS_DIR = DATASETS_DIR / "splits"
INPUT_SIDE = 640.0
# Limiares COCO no espaço do modelo; separam o que o FPN consegue resolver.
SMALL_MAX_AREA = 32.0 * 32.0
MEDIUM_MAX_AREA = 96.0 * 96.0
CLASSES = ("URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40")


def percentiles(values: list[float], points=(5, 25, 50, 75, 95)) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for point in points:
        index = min(len(ordered) - 1, max(0, round(point / 100 * (len(ordered) - 1))))
        out[f"p{point}"] = round(ordered[index], 6)
    out["mean"] = round(statistics.fmean(ordered), 6)
    return out


def load_selection() -> dict[str, dict]:
    rows = {}
    for line in require_local(SELECTION).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["rel_path"]] = row
    return rows


def load_splits() -> dict[str, str]:
    split_of = {}
    for name in ("train", "validation", "test"):
        path = SPLITS_DIR / f"rdd2022_subset_{name}.txt"
        for line in require_local(path).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                split_of[line.strip()] = name
    return split_of


def iter_boxes(row: dict, mapping: dict):
    """Rende (classe_canônica, largura, altura) no espaço do modelo 640 letterboxed."""
    annotation = row.get("annotation_path")
    if not annotation:
        return
    path = PROJECT_ROOT / annotation
    if not path.exists():
        return
    try:
        root = ET.parse(require_local(path)).getroot()
    except ET.ParseError:
        return
    width, height = row.get("width"), row.get("height")
    if not width or not height:
        return
    ratio = min(INPUT_SIDE / height, INPUT_SIDE / width)
    for obj in root.findall("object"):
        label = (obj.findtext("name") or "").strip()
        canonical = mapping["accepted"].get(label)
        if not canonical:
            continue
        try:
            x0, y0, x1, y1 = (
                float(obj.findtext("bndbox/" + key, "nan"))
                for key in ("xmin", "ymin", "xmax", "ymax")
            )
        except (TypeError, ValueError):
            continue
        box_width, box_height = (x1 - x0) * ratio, (y1 - y0) * ratio
        if box_width <= 0 or box_height <= 0:
            continue
        yield canonical, box_width, box_height


def blank_bucket() -> dict:
    return {
        "boxes": 0,
        "areas": [],
        "aspect_log2": [],
        "widths": [],
        "heights": [],
        "small": 0,
        "medium": 0,
        "large": 0,
    }


def finalize(bucket: dict) -> dict:
    total = bucket["boxes"]
    if not total:
        return {"boxes": 0}
    return {
        "boxes": total,
        "size_buckets": {
            "small": bucket["small"],
            "medium": bucket["medium"],
            "large": bucket["large"],
        },
        "size_buckets_pct": {
            key: round(100 * bucket[key] / total, 2)
            for key in ("small", "medium", "large")
        },
        "area_model_space": percentiles(bucket["areas"]),
        "relative_area_of_input": percentiles(
            [area / (INPUT_SIDE * INPUT_SIDE) for area in bucket["areas"]]
        ),
        "width_model_space": percentiles(bucket["widths"]),
        "height_model_space": percentiles(bucket["heights"]),
        # log2(w/h): 0 = quadrado, >0 alongado na horizontal (transversal à via),
        # <0 alongado na vertical (longitudinal à via, em câmera frontal).
        "aspect_ratio_log2": percentiles(bucket["aspect_log2"]),
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    mapping = load_class_mapping("rdd2022")
    selection = load_selection()
    split_of = load_splits()

    by_class = defaultdict(blank_bucket)
    by_class_split = defaultdict(blank_bucket)
    by_class_country = defaultdict(blank_bucket)
    cooccurrence = Counter()
    boxes_per_image = defaultdict(list)
    images_with_class = defaultdict(Counter)
    processed = 0

    for rel_path, split in split_of.items():
        row = selection.get(rel_path)
        if not row:
            continue
        country = row["geographic_country"]
        present = Counter()
        for canonical, box_width, box_height in iter_boxes(row, mapping):
            area = box_width * box_height
            aspect = (box_width / box_height) if box_height else 0.0
            for bucket in (
                by_class[canonical],
                by_class_split[(canonical, split)],
                by_class_country[(canonical, country)],
            ):
                bucket["boxes"] += 1
                bucket["areas"].append(area)
                bucket["widths"].append(box_width)
                bucket["heights"].append(box_height)
                if aspect > 0:
                    bucket["aspect_log2"].append(math.log2(aspect))
                if area < SMALL_MAX_AREA:
                    bucket["small"] += 1
                elif area < MEDIUM_MAX_AREA:
                    bucket["medium"] += 1
                else:
                    bucket["large"] += 1
            present[canonical] += 1
        boxes_per_image[split].append(sum(present.values()))
        for canonical in present:
            images_with_class[split][canonical] += 1
        if present:
            cooccurrence[tuple(sorted(present))] += 1
        processed += 1
        if processed % 5000 == 0:
            print(f"{processed} imagens processadas", flush=True)

    report = {
        "algorithm_version": 1,
        "purpose": "evidência geométrica para D40_ROOT_CAUSE_ANALYSIS e desenho do split V2",
        "measurement_space": f"letterbox {int(INPUT_SIDE)}x{int(INPUT_SIDE)} (espaço de decisão do modelo)",
        "size_thresholds_model_space": {
            "small": f"area < {int(SMALL_MAX_AREA)}",
            "medium": f"{int(SMALL_MAX_AREA)} <= area < {int(MEDIUM_MAX_AREA)}",
            "large": f"area >= {int(MEDIUM_MAX_AREA)}",
        },
        "images_processed": processed,
        "selection_manifest": str(SELECTION.relative_to(PROJECT_ROOT).as_posix()),
        "by_class": {key: finalize(value) for key, value in sorted(by_class.items())},
        "by_class_split": {
            f"{cls}|{split}": finalize(value)
            for (cls, split), value in sorted(by_class_split.items())
        },
        "by_class_country": {
            f"{cls}|{country}": finalize(value)
            for (cls, country), value in sorted(by_class_country.items())
        },
        "class_cooccurrence": {
            "+".join(combo): count for combo, count in cooccurrence.most_common()
        },
        "boxes_per_image": {
            split: percentiles([float(v) for v in values])
            for split, values in sorted(boxes_per_image.items())
        },
        "images_with_class": {
            split: dict(sorted(counter.items()))
            for split, counter in sorted(images_with_class.items())
        },
    }
    if not args.no_report:
        write_json_report("rdd2022_geometry.json", report)
    print(json.dumps({"images_processed": processed}, ensure_ascii=False))
    for cls in CLASSES:
        stats = report["by_class"].get(cls, {})
        if stats.get("boxes"):
            print(
                f"{cls}: boxes={stats['boxes']} "
                f"small={stats['size_buckets_pct']['small']}% "
                f"med={stats['size_buckets_pct']['medium']}% "
                f"large={stats['size_buckets_pct']['large']}% "
                f"area_p50={stats['area_model_space']['p50']} "
                f"aspect_log2_p50={stats['aspect_ratio_log2']['p50']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
