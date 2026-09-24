"""Read-only scientific audit of the acquired IRD Dashcam CANDIDATE_HOLDOUT.

Uses the already versioned RDD2022 hash inventory, not protected TEST images.
It reports candidates for human review and never authorizes evaluation.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from _core import DATASETS_DIR, file_sha256, write_json_report
from audit_rdd2022 import inspect_image
from find_duplicates import detect

RAW = DATASETS_DIR / "raw" / "ird_dashcam"
ACQUISITION = DATASETS_DIR / "reports" / "ird_dashcam_candidate_acquisition.json"
RDD_INVENTORY = DATASETS_DIR / "manifests" / "rdd2022_inventory.jsonl"
RDD_DUPLICATES = DATASETS_DIR / "reports" / "rdd2022_duplicates.json"
ROLE_MANIFESTS = {
    "TRAIN_V2": "detection_v2_train_authorized.jsonl",
    "VALIDATION_V2": "detection_v2_validation_authorized.jsonl",
    "FROZEN_TEST_V2_PREVIOUS": "detection_v2_frozen_test_authorized.jsonl",
    "HISTORICAL_PROBE": "detection_v2_domain_shift_probe.jsonl",
}
E2E_DEMO_IMAGE = "datasets/raw/ird_dashcam/images/Dash_0559.jpg"
E2E_DEMO_SHA256 = "57250810885a9cdd4925a2355e16fabfdfb2787533291a2e0fb73d52e7c204b0"
SOURCE_CAPTURE_GROUP = "IRD-Dataset_v1.0.0:Dashcam:all_frames"


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _roles() -> dict[str, str]:
    result = {}
    for role, filename in ROLE_MANIFESTS.items():
        if not (DATASETS_DIR / "manifests" / filename).is_file():
            raise RuntimeError("DATASET_SPLIT_NOT_AVAILABLE: comparação histórica V2 indisponível")
        for row in _rows(DATASETS_DIR / "manifests" / filename):
            path = row["image_path"]
            if path in result:
                raise ValueError(f"RDD2022 image appears in two protected roles: {path}")
            result[path] = role
    return result


def _label_audit(names: set[str]) -> dict:
    class_counts = Counter()
    empty = 0
    invalid = []
    with zipfile.ZipFile(RAW / "IRD-Dataset_v1.0.0_labels_bbox.zip") as archive:
        for name in sorted(names):
            lines = archive.read(f"labels_bbox/{name}.txt").decode("utf-8").splitlines()
            if not any(line.strip() for line in lines):
                empty += 1
            for number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                parts = line.split()
                try:
                    if len(parts) != 5:
                        raise ValueError("field_count")
                    category = int(parts[0])
                    x, y, width, height = map(float, parts[1:])
                    if (
                        category not in range(5)
                        or not all(math.isfinite(v) for v in (x, y, width, height))
                        or width <= 0
                        or height <= 0
                        or x - width / 2 < -1e-6
                        or x + width / 2 > 1 + 1e-6
                        or y - height / 2 < -1e-6
                        or y + height / 2 > 1 + 1e-6
                    ):
                        raise ValueError("geometry_or_class")
                    class_counts[category] += 1
                except ValueError as exc:
                    invalid.append({"label": name, "line": number, "reason": str(exc)})
    return {
        "images": len(names),
        "empty_labels": empty,
        "source_class_instances": {str(i): class_counts[i] for i in range(5)},
        "invalid_lines": invalid,
    }


def _gps_audit(names: set[str]) -> dict:
    with zipfile.ZipFile(RAW / "IRD-Dataset_v1.0.0_gps_metadata.zip") as archive:
        source = archive.read("gps_metadata/IRD_Dataset_GPS_Metadata.csv")
    rows = list(csv.DictReader(io.StringIO(source.decode("utf-8-sig"))))
    dash = {row["image_name"].removesuffix(".jpg"): row for row in rows if row["source"] == "Dash"}
    if set(dash) != {f"Dash_{i:04d}" for i in range(1, 1007)}:
        raise ValueError("official Dash GPS inventory is incomplete")
    selected = [dash[name] for name in sorted(names)]
    coordinates = [(float(row["lat"]), float(row["lon"])) for row in selected]
    seconds = [float(dash[f"Dash_{i:04d}"]["t_sec"]) for i in range(1, 1007)]
    return {
        "selected_gps_rows": len(selected),
        "missing_coordinates": sum(not row["lat"] or not row["lon"] for row in selected),
        "lat_range": [min(lat for lat, _ in coordinates), max(lat for lat, _ in coordinates)],
        "lon_range": [min(lon for _, lon in coordinates), max(lon for _, lon in coordinates)],
        "repeated_exact_coordinates": len(coordinates) - len(set(coordinates)),
        "t_sec_nonmonotonic_transitions_full_source": sum(b < a for a, b in itertools.pairwise(seconds)),
        "capture_group_policy": "Treat entire Dashcam source as one group until routes/sessions are independently proven",
        "route_session_identity": "UNKNOWN",
    }


def audit() -> dict:
    acquisition = json.loads(ACQUISITION.read_text(encoding="utf-8"))
    if acquisition.get("status") != "CANDIDATE_HOLDOUT" or acquisition.get("authorized_for_test") is not False:
        raise ValueError("acquisition report is not a sealed candidate")
    license_text = (RAW / "LICENSE.txt").read_text(encoding="utf-8")
    if "Creative Commons Attribution 4.0 International (CC BY 4.0)" not in license_text:
        raise ValueError("source license does not declare CC BY 4.0")
    images = acquisition["images"]
    names = {Path(row["image"]).stem for row in images}
    if len(names) != len(images) or len(names) != acquisition["selected_images"]:
        raise ValueError("candidate image list is duplicated or incomplete")
    actual = {path.name for path in (RAW / "images").iterdir() if path.is_file()}
    expected = {f"{name}.jpg" for name in names}
    if actual != expected:
        raise ValueError("candidate directory differs from acquisition report")
    image_rows = []
    image_errors = []
    for row in images:
        path = RAW / row["image"]
        result = inspect_image(path)
        if result.get("sha256") != row["sha256"] or result.get("image_errors"):
            image_errors.append({"image": row["image"], "result": result})
        image_rows.append({
            "rel_path": f"datasets/raw/ird_dashcam/{row['image']}",
            "sha256": result.get("sha256"),
            "dhash128": result.get("dhash128"),
            "gray_stddev": result.get("gray_stddev", 0),
            "size_bytes": row["bytes"],
            "country": "Iraq",
            "image_width": result.get("image_width"),
            "image_height": result.get("image_height"),
        })
    previous = json.loads(RDD_DUPLICATES.read_text(encoding="utf-8"))
    if file_sha256(RDD_INVENTORY) != previous["source_inventory_sha256"]:
        raise ValueError("RDD hash inventory changed since its duplicate audit")
    rdd_rows = list(_rows(RDD_INVENTORY))
    roles = _roles()
    by_sha = defaultdict(list)
    for row in rdd_rows:
        by_sha[row["sha256"]].append(row["rel_path"])
    exact_cross = [
        {"ird": row["rel_path"], "rdd": path, "role": roles.get(path, "OTHER_RDD_HISTORY")}
        for row in image_rows
        for path in by_sha.get(row["sha256"], [])
    ]
    _, near_groups, _ = detect([*rdd_rows, *image_rows], threshold=4)
    near_cross = []
    near_internal = []
    for group in near_groups:
        candidate = [path for path in group["files"] if path.startswith("datasets/raw/ird_dashcam/")]
        if not candidate:
            continue
        historical = [path for path in group["files"] if not path.startswith("datasets/raw/ird_dashcam/")]
        if historical:
            near_cross.append({
                "ird": candidate,
                "rdd": [{"path": path, "role": roles.get(path, "OTHER_RDD_HISTORY")} for path in historical],
            })
        elif len(candidate) > 1:
            near_internal.append(candidate)
    demo = next((row for row in image_rows if row["rel_path"] == E2E_DEMO_IMAGE), None)
    source_demo = next(
        (row for row in images if f"datasets/raw/ird_dashcam/{row['image']}" == E2E_DEMO_IMAGE),
        None,
    )
    if (
        demo is None
        or source_demo is None
        or demo["sha256"] != E2E_DEMO_SHA256
        or 3 not in source_demo["source_class_ids"]
        or any(E2E_DEMO_IMAGE in group for group in near_internal)
        or any(E2E_DEMO_IMAGE == item["ird"] for item in exact_cross)
        or any(E2E_DEMO_IMAGE in item["ird"] for item in near_cross)
    ):
        raise ValueError("E2E demo image integrity or duplicate isolation failed")
    report = {
        "status": "CANDIDATE_HOLDOUT_AUDITED_AUTOMATICALLY",
        "official_evaluation_authorized": False,
        "training_authorized": False,
        "acquisition_report_sha256": file_sha256(ACQUISITION),
        "source": acquisition["source"],
        "source_version": acquisition["source_version"],
        "license": "CC BY 4.0 (official included LICENSE.txt)",
        "license_sha256": file_sha256(RAW / "LICENSE.txt"),
        "source_dashcam_archive_md5": acquisition["source_archive_md5"],
        "rdd_inventory_sha256": file_sha256(RDD_INVENTORY),
        "image_count": len(images),
        "image_errors": image_errors,
        "image_dimensions": dict(Counter(f"{row['image_width']}x{row['image_height']}" for row in image_rows)),
        "labels": _label_audit(names),
        "gps": _gps_audit(names),
        "exact_cross_rdd": exact_cross,
        "near_cross_rdd_threshold4": near_cross,
        "near_internal_threshold4": near_internal,
        "e2e_demo_exclusion": {
            "image": E2E_DEMO_IMAGE,
            "sha256": E2E_DEMO_SHA256,
            "E2E_DEMO_EXCLUDED_FROM_HOLDOUT": True,
            "use": "phase7_dev_demo_only_after_human_review",
            "human_validated": False,
            "source_capture_group": SOURCE_CAPTURE_GROUP,
            "near_duplicate_group_detected_threshold4": None,
            "future_holdout_policy": "exclude_entire_source_group_until_routes_sessions_independently_proven",
            "source_group_frames": 1006,
            "locally_acquired_group_frames": len(images),
            "raw_image_preserved": True,
        },
        "human_validated_images": 0,
        "negative_semantics": "Source declares empty labels as background; no attributable human review yet",
        "taxonomy_mapping": {
            "0": "URMIND_ROAD_D00",
            "1": "URMIND_ROAD_D10",
            "2": "URMIND_ROAD_D20",
            "3": "URMIND_ROAD_D40",
            "4": "EXCLUDED_SPEED_BUMP",
        },
        "unresolved": [
            "Qualified human review of boxes, missed objects, class mapping and negative semantics",
            "Near-duplicate candidates require human adjudication; dHash threshold 4 is not exhaustive",
            "Dashcam route/session identity is unpublished; use whole-source grouping",
            "Stratified selection changes class prevalence; do not generalize aggregate prevalence metrics",
        ],
    }
    path = write_json_report("ird_dashcam_candidate_audit.json", report)
    print(json.dumps({
        "report": str(path),
        "images": len(images),
        "image_errors": len(image_errors),
        "invalid_boxes": len(report["labels"]["invalid_lines"]),
        "exact_cross": len(exact_cross),
        "near_cross": len(near_cross),
        "near_internal": len(near_internal),
    }))
    return report


if __name__ == "__main__":
    audit()
