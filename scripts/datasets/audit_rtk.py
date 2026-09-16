"""Audita o release oficial RTK v1 sem promover máscaras a instâncias."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)
from PIL import Image

RAW = DATASETS_DIR / "raw/rtk_br"
SCAN = DATASETS_DIR / "manifests/rtk_br_scan.jsonl"
AUDIT = DATASETS_DIR / "reports/rtk_br_audit.json"
PROVENANCE = DATASETS_DIR / "reports/rtk_br_provenance.json"
ARCHIVE_SHA256 = "78803e8df89eae6130139ee9c48de947edd08015fe56826c30fdacb2599841db"
DOI = "10.17632/hssswvmjwf.1"
CLASSES = {
    0: "background",
    1: "roadAsphalt",
    2: "roadPaved",
    3: "roadUnpaved",
    4: "roadMarking",
    5: "speedBump",
    6: "catsEye",
    7: "stormDrain",
    8: "manholeCover",
    9: "patchs",
    10: "waterPuddle",
    11: "pothole",
    12: "craks",
}
REFERENCES = {
    "rdd2022": DATASETS_DIR / "manifests/rdd2022_inventory.jsonl",
    "univali_br": DATASETS_DIR / "manifests/univali_br_boxes.jsonl",
    "urban_community": DATASETS_DIR / "manifests/urban_community_scan.jsonl",
}


def parse_codes(path: Path) -> dict[int, str]:
    with require_local(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["id", "class"]:
            raise RuntimeError("codes.txt possui cabeçalho divergente")
        rows = list(reader)
    try:
        parsed = {int(row["id"]): row["class"] for row in rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("codes.txt inválido") from exc
    if len(parsed) != len(rows) or parsed != CLASSES:
        raise RuntimeError("codes.txt não corresponde integralmente ao contrato RTK v1")
    return parsed


def validate_metadata_identity(raw: Path, source_definition: Path) -> dict[str, str]:
    source = json.loads(require_local(source_definition).read_text(encoding="utf-8-sig"))
    expected = source.get("metadata_sha256")
    if not isinstance(expected, dict):
        raise TypeError("source definition RTK sem metadata_sha256")
    actual = {
        name: file_sha256(raw / name)
        for name in ("codes.txt", "trainpaths.txt", "valpaths.txt")
    }
    if actual != expected:
        raise RuntimeError("metadados RTK divergentes da source definition versionada")
    return actual


def near_duplicate_pairs(rows: list[dict], threshold: int = 4) -> list[dict]:
    usable = [row for row in rows if row.get("dhash128")]
    pairs = []
    for index, left in enumerate(usable):
        left_hash = int(left["dhash128"], 16)
        for right in usable[index + 1 :]:
            distance = (left_hash ^ int(right["dhash128"], 16)).bit_count()
            if distance <= threshold:
                split_a, split_b = left["published_split"], right["published_split"]
                pairs.append(
                    {
                        "image_a": left["image_path"],
                        "image_b": right["image_path"],
                        "split_a": split_a,
                        "split_b": split_b,
                        "sha256_a": left["image_sha256"],
                        "sha256_b": right["image_sha256"],
                        "dhash_a": left["dhash128"],
                        "dhash_b": right["dhash128"],
                        "hamming_distance": distance,
                        "same_split": split_a == split_b,
                        "cross_split": split_a != split_b,
                        "status": "PENDING_HUMAN_ADJUDICATION",
                    }
                )
    return pairs


def _dhash(path: Path) -> str:
    with Image.open(require_local(path)) as image:
        gray = np.asarray(image.convert("L").resize((9, 9)), dtype=np.int16)
    bits = 0
    for value in (gray[:8, :-1] > gray[:8, 1:]).flat:
        bits = (bits << 1) | int(value)
    for value in (gray[:-1, :8] > gray[1:, :8]).flat:
        bits = (bits << 1) | int(value)
    return f"{bits:032x}"


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4-connectivity; candidatos geométricos, nunca instâncias semânticas."""
    active = mask.copy()
    height, width = active.shape
    result = []
    for y, x in zip(*np.nonzero(active), strict=True):
        if not active[y, x]:
            continue
        queue = deque([(int(y), int(x))])
        active[y, x] = False
        xs, ys = [], []
        while queue:
            cy, cx = queue.popleft()
            xs.append(cx)
            ys.append(cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and active[ny, nx]:
                    active[ny, nx] = False
                    queue.append((ny, nx))
        result.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(xs)))
    return result


def _paths(name: str) -> list[str]:
    path = require_local(RAW / name)
    return [
        line.strip().replace("\\", "/")
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _cross_source(rows: list[dict]) -> list[dict]:
    source_sha = {row["image_sha256"] for row in rows if row["image_sha256"]}
    source_dhash = [int(row["dhash128"], 16) for row in rows if row["dhash128"]]
    results = []
    for dataset_id, path in REFERENCES.items():
        reference = [
            json.loads(line)
            for line in require_local(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        sha_values = set()
        dhash_values = []
        hashable_records = 0
        unreadable = 0
        for row in reference:
            sha_value = row.get("sha256") or row.get("image_sha256")
            value = row.get("dhash128") or row.get("dhash64") or row.get("dhash")
            image_relpath = row.get("image_relpath")
            if (not sha_value or not value) and image_relpath:
                try:
                    image_path = PROJECT_ROOT / image_relpath
                    sha_value = file_sha256(image_path)
                    value = _dhash(image_path)
                except (OSError, RuntimeError):
                    unreadable += 1
                    continue
            if sha_value:
                sha_values.add(sha_value)
                hashable_records += 1
            if value:
                dhash_values.append(int(value, 16))
        nearest = (
            min(
                (left ^ right).bit_count()
                for left in source_dhash
                for right in dhash_values
            )
            if source_dhash and dhash_values
            else None
        )
        exact = sorted(source_sha & sha_values)
        complete = (
            not unreadable
            and hashable_records == len(reference)
            and len(dhash_values) == len(reference)
        )
        results.append(
            {
                "dataset_id": dataset_id,
                "manifest": path.relative_to(PROJECT_ROOT).as_posix(),
                "manifest_sha256": file_sha256(path),
                "reference_records": len(reference),
                "reference_hashable": hashable_records,
                "reference_dhashable": len(dhash_values),
                "reference_unreadable": unreadable,
                "coverage_complete": complete,
                "exact_sha256_matches": len(exact),
                "nearest_dhash_distance": nearest,
                "near_duplicate_threshold": 4,
                "near_candidate_present": nearest is not None and nearest <= 4,
                "passed": complete
                and not exact
                and nearest is not None
                and nearest > 4,
            }
        )
    return results


def main() -> int:
    metadata_sha256 = validate_metadata_identity(
        RAW, DATASETS_DIR / "manifests/rtk_br.json"
    )
    parse_codes(RAW / "codes.txt")
    train, validation = _paths("trainpaths.txt"), _paths("valpaths.txt")
    if len(train) != 561 or len(validation) != 140 or set(train) & set(validation):
        raise RuntimeError("split publicado RTK divergente ou sobreposto")
    split_by_id = {Path(p).stem: "TRAIN_PUBLISHED" for p in train}
    split_by_id.update({Path(p).stem: "VALIDATION_PUBLISHED" for p in validation})
    rows, failures = [], []
    class_pixels: Counter[str] = Counter()
    class_images: Counter[str] = Counter()
    exact: defaultdict[str, list[str]] = defaultdict(list)
    perceptual: defaultdict[str, list[str]] = defaultdict(list)
    candidate_components = 0
    for image_path in sorted((RAW / "image").glob("*.png")):
        sample_id = image_path.stem
        mask_path = RAW / "label" / image_path.name
        errors = []
        try:
            with Image.open(require_local(image_path)) as image:
                image.load()
                width, height, image_mode = image.width, image.height, image.mode
            with Image.open(require_local(mask_path)) as mask_image:
                mask_image.load()
                mask = np.asarray(mask_image)
                mask_mode = mask_image.mode
            if (width, height) != (352, 288) or mask.shape != (288, 352):
                errors.append("dimension_mismatch")
            values, counts = np.unique(mask, return_counts=True)
            unknown = sorted(int(v) for v in values if int(v) not in CLASSES)
            if unknown:
                errors.append(f"unknown_class_values:{unknown}")
            present = [CLASSES[int(v)] for v in values if int(v) and int(v) in CLASSES]
            for value, count in zip(values, counts, strict=True):
                if int(value) in CLASSES:
                    class_pixels[CLASSES[int(value)]] += int(count)
                    if int(value):
                        class_images[CLASSES[int(value)]] += 1
            pothole_components = _components(mask == 11)
            candidate_components += len(pothole_components)
        except (OSError, RuntimeError, ValueError) as exc:
            width = height = None
            image_mode = mask_mode = None
            present, pothole_components = [], []
            errors.append(f"unreadable:{exc}")
        image_sha = file_sha256(image_path) if not errors else None
        mask_sha = file_sha256(mask_path) if not errors else None
        dhash = _dhash(image_path) if not errors else None
        if image_sha:
            exact[image_sha].append(sample_id)
            perceptual[dhash].append(sample_id)
        row = {
            "dataset_id": "rtk_br",
            "source_version": "mendeley-hssswvmjwf-v1",
            "sample_id": sample_id,
            "image_path": image_path.relative_to(PROJECT_ROOT).as_posix(),
            "mask_path": mask_path.relative_to(PROJECT_ROOT).as_posix(),
            "published_split": split_by_id.get(sample_id),
            "width": width,
            "height": height,
            "image_mode": image_mode,
            "mask_mode": mask_mode,
            "classes_present": present,
            "image_sha256": image_sha,
            "mask_sha256": mask_sha,
            "dhash128": dhash,
            "pothole_component_candidates": [
                {"bbox_xyxy": list(c[:4]), "area_px": c[4]} for c in pothole_components
            ],
            "geometric_valid": not errors,
            "semantic_candidate": "pothole" in present,
            "human_validated": False,
            "training_authorized": False,
            "authorization_status": "BLOCKED_SEMANTIC_INSTANCE_AND_GROUP_EVIDENCE",
            "errors": errors,
        }
        rows.append(row)
        failures.extend(f"{sample_id}:{error}" for error in errors)
    if len(rows) != 701 or set(split_by_id) != {row["sample_id"] for row in rows}:
        failures.append(
            "população real não coincide com os 701 IDs dos splits publicados"
        )
    write_text_safe(
        SCAN, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    scan_sha = file_sha256(SCAN)
    exact_groups = [ids for ids in exact.values() if len(ids) > 1]
    dhash_groups = [ids for ids in perceptual.values() if len(ids) > 1]
    row_by_id = {row["sample_id"]: row for row in rows}
    dhash_cross_split = [
        ids
        for ids in dhash_groups
        if len({row_by_id[item]["published_split"] for item in ids}) > 1
    ]
    near_pairs = near_duplicate_pairs(rows, threshold=4)
    cross_source = _cross_source(rows)
    audit = {
        "version": 1,
        **provenance(
            __file__,
            source_dataset="rtk_br",
            source_version="mendeley-hssswvmjwf-v1",
            transform="scan estrutural de imagens e class-masks; componentes somente candidatos",
            params={"connectivity": 4, "pothole_value": 11, "dhash_bits": 128},
        ),
        "inputs": 701,
        "outputs": len(rows),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {"scan_sha256": scan_sha, "archive_sha256": ARCHIVE_SHA256},
        "passed_structural": not failures,
        "failures": failures,
        "counts": {
            "images": len(rows),
            "train_published": len(train),
            "validation_published": len(validation),
            "candidate_pothole_components": candidate_components,
            "class_images": dict(class_images),
            "class_pixels": dict(class_pixels),
        },
        "duplicates": {
            "exact_groups": exact_groups,
            "identical_dhash_groups": dhash_groups,
            "identical_dhash_cross_split_groups": dhash_cross_split,
            "near_duplicate_pairs": near_pairs,
            "cross_split_near_duplicate_pairs": [
                pair for pair in near_pairs if pair["cross_split"]
            ],
            "near_duplicate_threshold": 4,
            "status": "CANDIDATES_REQUIRE_GROUP_OR_HUMAN_ADJUDICATION",
        },
        "cross_source": cross_source,
        "annotation_semantics": {
            "type": "semantic_class_mask",
            "instance_ids": False,
            "connected_components_are_instances": False,
        },
        "cross_source_check_passed": all(item["passed"] for item in cross_source),
        "training_authorized": False,
        "evaluation_authorized": False,
    }
    write_json_report("rtk_br_audit.json", audit)
    write_json_report(
        "rtk_br_provenance.json",
        {
            "dataset_id": "rtk_br",
            "source_version": "mendeley-hssswvmjwf-v1",
            "doi": DOI,
            "official_url": "https://data.mendeley.com/datasets/hssswvmjwf/1",
            "official_file": "RTK.zip",
            "official_file_bytes": 101609702,
            "official_file_sha256": ARCHIVE_SHA256,
            "license": "CC BY 4.0",
            "institution": "Universidade Federal de Santa Catarina",
            "location": "Santa Catarina, Brasil",
            "raw_path": "datasets/raw/rtk_br",
            "raw_preserved_extracted": True,
            "archive_retained": False,
            "archive_retention_reason": "evitar cópia permanente duplicada; identidade preservada por SHA-256",
            "scan_manifest": "datasets/manifests/rtk_br_scan.jsonl",
            "scan_sha256": scan_sha,
            "metadata_sha256": metadata_sha256,
        },
    )
    print(f"RTK: {len(rows)} imagens; scan={scan_sha}; structural={not failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
