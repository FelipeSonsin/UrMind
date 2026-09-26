"""Acquire and audit Attain v1 severity annotations and paired images.

The Mendeley file API supplies immutable file identities and SHA-256 values.
Only WS v1/v2 contain per-object severity; OS v1 labels damage type alone.
This script does not create UrMind Events or promote a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _core import PROJECT_ROOT, assert_inside_project, file_sha256

DATASET_ID = "nykrzdm74f"
SOURCE_VERSION = "mendeley-nykrzdm74f-v1"
SOURCE_URL = f"https://data.mendeley.com/datasets/{DATASET_ID}/1"
API_URL = f"https://data.mendeley.com/api/datasets/{DATASET_ID}/files"
RAW_ROOT = assert_inside_project(PROJECT_ROOT / "datasets/raw/attain")
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": SOURCE_URL,
}
MAX_DATASET_BYTES = 7_000_000_000
MIN_FREE_BYTES = 10_000_000_000

# Folder IDs and map hashes are pinned to the official published v1 release.
SUBSETS: dict[str, dict[str, Any]] = {
    "ws_v1": {
        "count": 809,
        "prefix": "Attain_SMP_WS_v1_",
        "annotation_folder": "e062757e-7d0e-4f48-a8b8-6451c9455b02",
        "image_folder": "737a0627-89a0-429e-b9c6-3c73dd85647e",
        "annotation_suffix": ".txt",
        "map_folder": "4d628605-0ffc-4736-ad87-36d162837e10",
        "map_name": "Attain_SMP_WS_V1.0_data.yaml",
        "map_sha256": "bebf50d91d8de834dc34c1a5c6b9e6c457a85cd4fd6590f6ace9c0a8925b3c6a",
    },
    "ws_v2": {
        "count": 847,
        "prefix": "Attain_SMP_WS_v2_",
        "annotation_folder": "e28a62b7-0f73-43f7-8df6-c86f41ca7a01",
        "image_folder": "7f0c3a77-ef0e-4677-9fce-65a849ac9f4a",
        "annotation_suffix": ".xml",
        "map_folder": "e7f62003-066e-4757-9f44-a335ad4c73d8",
        "map_name": "Attain_SMP_WS_V2.0_data.yaml",
        "map_sha256": "c7f78f248fe20e33af2d91d226ea63ffa525b63ddec1fccd24046e89bbf18276",
    },
}

# Exact overlap with the current four-class YOLOX contract. A "Linear crack"
# cannot be assigned to D00 or D10 without an independent orientation label.
SUPPORTED_TYPES = {"alligator crack": "URMIND_ROAD_D20", "pothole": "URMIND_ROAD_D40"}


def _request(url: str) -> urllib.request.Request:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "data.mendeley.com":
        raise ValueError("Attain source URL outside the pinned host")
    return urllib.request.Request(url, headers=HEADERS)


def _listing(folder_id: str, expected: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"version": 1, "folder_id": folder_id, "$limit": expected})
    with urllib.request.urlopen(_request(f"{API_URL}?{query}"), timeout=30) as response:
        content_range = response.headers.get("Content-Range")
        rows = json.load(response)
    if content_range != f"items 0-{expected - 1}/{expected}" or len(rows) != expected:
        raise ValueError(f"Attain folder inventory incomplete: {folder_id}: {content_range}")
    if len({row.get("filename") for row in rows}) != expected:
        raise ValueError("Attain folder has duplicate filenames")
    for row in rows:
        detail = row.get("content_details") or {}
        if (
            row.get("folder_id") != folder_id
            or not re.fullmatch(r"[0-9a-f]{64}", str(detail.get("sha256_hash", "")))
            or type(detail.get("size")) is not int
            or detail["size"] < 0
        ):
            raise ValueError("Attain file metadata invalid")
    return rows


def _download_verified(
    row: dict[str, Any], destination: Path, *, return_bytes: bool = True
) -> bytes:
    destination = assert_inside_project(destination)
    detail = row["content_details"]
    expected_hash, expected_size = detail["sha256_hash"], detail["size"]
    if destination.exists():
        if destination.stat().st_size != expected_size or file_sha256(destination) != expected_hash:
            raise ValueError(f"Existing Attain file differs from official hash: {destination}")
        return destination.read_bytes() if return_bytes else b""
    temporary = destination.with_name(destination.name + ".part")
    if temporary.exists():
        raise ValueError(f"Inspect incomplete Attain download before retry: {temporary}")
    url = detail.get("download_url")
    if not isinstance(url, str) or not urllib.parse.urlsplit(url).path.startswith(
        f"/public-files/datasets/{DATASET_ID}/files/"
    ):
        raise ValueError("Attain download URL outside the pinned dataset")
    if shutil.disk_usage(PROJECT_ROOT).free < MIN_FREE_BYTES + expected_size:
        raise ValueError("Attain download refused: free disk below project floor")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(_request(url), timeout=30) as source, temporary.open("xb") as out:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("Attain file exceeded published size")
                digest.update(chunk)
                out.write(chunk)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError(f"Attain checksum mismatch: {row['filename']}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination.read_bytes() if return_bytes else b""


def _class_names(mapping: bytes) -> list[str]:
    text = mapping.decode("utf-8-sig")
    section = re.search(r"\bnames:\s*\[(.*?)\]", text, re.DOTALL)
    count = re.search(r"\bnc:\s*(\d+)", text)
    if section is None or count is None:
        raise ValueError("Attain class map lacks names/nc")
    names = re.findall(r"['\"]([^'\"]+)['\"]", section.group(1))
    if len(names) != int(count.group(1)):
        raise ValueError("Attain class map count mismatch")
    return names


def _label_parts(name: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*(.*?)\s*-\s*(Low|High|Medium)\s*", name, re.IGNORECASE)
    if not match:
        raise ValueError(f"Attain label without explicit severity: {name}")
    return match.group(1).strip().lower(), match.group(2).upper()


def _binary_severity_target(severity: str) -> int:
    """Only the two levels named by the versioned external target are eligible."""
    if severity not in {"LOW", "HIGH"}:
        raise ValueError(f"Attain unsupported binary severity level: {severity}")
    return int(severity == "HIGH")


def _yolo_objects(
    data: bytes, names: list[str]
) -> list[tuple[int, str, str, str, dict[str, float]]]:
    result = []
    for index, line in enumerate(data.decode("utf-8-sig").splitlines()):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5 or (len(parts) != 5 and (len(parts) < 7 or len(parts) % 2 != 1)):
            raise ValueError("Attain YOLO annotation geometry malformed")
        class_id = int(parts[0])
        if class_id < 0 or class_id >= len(names):
            raise ValueError("Attain YOLO class ID outside pinned map")
        values = [float(value) for value in parts[1:]]
        if any(not 0 <= value <= 1 for value in values):
            raise ValueError("Attain YOLO coordinates outside image")
        if len(values) == 4:
            x, y, width, height = values
            if (
                width <= 0
                or height <= 0
                or abs(x - 0.5) * 2 + width > 1.0001
                or abs(y - 0.5) * 2 + height > 1.0001
            ):
                raise ValueError("Attain YOLO box invalid")
            left, top = max(0.0, x - width / 2), max(0.0, y - height / 2)
            right, bottom = min(1.0, x + width / 2), min(1.0, y + height / 2)
        elif max(values[::2]) == min(values[::2]) or max(values[1::2]) == min(values[1::2]):
            raise ValueError("Attain YOLO polygon degenerate")
        else:
            left, right = min(values[::2]), max(values[::2])
            top, bottom = min(values[1::2]), max(values[1::2])
        kind, severity = _label_parts(names[class_id])
        result.append(
            (
                index,
                kind,
                severity,
                names[class_id],
                {"x": left, "y": top, "width": right - left, "height": bottom - top},
            )
        )
    return result


def _parse_yolo(data: bytes, names: list[str]) -> list[tuple[str, str]]:
    return [(kind, severity) for _, kind, severity, _, _ in _yolo_objects(data, names)]


def _xml_objects(
    data: bytes, expected_image: str, *, issues: list[str] | None = None
) -> list[tuple[int, str, str, str, dict[str, float]]]:
    """Return source object indexes and boxes in the source image pixel frame."""
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Attain XML entity declaration forbidden")
    root = ET.fromstring(data)
    if root.tag != "annotation" or root.findtext("filename") != expected_image:
        raise ValueError("Attain XML image linkage mismatch")
    width = int(root.findtext("size/width") or 0)
    height = int(root.findtext("size/height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("Attain XML image size invalid")
    result = []
    for index, obj in enumerate(root.findall("object")):
        box = obj.find("bndbox")
        if box is None:
            if issues is None:
                raise ValueError("Attain XML object without box")
            issues.append(f"object_{index}:box_missing")
            continue
        xmin, xmax, ymin, ymax = (
            int(box.findtext(key) or -1) for key in ("xmin", "xmax", "ymin", "ymax")
        )
        # Published XML sometimes uses width+1/height+1 for right/bottom.
        if not (
            0 <= xmin < width
            and xmin < xmax <= width + 1
            and 0 <= ymin < height
            and ymin < ymax <= height + 1
        ):
            if issues is None:
                raise ValueError("Attain XML box outside image")
            issues.append(f"object_{index}:box_outside_or_degenerate")
            continue
        polygon = obj.find("polygon")
        if polygon is not None:
            points: dict[int, dict[str, float]] = {}
            valid_polygon = False
            for element in polygon:
                match = re.fullmatch(r"([xy])([1-9][0-9]*)", element.tag)
                try:
                    coordinate = (
                        float(element.text) if element.text is not None else math.nan
                    )
                except ValueError:
                    coordinate = math.nan
                if match is None or not math.isfinite(coordinate):
                    break
                point = points.setdefault(int(match.group(2)), {})
                if match.group(1) in point:
                    break
                point[match.group(1)] = coordinate
            else:
                if len(points) >= 3 and all(
                    set(point) == {"x", "y"} for point in points.values()
                ):
                    xs = [point["x"] for point in points.values()]
                    ys = [point["y"] for point in points.values()]
                    box_bounds = (xmin, ymin, min(xmax, width), min(ymax, height))
                    polygon_bounds = (min(xs), min(ys), max(xs), max(ys))
                    max_delta = max(
                        abs(box_value - polygon_value)
                        for box_value, polygon_value in zip(
                            box_bounds, polygon_bounds, strict=True
                        )
                    )
                    valid_polygon = max_delta <= 2
            if not valid_polygon:
                if issues is None:
                    raise ValueError("Attain XML polygon and box disagree")
                issues.append(f"object_{index}:polygon_box_mismatch")
                continue
        label_original = obj.findtext("name") or ""
        kind, severity = _label_parts(label_original)
        result.append(
            (
                index,
                kind,
                severity,
                label_original,
                {
                    "x": xmin / width,
                    "y": ymin / height,
                    "width": (min(xmax, width) - xmin) / width,
                    "height": (min(ymax, height) - ymin) / height,
                },
            )
        )
    return result


def _parse_xml(
    data: bytes, expected_image: str, *, issues: list[str] | None = None
) -> list[tuple[str, str]]:
    return [
        (kind, severity)
        for _, kind, severity, _, _ in _xml_objects(data, expected_image, issues=issues)
    ]


def acquire(*, execute: bool, max_files: int | None = None) -> dict[str, Any]:
    inventories: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for subset, spec in SUBSETS.items():
        annotations = _listing(spec["annotation_folder"], spec["count"])
        images = _listing(spec["image_folder"], spec["count"])
        maps = _listing(spec["map_folder"], 1)
        mapping = maps[0]
        if mapping["filename"] != spec["map_name"] or mapping["content_details"]["sha256_hash"] != spec["map_sha256"]:
            raise ValueError("Attain class map differs from pinned release")
        image_by_stem = {}
        for row in images:
            match = re.fullmatch(re.escape(spec["prefix"]) + r"(\d{6})\.jpg", row["filename"])
            if match is None:
                raise ValueError("Unexpected Attain image name")
            image_by_stem[match.group(1)] = row
        paired = []
        for row in annotations:
            match = re.fullmatch(
                re.escape(spec["prefix"]) + r"(\d{6})" + re.escape(spec["annotation_suffix"]),
                row["filename"],
            )
            if match is None or match.group(1) not in image_by_stem:
                raise ValueError("Attain annotation lacks exact image pair")
            paired.append((row, image_by_stem[match.group(1)]))
        if len(image_by_stem) != spec["count"] or len(paired) != spec["count"]:
            raise ValueError("Attain image/annotation pairing incomplete")
        inventories[subset] = {"mapping": mapping, "paired": paired}
        total_bytes += sum(row["content_details"]["size"] for row in annotations)
        total_bytes += mapping["content_details"]["size"]
    if total_bytes > MAX_DATASET_BYTES or shutil.disk_usage(PROJECT_ROOT).free < MIN_FREE_BYTES + total_bytes:
        raise ValueError("Attain acquisition exceeds project storage budget")
    if not execute:
        return {"status": "INSPECTED", "annotation_files": 1656, "annotation_bytes": total_bytes, "images_downloaded": 0}

    rows = []
    distribution: Counter[str] = Counter()
    eligible: Counter[str] = Counter()
    invalid_objects = 0
    invalid_samples: list[str] = []
    acquired = 0
    for subset, inventory in inventories.items():
        mapping_bytes = _download_verified(
            inventory["mapping"], RAW_ROOT / subset / inventory["mapping"]["filename"]
        )
        names = _class_names(mapping_bytes)
        remaining = None if max_files is None else max_files - acquired
        if remaining is not None and remaining <= 0:
            return {"status": "PARTIAL_DOWNLOAD", "verified_files": acquired, "annotation_files": 1656}
        pairs = inventory["paired"] if remaining is None else inventory["paired"][:remaining]

        def verify_pair(
            pair: tuple[dict[str, Any], dict[str, Any]],
            subset: str = subset,
            names: list[str] = names,
        ) -> tuple[Any, Any, Any, Any]:
            annotation, image = pair
            data = _download_verified(
                annotation, RAW_ROOT / subset / "annotations" / annotation["filename"]
            )
            issues: list[str] = []
            try:
                labels = (
                    _parse_yolo(data, names)
                    if subset == "ws_v1"
                    else _parse_xml(data, image["filename"], issues=issues)
                )
            except (ValueError, ET.ParseError) as exc:
                labels = []
                issues.append(f"file_invalid:{exc}")
            return annotation, image, labels, issues

        # Three small HTTP transfers overlap latency; XGBoost training stays on one CPU thread.
        with ThreadPoolExecutor(max_workers=3) as pool:
            verified = pool.map(verify_pair, pairs)
            for annotation, image, labels, issues in verified:
                for kind, severity in labels:
                    distribution[f"{subset}:{kind}:{severity}"] += 1
                    if kind in SUPPORTED_TYPES:
                        eligible[f"{SUPPORTED_TYPES[kind]}:{severity}"] += 1
                rows.append(
                    {
                        "source_kind": "EXTERNAL_ANNOTATION",
                        "source_dataset": "Attain",
                        "source_version": SOURCE_VERSION,
                        "subset": subset,
                        "image_id": image["filename"],
                        "image_file_id": image["id"],
                        "image_sha256_official": image["content_details"]["sha256_hash"],
                        "image_bytes_verified": False,
                        "annotation_file": annotation["filename"],
                        "annotation_file_id": annotation["id"],
                        "annotation_sha256": annotation["content_details"]["sha256_hash"],
                        "object_count": len(labels),
                        "invalid_objects": issues,
                        "captured_at": None,
                        "label_created_at": None,
                        "group_status": "SCENE_GROUP_UNKNOWN",
                    }
                )
                invalid_objects += len(issues)
                if issues and len(invalid_samples) < 20:
                    invalid_samples.append(f"{annotation['filename']}:{','.join(issues[:3])}")
                acquired += 1
                if acquired % 100 == 0:
                    print(f"VERIFIED_ANNOTATIONS={acquired}/1656", flush=True)
        if max_files is not None and acquired >= max_files:
            return {"status": "PARTIAL_DOWNLOAD", "verified_files": acquired, "annotation_files": 1656}
    manifest = {
        "source": SOURCE_URL,
        "source_version": SOURCE_VERSION,
        "license": "CC BY 4.0",
        "annotation_files": len(rows),
        "images_downloaded": 0,
        "rows": rows,
    }
    destination = RAW_ROOT / "annotation_manifest.json"
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError("Existing Attain manifest differs; not overwritten")
    else:
        destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "ANNOTATIONS_AUDITED_WITH_EXCLUSIONS" if invalid_objects else "ANNOTATIONS_VERIFIED",
        "annotation_files": len(rows),
        "annotation_objects": sum(distribution.values()),
        "severity_counts": dict(sorted(distribution.items())),
        "yolox_compatible_counts": dict(sorted(eligible.items())),
        "yolox_compatible_objects": sum(eligible.values()),
        "invalid_objects": invalid_objects,
        "invalid_samples": invalid_samples,
        "image_bytes_verified": False,
        "scene_groups_verified": False,
        "manifest_sha256": file_sha256(destination),
    }


def acquire_images(*, max_files: int | None = None) -> dict[str, Any]:
    """Fetch exact paired image bytes only after the annotation audit exists."""
    manifest_path = RAW_ROOT / "annotation_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Attain annotations must be audited before image acquisition")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("source_version") != SOURCE_VERSION
        or manifest.get("annotation_files") != 1656
        or len(manifest.get("rows", [])) != 1656
    ):
        raise ValueError("Attain annotation manifest incomplete")
    expected_images = {
        row["image_id"]: (row["subset"], row["image_sha256_official"])
        for row in manifest["rows"]
    }
    if len(expected_images) != 1656:
        raise ValueError("Attain image identities duplicated")
    images = []
    for subset, spec in SUBSETS.items():
        for image in _listing(spec["image_folder"], spec["count"]):
            if expected_images.get(image["filename"]) != (
                subset,
                image["content_details"]["sha256_hash"],
            ):
                raise ValueError("Attain image differs from annotation manifest")
            images.append((subset, image))
    total_bytes = sum(image["content_details"]["size"] for _, image in images)
    if total_bytes > MAX_DATASET_BYTES or shutil.disk_usage(PROJECT_ROOT).free < MIN_FREE_BYTES + total_bytes:
        raise ValueError("Attain images exceed project storage budget")
    chosen = images if max_files is None else images[:max_files]

    def verify_image(pair: tuple[str, dict[str, Any]]) -> None:
        subset, image = pair
        _download_verified(
            image, RAW_ROOT / subset / "images" / image["filename"], return_bytes=False
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        for count, _ in enumerate(pool.map(verify_image, chosen), start=1):
            if count % 100 == 0:
                print(f"VERIFIED_IMAGES={count}/1656", flush=True)
    if len(chosen) != len(images):
        return {"status": "PARTIAL_IMAGE_DOWNLOAD", "verified_images": len(chosen)}
    evidence = {
        "source_version": SOURCE_VERSION,
        "annotation_manifest_sha256": file_sha256(manifest_path),
        "verified_images": len(images),
        "image_bytes": total_bytes,
        "image_hash_method": "SHA-256 of local bytes equals official Mendeley file hash",
    }
    evidence_path = RAW_ROOT / "image_verification.json"
    if evidence_path.exists():
        if json.loads(evidence_path.read_text(encoding="utf-8")) != evidence:
            raise ValueError("Existing Attain image evidence differs; not overwritten")
    else:
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return {"status": "IMAGES_VERIFIED", **evidence}


def export_severity_dataset() -> dict[str, Any]:
    """Build one real per-object DatasetVersion from verified source bytes."""
    manifest_path = RAW_ROOT / "annotation_manifest.json"
    image_evidence_path = RAW_ROOT / "image_verification.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    image_evidence = json.loads(image_evidence_path.read_text(encoding="utf-8"))
    if (
        manifest.get("source_version") != SOURCE_VERSION
        or manifest.get("license") != "CC BY 4.0"
        or len(manifest.get("rows", [])) != 1656
        or image_evidence.get("source_version") != SOURCE_VERSION
        or image_evidence.get("verified_images") != 1656
        or image_evidence.get("annotation_manifest_sha256") != file_sha256(manifest_path)
    ):
        raise ValueError("Attain source manifests incomplete or divergent")

    # These imports do not load YOLOX, XGBoost, or the web application.
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.ml import tabular
    from app.services.features import (
        SEVERITY_DETECTION_FEATURE_ORDER,
        SEVERITY_DETECTION_SCHEMA_VERSION,
        build_severity_detection_features,
        severity_detection_schema_sha256,
    )
    from PIL import Image

    computed_at = datetime.now(UTC).isoformat()
    rows = []
    source_counts: Counter[str] = Counter()
    for entry in manifest["rows"]:
        subset = entry["subset"]
        image_path = RAW_ROOT / subset / "images" / entry["image_id"]
        annotation_path = RAW_ROOT / subset / "annotations" / entry["annotation_file"]
        if (
            file_sha256(image_path) != entry["image_sha256_official"]
            or file_sha256(annotation_path) != entry["annotation_sha256"]
        ):
            raise ValueError("Attain image or annotation differs from official source hash")
        annotation_bytes = annotation_path.read_bytes()
        if subset == "ws_v1":
            spec = SUBSETS[subset]
            map_path = RAW_ROOT / subset / spec["map_name"]
            if file_sha256(map_path) != spec["map_sha256"]:
                raise ValueError("Attain class map differs from official source hash")
            objects = _yolo_objects(annotation_bytes, _class_names(map_path.read_bytes()))
        else:
            issues: list[str] = []
            objects = _xml_objects(annotation_bytes, entry["image_id"], issues=issues)
            if issues != entry["invalid_objects"]:
                raise ValueError("Attain XML exclusions changed since source audit")
        if len(objects) != entry["object_count"]:
            raise ValueError("Attain object count changed since source audit")
        with Image.open(image_path) as image:
            image.load()
            if subset == "ws_v2":
                xml = ET.fromstring(annotation_bytes)
                if (
                    image.width != int(xml.findtext("size/width") or 0)
                    or image.height != int(xml.findtext("size/height") or 0)
                ):
                    raise ValueError("Attain XML and decoded image dimensions differ")
            for index, kind, severity, original, bbox in objects:
                if kind not in SUPPORTED_TYPES:
                    continue
                target = _binary_severity_target(severity)
                features = build_severity_detection_features(
                    SUPPORTED_TYPES[kind], bbox, image
                )
                rows.append(
                    {
                        "row_id": f"{entry['image_id']}#{index}",
                        "source_sample_id": entry["image_id"],
                        "image_id": entry["image_id"],
                        "image_file_id": entry["image_file_id"],
                        "annotation_file_id": entry["annotation_file_id"],
                        "annotation_index": index,
                        "image_sha256": entry["image_sha256_official"],
                        "annotation_sha256": entry["annotation_sha256"],
                        "source_subset": subset,
                        "source_type": kind,
                        "label_original": original,
                        "target": target,
                        "group_id": entry["image_sha256_official"],
                        "scene_group_status": "UNKNOWN",
                        "box_origin": "human_annotation_proxy",
                        "bbox": bbox,
                        "image_width": image.width,
                        "image_height": image.height,
                        "vision_model_version": None,
                        "vision_checkpoint_hash": None,
                        "vision_feature_version": SEVERITY_DETECTION_SCHEMA_VERSION,
                        "yolox_exposure": "YOLOX_EXPOSURE_UNKNOWN",
                        "captured_at": None,
                        "label_created_at": None,
                        "feature_computed_at": computed_at,
                        "missingness": {
                            "capture_time_unknown": True,
                            "label_time_unknown": True,
                            "context_unavailable": True,
                            "detector_confidence_unavailable": True,
                        },
                        "features": features,
                    }
                )
                source_counts[f"{subset}:{kind}:{severity}"] += 1
    document = {
        "source_kind": "EXTERNAL_ANNOTATION",
        "source_dataset": "Attain",
        "source_version": SOURCE_VERSION,
        "source_url": SOURCE_URL,
        "license_reference": "CC BY 4.0; " + SOURCE_URL,
        "target": tabular.EXTERNAL_SEVERITY_TARGET,
        "target_version": tabular.EXTERNAL_SEVERITY_TARGET_VERSION,
        "feature_schema_version": SEVERITY_DETECTION_SCHEMA_VERSION,
        "feature_schema_sha256": severity_detection_schema_sha256(),
        "label_schema_sha256": tabular.external_severity_label_schema_sha256(),
        "feature_order": list(SEVERITY_DETECTION_FEATURE_ORDER),
        "annotation_manifest_sha256": file_sha256(manifest_path),
        "image_verification_sha256": file_sha256(image_evidence_path),
        "rows": rows,
    }
    digest = tabular.external_annotation_content_sha256(document)
    document["dataset"] = {
        "name": f"attain-pavement-visual-severity-{digest[:16]}",
        "content_sha256": digest,
        "status": "EXPERIMENTAL_DRAFT",
    }
    checked = tabular.validate_external_annotation_export(document)
    destination = assert_inside_project(
        PROJECT_ROOT / "datasets/processed/tabular" / f"{document['dataset']['name']}.json"
    )
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n"
    if destination.exists():
        if destination.read_text(encoding="utf-8") != payload:
            raise ValueError(f"existing dataset artifact differs: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    return {
        "status": "EXPERIMENTAL_DATASET_EXPORTED",
        "dataset_path": str(destination),
        "split_path": None,
        "similarity_audit_required": True,
        "dataset_version": document["dataset"]["name"],
        "dataset_sha256": digest,
        "rows": checked["rows"],
        "source_counts": dict(sorted(source_counts.items())),
        "scene_group_status": "UNKNOWN",
        "scientific_validation": False,
    }


def audit_image_similarity(dataset_path: Path) -> dict[str, Any]:
    """Quarantine cross-subset dHash candidates; never call them verified scenes."""
    dataset_path = assert_inside_project(dataset_path)
    manifest_path = RAW_ROOT / "annotation_manifest.json"
    evidence_path = RAW_ROOT / "image_verification.json"
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.ml import tabular
    from PIL import Image
    from PIL import __version__ as pillow_version

    export = json.loads(dataset_path.read_text(encoding="utf-8"))
    tabular.validate_external_annotation_export(export)
    if (
        file_sha256(manifest_path) != export["annotation_manifest_sha256"]
        or file_sha256(evidence_path) != export["image_verification_sha256"]
    ):
        raise ValueError("Attain similarity audit source evidence changed")
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_source = {entry["image_id"]: entry for entry in source["rows"]}
    eligible = {
        row["image_id"]: (row["source_subset"], row["image_sha256"])
        for row in export["rows"]
    }
    images: list[dict[str, Any]] = []
    for image_id in sorted(eligible):
        subset, image_hash = eligible[image_id]
        entry = by_source.get(image_id)
        if (
            entry is None
            or entry.get("subset") != subset
            or entry.get("image_sha256_official") != image_hash
        ):
            raise ValueError("Attain similarity image differs from source manifest")
        path = RAW_ROOT / subset / "images" / image_id
        if file_sha256(path) != image_hash:
            raise ValueError("Attain similarity image differs from official bytes")
        with Image.open(path) as image:
            pixels = list(
                image.convert("L").resize((9, 8), Image.Resampling.BILINEAR).getdata()
            )
        bits = sum(
            (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
            << (row * 8 + column)
            for row in range(8)
            for column in range(8)
        )
        images.append(
            {
                "image_id": image_id,
                "source_subset": subset,
                "image_sha256": image_hash,
                "dhash64": f"{bits:016x}",
            }
        )
    first = [item for item in images if item["source_subset"] == "ws_v1"]
    second = [item for item in images if item["source_subset"] == "ws_v2"]
    pairs = [
        {
            "ws_v1_image_id": left["image_id"],
            "ws_v2_image_id": right["image_id"],
            "distance": (int(left["dhash64"], 16) ^ int(right["dhash64"], 16)).bit_count(),
        }
        for left in first
        for right in second
        if (int(left["dhash64"], 16) ^ int(right["dhash64"], 16)).bit_count()
        <= tabular.EXTERNAL_SIMILARITY_MAX_DISTANCE
    ]
    audit = {
        "dataset_sha256": export["dataset"]["content_sha256"],
        "dataset_version": export["dataset"]["name"],
        "annotation_manifest_sha256": export["annotation_manifest_sha256"],
        "image_verification_sha256": export["image_verification_sha256"],
        "method": tabular.EXTERNAL_SIMILARITY_METHOD,
        "pillow_version": pillow_version,
        "max_distance": tabular.EXTERNAL_SIMILARITY_MAX_DISTANCE,
        "images": images,
        "pairs": pairs,
    }
    audit["content_sha256"] = tabular.external_similarity_audit_content_sha256(audit)
    checked = tabular.validate_external_similarity_audit(audit, export)
    if not pairs:
        raise ValueError("Attain similarity audit unexpectedly found no candidates")
    audit_path = assert_inside_project(
        PROJECT_ROOT
        / "datasets/processed/tabular"
        / f"attain-cross-subset-dhash12-{audit['content_sha256'][:16]}.json"
    )
    split = tabular.generate_external_split_plan(export, similarity_audit=audit)
    split_path = dataset_path.with_name(
        dataset_path.stem + f"-split-dhash12-{audit['content_sha256'][:8]}.json"
    )
    for path, document in ((audit_path, audit), (split_path, split)):
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != payload:
            raise ValueError(f"existing Attain derived artifact differs: {path}")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
    return {
        "status": "NEAR_DUPLICATE_CANDIDATES_QUARANTINED",
        "audit_path": str(audit_path),
        "split_path": str(split_path),
        "candidate_pairs": checked["pair_count"],
        "candidate_images": checked["candidate_image_count"],
        "split_counts": tabular.validate_external_split_plan(
            split, export, similarity_audit=audit
        )["counts"],
        "scene_independence_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="download only verified annotations")
    parser.add_argument("--images", action="store_true", help="download paired images after annotations")
    parser.add_argument("--export-severity", action="store_true", help="write a verified visual severity DatasetVersion")
    parser.add_argument("--audit-similarity", type=Path, help="audit verified severity images and write quarantined split")
    parser.add_argument("--max-files", type=int, help="bounded, resumable acquisition; no final manifest")
    args = parser.parse_args()
    if args.max_files is not None and (args.max_files <= 0 or not args.execute):
        parser.error("--max-files requires --execute and a positive value")
    if args.images and not args.execute:
        parser.error("--images requires --execute")
    if args.export_severity and (args.images or args.execute or args.max_files):
        parser.error("--export-severity cannot be combined with acquisition flags")
    if args.audit_similarity and (args.export_severity or args.images or args.execute or args.max_files):
        parser.error("--audit-similarity cannot be combined with acquisition/export flags")
    result = (
        audit_image_similarity(args.audit_similarity)
        if args.audit_similarity
        else export_severity_dataset()
        if args.export_severity
        else acquire_images(max_files=args.max_files)
        if args.images
        else acquire(execute=args.execute, max_files=args.max_files)
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
