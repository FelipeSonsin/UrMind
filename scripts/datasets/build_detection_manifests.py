"""Materializa manifests do detector somente após autorização humana vinculada."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)

NAMES = {"TRAIN": "detection_train_authorized.jsonl", "VALIDATION": "detection_validation_authorized.jsonl", "TEST": "detection_test_authorized.jsonl", "EXTERNAL_TEST": "detection_external_test.jsonl"}
RDD_MAP = {"D00": "URMIND_ROAD_D00", "D10": "URMIND_ROAD_D10", "D20": "URMIND_ROAD_D20", "D40": "URMIND_ROAD_D40"}
SCHEMA_VERSION = 2


class ManifestBuildError(RuntimeError):
    pass


def _json(path: Path) -> dict:
    return json.loads(require_local(path).read_text(encoding="utf-8-sig"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in require_local(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def authorization_valid(status: dict, *, split_path: Path, selection_path: Path, source: dict) -> bool:
    expected = {
        "dataset_id": "rdd2022",
        "dataset_version": source.get("version"),
        "population_id": "crddc2022-official-all-images-train-test",
        "split_manifest_sha256": file_sha256(split_path),
        "selection_manifest_sha256": file_sha256(selection_path),
    }
    return status.get("status") == "AUTHORIZED_FOR_MODEL_V1" and all(status.get(key) == value for key, value in expected.items())


def _boxes(annotation: Path, expected_sha256: str) -> tuple[int, int, list[dict]]:
    if file_sha256(annotation) != expected_sha256:
        raise ManifestBuildError(f"annotation stale: {annotation}")
    root = ET.parse(require_local(annotation)).getroot()
    size = root.find("size")
    if size is None:
        raise ManifestBuildError(f"annotation sem size: {annotation}")
    width, height = int(size.findtext("width", "0")), int(size.findtext("height", "0"))
    result = []
    for obj in root.findall("object"):
        original = (obj.findtext("name") or "").strip().upper()
        canonical = RDD_MAP.get(original)
        if canonical is None:
            continue
        node = obj.find("bndbox")
        if node is None:
            raise ManifestBuildError(f"objeto canônico sem bbox: {annotation}")
        bbox = [int(float(node.findtext(key, "0"))) for key in ("xmin", "ymin", "xmax", "ymax")]
        if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
            raise ManifestBuildError(f"bbox inválida: {annotation}: {bbox}")
        result.append({"original_class": original, "canonical_class": canonical, "bbox": bbox})
    return width, height, result


def validate_image_records(rows: dict[str, list[dict]]) -> None:
    """Valida o contrato canônico sem abrir imagens ou hidratar cloud-only."""
    paths_by_role: dict[str, set[str]] = {}
    fingerprints_by_role: dict[str, set[str]] = {}
    for role, records in rows.items():
        paths: set[str] = set()
        fingerprints: set[str] = set()
        for number, row in enumerate(records, 1):
            prefix = f"{role} registro {number}"
            if row.get("schema_version") != SCHEMA_VERSION:
                raise ManifestBuildError(f"{prefix}: schema_version inválida")
            if not row.get("image_path") or row.get("split") != role:
                raise ManifestBuildError(f"{prefix}: image_path/split inválido")
            if row["image_path"] in paths:
                raise ManifestBuildError(f"{prefix}: imagem duplicada")
            paths.add(row["image_path"])
            source_fingerprint = row.get("source_fingerprint")
            fingerprint_values = (source_fingerprint, row.get("annotation_fingerprint"))
            if any(not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in fingerprint_values):
                raise ManifestBuildError(f"{prefix}: provenance/fingerprint ausente")
            fingerprints.add(source_fingerprint)
            boxes = row.get("boxes")
            if not isinstance(boxes, list):
                raise ManifestBuildError(f"{prefix}: boxes não é lista")
            box_identities: set[tuple] = set()
            width, height = row.get("image_width"), row.get("image_height")
            if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ManifestBuildError(f"{prefix}: dimensões inválidas")
            for box_number, box in enumerate(boxes, 1):
                bbox = box.get("bbox") if isinstance(box, dict) else None
                if not isinstance(box, dict) or box.get("canonical_class") not in RDD_MAP.values() or not box.get("original_class"):
                    raise ManifestBuildError(f"{prefix} box {box_number}: classe inválida")
                if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(value, (int, float)) for value in bbox):
                    raise ManifestBuildError(f"{prefix} box {box_number}: bbox inválida")
                x0, y0, x1, y1 = bbox
                if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                    raise ManifestBuildError(f"{prefix} box {box_number}: bbox fora da imagem")
                identity = (tuple(bbox), box["canonical_class"], box["original_class"])
                if identity in box_identities:
                    raise ManifestBuildError(f"{prefix} box {box_number}: box duplicada")
                box_identities.add(identity)
        paths_by_role[role] = paths
        fingerprints_by_role[role] = fingerprints
    protected = ("TRAIN", "VALIDATION", "TEST")
    for index, left in enumerate(protected):
        for right in protected[index + 1 :]:
            if paths_by_role[left] & paths_by_role[right]:
                raise ManifestBuildError(f"leakage por image_path: {left}/{right}")
            if fingerprints_by_role[left] & fingerprints_by_role[right]:
                raise ManifestBuildError(f"leakage por source_fingerprint: {left}/{right}")


def materialize_rdd2022(*, split_path: Path, selection_path: Path, source_path: Path, status_path: Path) -> tuple[dict[str, list[dict]], dict]:
    split, source, status = _json(split_path), _json(source_path), _json(status_path)
    empty = {role: [] for role in NAMES}
    if not authorization_valid(status, split_path=split_path, selection_path=selection_path, source=source):
        return empty, {"authorized": False, "reason": f"authorization status={status.get('status')}"}
    if split.get("source_manifest_sha256") != file_sha256(selection_path):
        raise ManifestBuildError("split aponta para selection fingerprint divergente")
    selection = _jsonl(selection_path)
    indexed = {row["rel_path"]: row for row in selection}
    if len(indexed) != len(selection):
        raise ManifestBuildError("selection contém image_path duplicado")
    result, source_counts = {role: [] for role in NAMES}, {}
    for role, key in (("TRAIN", "train"), ("VALIDATION", "validation"), ("TEST", "test")):
        paths = [line.strip() for line in require_local(split_path.parent / f"rdd2022_subset_{key}.txt").read_text().splitlines() if line.strip()]
        expected = split["splits"][key]
        list_path = split_path.parent / f"rdd2022_subset_{key}.txt"
        if expected.get("manifest_sha256") != file_sha256(list_path):
            raise ManifestBuildError(f"{role}: fingerprint do arquivo de split diverge")
        if len(paths) != expected["images"]:
            raise ManifestBuildError(f"{role}: cardinalidade diverge")
        for image_rel in paths:
            row = indexed.get(image_rel)
            if row is None or row.get("group") not in expected["groups"]:
                raise ManifestBuildError(f"{role}: path/group fora do split: {image_rel}")
            if not row.get("sha256") or not row.get("annotation_sha256"):
                raise ManifestBuildError(f"{role}: fingerprint ausente: {image_rel}")
            width, height, boxes = _boxes(PROJECT_ROOT / row["annotation_path"], row["annotation_sha256"])
            result[role].append({
                "schema_version": SCHEMA_VERSION,
                "dataset_id": "rdd2022", "source_version": source["version"], "image_path": image_rel,
                "image_width": width, "image_height": height, "split": role, "group": row["group"],
                "authorization_status": f"{role}_AUTHORIZED",
                "source_fingerprint": row["sha256"], "annotation_fingerprint": row["annotation_sha256"],
                "human_pending": False, "quarantine": False, "duplicate_rejected": False,
                "blocked_source": False, "boxes": boxes,
            })
        box_count = sum(len(item["boxes"]) for item in result[role])
        if box_count != expected["objects"]:
            raise ManifestBuildError(f"{role}: boxes {box_count} != {expected['objects']}")
        source_counts[role] = {
            "total_images": len(paths),
            "positive_images": sum(bool(item["boxes"]) for item in result[role]),
            "negative_images": sum(not item["boxes"] for item in result[role]),
            "boxes": box_count,
        }
    validate_image_records(result)
    return result, {"authorized": True, "counts": source_counts}


def main() -> int:
    split_path = DATASETS_DIR / "splits/rdd2022_subset_splits.json"
    selection_path = DATASETS_DIR / "manifests/rdd2022_subset_selection.jsonl"
    rows, rdd = materialize_rdd2022(split_path=split_path, selection_path=selection_path, source_path=DATASETS_DIR / "manifests/rdd2022.json", status_path=DATASETS_DIR / "reports/rdd2022_split_authorization_status.json")
    hashes, counts, classes = {}, {}, {}
    for role, name in NAMES.items():
        path = DATASETS_DIR / "manifests" / name
        write_text_safe(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows[role]))
        hashes[role] = file_sha256(path)
        positive = sum(bool(row["boxes"]) for row in rows[role])
        counts[role] = {
            "records": len(rows[role]), "total_images": len(rows[role]),
            "positive_images": positive, "negative_images": len(rows[role]) - positive,
            "boxes": sum(len(row["boxes"]) for row in rows[role]),
        }
        classes[role] = dict(Counter(box["canonical_class"] for row in rows[role] for box in row["boxes"]))
    total = sum(item["boxes"] for item in counts.values())
    model_metadata_path = DATASETS_DIR / "metadata/yolox_model_v1.json"
    model_readiness = _json(model_metadata_path)["readiness"]
    smoke_path = DATASETS_DIR / "reports/detection_loader_smoke.json"
    smoke = None
    if smoke_path.exists():
        candidate = _json(smoke_path)
        if candidate.get("manifest_sha256") == hashes["TRAIN"]:
            smoke = candidate
    report = {
        "version": 3,
        **provenance(__file__, source_dataset="multi_source_detection", source_version="pre-training-v1", transform="materialização fail-closed de registros explicitamente autorizados", params={"roles": list(NAMES), "authorization_required": True}),
        "inputs": sum(item["total_images"] for item in rdd.get("counts", {}).values()),
        "outputs": sum(item["total_images"] for item in counts.values()), "dropped": 0, "drop_reasons": {},
        "integrity": {"manifests_sha256": hashes, "rdd_split_sha256": file_sha256(split_path), "rdd_selection_sha256": file_sha256(selection_path)},
        "counts": counts, "classes": classes,
        "sources": {"rdd2022": rdd, "rtk_br": {"authorized": False, "reason": "instance/mapping/duplicate/group gates pending"}, "univali_br": {"authorized": False, "reason": "human/mapping gates pending"}, "urban_community": {"authorized": False, "reason": "human gates pending; evaluation forbidden"}},
        "data_manifest_schema_ready": rdd.get("authorized", False),
        "negative_images_supported": rdd.get("authorized", False),
        "data_model_interface_ready": bool(smoke and smoke.get("yolox_input_compatible")),
        "training_engine_ready": model_readiness["training_engine_ready"],
        "evaluator_ready": model_readiness["evaluator_ready"],
        "checkpointing_ready": model_readiness["checkpointing_ready"],
        "system_ready_for_training": model_readiness["system_ready_for_training"],
        "reason": None if total else "BLOCKED_NO_AUTHORIZED_DATA",
        "accepted_model_v1_risks": [
            "RDD2022 não publica rota/sessão por imagem",
            "domain shift forte por país no split",
            "D40 minoritária em VALIDATION e TEST",
            "arquivos cloud-only permanecem no split lógico e não são hidratados automaticamente",
        ],
        "loader_smoke": smoke,
        "model_configuration": {
            "path": model_metadata_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": file_sha256(model_metadata_path),
        },
    }
    write_json_report("pre_training_readiness.json", report)
    print(f"unified detection manifests: {sum(len(value) for value in rows.values())} imagens, {total} boxes; authorized={rdd['authorized']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
