"""Lê imagens e keypoints dos Parquets originais, sem extrair/copiá-los em disco."""

import argparse
import hashlib
import io
import json
import math
from collections import Counter

import pyarrow.parquet as pq
from _budget import preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    require_local,
    write_json_report,
    write_text_safe,
)
from PIL import Image


def iter_samples(dataset):
    """Iterador independente do backend; bytes de imagem e labels originais em RAM."""
    plan = json.loads(
        require_local(DATASETS_DIR / "metadata/acquisition_plan.json").read_text(
            encoding="utf-8"
        )
    )
    for entry in plan["files"]:
        if entry["dataset"] != dataset:
            continue
        path = require_local(PROJECT_ROOT / entry["path"])
        if path.stat().st_size != entry["size_bytes"]:
            raise ValueError("Parquet incompleto")
        parquet = pq.ParquetFile(path, pre_buffer=False)
        index = 0
        for batch in parquet.iter_batches(batch_size=1, use_threads=False):
            for row in batch.to_pylist():
                yield entry, index, row
                index += 1


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, choices=("project_sidewalk", "rampnet")
    )
    args = parser.parse_args()
    preflight(
        "validação de Parquet e manifesto sem cópia de imagens",
        20_000_000,
        40_000_000,
        raise_on_block=True,
    )
    plan = json.loads(
        require_local(DATASETS_DIR / "metadata/acquisition_plan.json").read_text()
    )
    entries = [e for e in plan["files"] if e["dataset"] == args.dataset]
    for entry in entries:
        if file_sha256(PROJECT_ROOT / entry["path"]) != entry["sha256"]:
            raise ValueError("Parquet não corresponde ao SHA-256 oficial")
    inventory, issues = [], []
    splits, hashes, point_counts = Counter(), {}, Counter()
    for entry, index, row in iter_samples(args.dataset):
        data = row["image"]["bytes"]
        sha = hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
        normalized = args.dataset == "rampnet"
        points = row["curb_ramp_points_normalized"] if normalized else row["keypoints"]
        for point in points:
            x, y = (point["x"], point["y"]) if isinstance(point, dict) else point
            upper_x, upper_y = (1, 1) if normalized else (width, height)
            if not (
                math.isfinite(x)
                and math.isfinite(y)
                and 0 <= x <= upper_x
                and 0 <= y <= upper_y
                and (normalized or (x < width and y < height))
            ):
                issues.append(
                    {
                        "container_path": entry["path"],
                        "row_index": index,
                        "issue": "keypoint_outside_image",
                    }
                )
        if row.get("sha256") and row["sha256"] != sha:
            issues.append(
                {
                    "container_path": entry["path"],
                    "row_index": index,
                    "issue": "embedded_sha256_mismatch",
                }
            )
        if "n_keypoints" in row and row["n_keypoints"] != len(points):
            issues.append(
                {
                    "container_path": entry["path"],
                    "row_index": index,
                    "issue": "keypoint_count_mismatch",
                }
            )
        sample = {
            "container_path": entry["path"],
            "row_index": index,
            "source_dataset": args.dataset,
            "original_split": entry["official_split"],
            "sample_id": row.get("crop_id", row.get("pano_id")),
            "pano_id": row.get("pano_id"),
            "width": width,
            "height": height,
            "image_bytes": len(data),
            "image_sha256": sha,
            "keypoints_count": len(points),
            "original_keypoints": points,
            "keypoints_normalized": normalized,
            "geographic_coordinates": row.get("pano_coord"),
            "automatic_road_class_mapping": None,
        }
        inventory.append(sample)
        hashes.setdefault(sha, []).append(
            (entry["official_split"], sample["sample_id"])
        )
        splits[entry["official_split"]] += 1
        point_counts[entry["official_split"]] += len(points)
        if len(inventory) % 500 == 0:
            print(args.dataset, len(inventory), "decoded images", flush=True)
    duplicates = {k: v for k, v in hashes.items() if len(v) > 1}
    cross_split = {k: v for k, v in duplicates.items() if len({s for s, _ in v}) > 1}
    write_text_safe(
        DATASETS_DIR / "manifests" / f"{args.dataset}_acquired_inventory.jsonl",
        "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in inventory
        ),
    )
    report = {
        "dataset": args.dataset,
        "images_decoded": len(inventory),
        "keypoints": sum(point_counts.values()),
        "negative_images": sum(r["keypoints_count"] == 0 for r in inventory),
        "by_original_split": dict(splits),
        "points_by_original_split": dict(point_counts),
        "issues": issues,
        "format_validation_passed": bool(inventory) and not issues,
        "exact_duplicate_groups": duplicates,
        "exact_cross_split_groups": cross_split,
        "split_status": "upstream partitions recorded; no leakage-free training approval",
        "scope_limitations": "keypoints are not bounding boxes; full semantic/visual review not performed; no inference or training",
        "plan_sha256": file_sha256(DATASETS_DIR / "metadata/acquisition_plan.json"),
    }
    write_json_report(f"{args.dataset}_acquired_audit.json", report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "dataset",
                    "images_decoded",
                    "keypoints",
                    "format_validation_passed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
