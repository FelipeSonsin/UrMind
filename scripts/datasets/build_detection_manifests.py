"""Materializa manifests do detector somente após autorização humana vinculada."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import yaml
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)
from review_annotations import digest, group_status, stored_package, validate_decisions

NAMES = {"TRAIN": "detection_train_authorized.jsonl", "VALIDATION": "detection_validation_authorized.jsonl", "TEST": "detection_test_authorized.jsonl", "EXTERNAL_TEST": "detection_external_test.jsonl"}
RDD_MAP = {"D00": "URMIND_ROAD_D00", "D10": "URMIND_ROAD_D10", "D20": "URMIND_ROAD_D20", "D40": "URMIND_ROAD_D40"}
SCHEMA_VERSION = 2

# Geração V2 (quality rebuild). Papéis separados por arquivo próprio: nada aqui
# sobrescreve os manifests do V1, que continuam sendo a fonte do ModelVersion V1.
V2_NAMES = {
    "TRAIN": "detection_v2_train_authorized.jsonl",
    "VALIDATION": "detection_v2_validation_authorized.jsonl",
    "TEST": "detection_v2_frozen_test_authorized.jsonl",
    "DOMAIN_SHIFT_PROBE": "detection_v2_domain_shift_probe.jsonl",
}
V2_ROLE_TO_SPLIT_KEY = {
    "TRAIN": "train",
    "VALIDATION": "validation",
    "TEST": "frozen_test",
    "DOMAIN_SHIFT_PROBE": "domain_shift_probe",
}


class ManifestBuildError(RuntimeError):
    pass


V3_SPLIT_MANIFESTS = {
    "TRAIN": "datasets/manifests/detection_v3_train_authorized.jsonl",
    "VALIDATION": "datasets/manifests/detection_v3_validation_authorized.jsonl",
    "HOLDOUT_V3": "datasets/manifests/detection_v3_holdout.jsonl",
    "FROZEN_TEST": "datasets/manifests/detection_test_authorized.jsonl",
}


def _confirmed_identifier(value: object) -> bool:
    return isinstance(value, str) and group_status(value) != "UNCONFIRMED"


def _sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _adjudicated_splits(rows: list[dict]) -> tuple[dict, dict[str, list[dict]], str]:
    """Read owner-pinned adjudication and all four current split manifests."""
    owner_path = PROJECT_ROOT / "datasets/reports/v3_owner_decisions.json"
    artifact_path = PROJECT_ROOT / "datasets/reports/v3_group_adjudication.json"
    try:
        owner = _json(owner_path)
        artifact_sha = file_sha256(require_local(artifact_path))
        artifact = _json(artifact_path)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise ManifestBuildError("owner-pinned group adjudication missing or unreadable") from exc
    if not isinstance(owner, dict) or not isinstance(artifact, dict):
        raise ManifestBuildError("owner-pinned group adjudication invalid")
    permit = owner.get("group_adjudication", {})
    if not isinstance(permit, dict) or any((
        permit.get("authority") != "auto_policy_v1",
        permit.get("authorized_by") != "Felipe",
        permit.get("decision") != "AUTHORIZED",
        permit.get("artifact_sha256") != artifact_sha,
        artifact.get("authority") != "auto_policy_v1",
        artifact.get("schema_version") != 1,
    )):
        raise ManifestBuildError("group adjudication authority or SHA256 mismatch")
    expected_hashes = artifact.get("manifest_sha256")
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != set(V3_SPLIT_MANIFESTS):
        raise ManifestBuildError("four-split leakage check absent or incomplete")
    manifests: dict[str, list[dict]] = {}
    for role, name in V3_SPLIT_MANIFESTS.items():
        path = PROJECT_ROOT / name
        try:
            current_sha = file_sha256(require_local(path))
            records = _jsonl(path)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            raise ManifestBuildError(f"{role} split manifest missing or unreadable") from exc
        # Outside the try: ManifestBuildError is a RuntimeError and would otherwise be
        # reported as "missing", hiding that the file exists but no longer matches.
        if current_sha != expected_hashes[role]:
            raise ManifestBuildError(f"{role} split manifest stale")
        if not records:
            raise ManifestBuildError(f"{role} split manifest empty or incomplete")
        manifests[role] = records
    for role, records in manifests.items():
        for record in records:
            if not isinstance(record, dict) or record.get("role") != role or any(
                not _confirmed_identifier(record.get(key)) for key in ("group", "near_duplicate_group")
            ) or not _sha256_hex(record.get("image_sha256")) or not isinstance(
                record.get("image_path"), str
            ) or not record["image_path"]:
                raise ManifestBuildError(f"{role} split manifest lacks leakage identifiers")
    leakage_keys = ("image_path", "image_sha256", "group", "near_duplicate_group")
    for left_index, left_role in enumerate(V3_SPLIT_MANIFESTS):
        for right_role in list(V3_SPLIT_MANIFESTS)[left_index + 1:]:
            for key in leakage_keys:
                left_values = {record[key] for record in manifests[left_role]}
                right_values = {record[key] for record in manifests[right_role]}
                if left_values & right_values:
                    raise ManifestBuildError(f"{key} crosses {left_role}/{right_role}")
    keyed = {(record["role"], record["image_path"]): record
             for records in manifests.values() for record in records}
    if len(keyed) != sum(map(len, manifests.values())):
        raise ManifestBuildError("duplicate split manifest record")
    for row in rows:
        record = keyed.get((row.get("role"), row.get("image_path")))
        if record is None or any(record.get(key) != row.get(key) for key in (
            "image_sha256", "group", "near_duplicate_group"
        )):
            raise ManifestBuildError("conversion row differs from pinned split manifest")
    all_records = [record for records in manifests.values() for record in records]
    for field, collection, identifier in (
        ("group", artifact.get("groups"), "group_id"),
        ("near_duplicate_group", artifact.get("near_duplicate_clusters"), "cluster_id"),
    ):
        if not isinstance(collection, list):
            raise ManifestBuildError("group adjudication incomplete")
        declared: dict[str, set[tuple[str, str]]] = {}
        for entry in collection:
            if not isinstance(entry, dict) or not _confirmed_identifier(entry.get(identifier)):
                raise ManifestBuildError("invalid group adjudication entry")
            members = entry.get("members")
            if not isinstance(members, list) or not members:
                raise ManifestBuildError("group adjudication members missing")
            if any(not isinstance(member, dict) or not isinstance(
                member.get("image_path"), str
            ) or not _sha256_hex(member.get("image_sha256")) for member in members):
                raise ManifestBuildError("group adjudication members invalid")
            pairs = {(member["image_path"], member["image_sha256"]) for member in members}
            if len(pairs) != len(members) or entry[identifier] in declared:
                raise ManifestBuildError("group adjudication members invalid")
            declared[entry[identifier]] = pairs
        for group in {record[field] for record in all_records}:
            actual = {(record["image_path"], record["image_sha256"])
                      for record in all_records if record[field] == group}
            if declared.get(group) != actual:
                raise ManifestBuildError(f"{field} members or image hashes differ from adjudication")
    return artifact, manifests, artifact_sha


def eligible_rows_to_coco(
    rows: list[dict], class_order: list[str], authorization: dict | None = None
) -> dict:
    """Convert only independently authorized, fully annotated TRAIN/VALIDATION rows.

    Empty labels and `NOT_ANNOTATED` never become background for active classes.
    The caller must supply a new-cycle authorization bound to exact input hashes.
    """
    if not class_order or len(class_order) != len(set(class_order)):
        raise ManifestBuildError("COCO conversion requires unique approved class order")
    row_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    order_hash = hashlib.sha256(json.dumps(class_order).encode()).hexdigest()
    if not authorization or any(
        (
            authorization.get("status") != "AUTHORIZED",
            authorization.get("decision") != "APPROVED_FOR_URMIND_URBAN_VISION_V3",
            authorization.get("rows_sha256") != row_hash,
            authorization.get("class_order_sha256") != order_hash,
            not authorization.get("authorization_id"),
            not authorization.get("authorized_by"),
            not authorization.get("authorized_at"),
        )
    ):
        raise ManifestBuildError("exact new-cycle authorization binding missing")
    _, _, adjudication_sha = _adjudicated_splits(rows)
    class_ids = {code: index + 1 for index, code in enumerate(class_order)}
    images: list[dict] = []
    annotations: list[dict] = []
    seen = set()
    seen_hashes = set()
    group_roles: dict[str, str] = {}
    near_duplicate_roles: dict[str, str] = {}
    for image_id, row in enumerate(rows, 1):
        if row.get("role") not in {"TRAIN", "VALIDATION"}:
            raise ManifestBuildError("protected or missing split role")
        if row.get("training_authorized") is not True or row.get("authorization_id") != authorization["authorization_id"]:
            raise ManifestBuildError("new-cycle authorization missing")
        if row.get("license_status") != "VERIFIED" or row.get("provenance_status") != "VERIFIED":
            raise ManifestBuildError("image-level license/provenance not verified")
        if not row.get("group") or not row.get("image_sha256") or not row.get("annotation_sha256"):
            raise ManifestBuildError("group or source fingerprints missing")
        if row.get("group_status") == "REPORTED_UNVERIFIED" or not _confirmed_identifier(row.get("group")):
            raise ManifestBuildError("scene group requires adjudicated evidence")
        if row.get("duplicate_check_status") == "REPORTED_UNVERIFIED" or not _confirmed_identifier(row.get("near_duplicate_group")):
            raise ManifestBuildError("near-duplicate grouping requires adjudicated evidence")
        states = row.get("annotation_state_by_class", {})
        if any(states.get(code) not in {"PRESENT_ANNOTATED", "ABSENT_REVIEWED"} for code in class_order):
            raise ManifestBuildError("partial annotation cannot be YOLOX background")
        path = row.get("image_path")
        width, height = row.get("image_width"), row.get("image_height")
        if not path or path in seen or not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise ManifestBuildError("duplicate image or invalid dimensions")
        if row["image_sha256"] in seen_hashes:
            raise ManifestBuildError("duplicate image fingerprint")
        prior_role = group_roles.setdefault(row["group"], row["role"])
        if prior_role != row["role"]:
            raise ManifestBuildError("group crosses TRAIN/VALIDATION")
        near_duplicate_role = near_duplicate_roles.setdefault(row["near_duplicate_group"], row["role"])
        if near_duplicate_role != row["role"]:
            raise ManifestBuildError("near-duplicate group crosses TRAIN/VALIDATION")
        seen.add(path)
        seen_hashes.add(row["image_sha256"])
        boxes = row.get("boxes", [])
        counts = Counter(box.get("canonical_class") for box in boxes)
        for code in class_order:
            if states[code] == "ABSENT_REVIEWED" and counts[code]:
                raise ManifestBuildError("reviewed absence contradicts positive box")
            if states[code] == "PRESENT_ANNOTATED" and not counts[code]:
                raise ManifestBuildError("positive coverage has no box")
        images.append({"id": image_id, "file_name": path, "width": width, "height": height,
                       "sha256": row["image_sha256"], "group": row["group"], "role": row["role"],
                       "near_duplicate_group": row["near_duplicate_group"],
                       "group_evidence_sha256": adjudication_sha,
                       "authorization_id": row["authorization_id"]})
        for box in boxes:
            code = box.get("canonical_class")
            if code not in class_ids:
                raise ManifestBuildError("box class outside approved class order")
            x1, y1, x2, y2 = box["bbox"]
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ManifestBuildError("box outside image")
            annotations.append({"id": len(annotations) + 1, "image_id": image_id,
                                "category_id": class_ids[code], "bbox": [x1, y1, x2 - x1, y2 - y1],
                                "area": (x2 - x1) * (y2 - y1), "iscrowd": 0,
                                "source_annotation_sha256": row["annotation_sha256"]})
    return {"images": images, "annotations": annotations,
            "categories": [{"id": index + 1, "name": code} for index, code in enumerate(class_order)]}


def build_v3_draft() -> int:
    """Materialize a quarantined candidate pool; assign no scientific split role."""
    review_path = DATASETS_DIR / "reports/review_summary.json"
    plan_path = DATASETS_DIR / "reports/training_class_plan.json"
    review, plan = _json(review_path), _json(plan_path)
    class_order = plan["future_dataset"]["CLASS_ORDER"]
    if class_order or any(row["TRAINING_AUTHORIZED"] for row in plan["categories"]):
        raise ManifestBuildError("draft producer refuses approved class set; use reviewed authorization path")
    proposals = review["proposals"]
    if len({row["PROPOSAL_ID"] for row in proposals}) != len(proposals):
        raise ManifestBuildError("duplicate proposal ID")
    candidates = []
    for row in proposals:
        chain = row["evidence_chain"]
        candidates.append({
            "proposal_id": row["PROPOSAL_ID"], "source": row["SOURCE"],
            "source_class": row["SOURCE_CLASS"], "proposed_class": row["PROPOSED_URMIND_CLASS"],
            "image_path": chain["original_image_path"], "image_sha256": row["IMAGE_SHA256"],
            "annotation_sha256": chain["original_annotation_sha256"] or chain["original_mask_sha256"],
            "source_release_evidence_linked": row["SOURCE_RELEASE_EVIDENCE_LINKED"],
            "group_candidate": row["GROUP_ID"], "role": "QUARANTINE",
            "annotation_state_by_class": row["annotation_state_by_class"],
            "semantic_review_status": row["review_status"],
            "provenance_status": row["PROVENANCE_STATUS"],
            "license_status": row["LICENSE_STATUS"], "training_authorized": False,
            "exclusion_reasons": ["HUMAN_REVIEW_REQUIRED", "NEW_CYCLE_AUTHORIZATION_REQUIRED", "PARTIAL_ANNOTATIONS"]
            + (["PROVENANCE_UNRESOLVED", "LICENSE_UNRESOLVED"] if row["SOURCE"] == "urban_community" else []),
        })
    path = DATASETS_DIR / "processed/urban_vision_v3/candidate_pool.jsonl"
    write_text_safe(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates))
    report = {
        "schema_version": 1, "dataset_version": "urmind-urban-vision-v3-DRAFT",
        "state": "QUARANTINED_NO_APPROVED_CLASSES", "training_authorized": False,
        "source_review_summary_sha256": file_sha256(review_path),
        "source_class_plan_sha256": file_sha256(plan_path),
        "producer_sha256": file_sha256(Path(__file__)),
        "candidate_pool": path.relative_to(PROJECT_ROOT).as_posix(),
        "candidate_pool_sha256": file_sha256(path),
        "proposals": len(candidates), "release_evidence_linked": sum(row["source_release_evidence_linked"] for row in candidates),
        "approved_images": 0, "class_order": [],
        "train_validation_split_status": "NOT_CREATED_NO_ELIGIBLE_ROWS",
        "frozen_test_status": "SEALED_UNTOUCHED",
        "conversion": "eligible_rows_to_coco: fail-closed, not executed on quarantined proposals",
        "next_action": "import human decisions; adjudicate completeness, rights, groups and exact dataset authorization",
        "producer": "scripts/datasets/build_detection_manifests.py --generation v3-draft",
    }
    write_json_report("urban_vision_v3_draft.json", report)
    print(json.dumps({"proposals": len(candidates), "approved_images": 0, "state": report["state"]}))
    return 0


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
            assert isinstance(source_fingerprint, str)
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
    protected = tuple(
        role for role in ("TRAIN", "VALIDATION", "TEST", "DOMAIN_SHIFT_PROBE") if role in rows
    )
    for index, left in enumerate(protected):
        for right in protected[index + 1 :]:
            if paths_by_role[left] & paths_by_role[right]:
                raise ManifestBuildError(f"leakage por image_path: {left}/{right}")
            if fingerprints_by_role[left] & fingerprints_by_role[right]:
                raise ManifestBuildError(f"leakage por source_fingerprint: {left}/{right}")


def materialize_rdd2022(*, split_path: Path, selection_path: Path, source_path: Path, status_path: Path) -> tuple[dict[str, list[dict]], dict]:
    split, source, status = _json(split_path), _json(source_path), _json(status_path)
    empty: dict[str, list[dict]] = {role: [] for role in NAMES}
    if not authorization_valid(status, split_path=split_path, selection_path=selection_path, source=source):
        return empty, {"authorized": False, "reason": f"authorization status={status.get('status')}"}
    if split.get("source_manifest_sha256") != file_sha256(selection_path):
        raise ManifestBuildError("split aponta para selection fingerprint divergente")
    selection = _jsonl(selection_path)
    indexed = {row["rel_path"]: row for row in selection}
    if len(indexed) != len(selection):
        raise ManifestBuildError("selection contém image_path duplicado")
    result: dict[str, list[dict]] = {role: [] for role in NAMES}
    source_counts: dict[str, dict[str, int]] = {}
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


def materialize_rdd2022_v2() -> tuple[dict[str, list[dict]], dict]:
    """Materializa os manifests V2 a partir do split estratificado autorizado."""
    split_path = DATASETS_DIR / "splits/rdd2022_v2_splits.json"
    selection_path = DATASETS_DIR / "manifests/rdd2022_subset_selection.jsonl"
    status_path = DATASETS_DIR / "reports/rdd2022_v2_split_authorization_status.json"
    if not split_path.is_file() or not status_path.is_file():
        raise ManifestBuildError("DATASET_SPLIT_NOT_AVAILABLE: V2 requer novo split/autorização")
    split, source, status = (
        _json(split_path),
        _json(DATASETS_DIR / "manifests/rdd2022.json"),
        _json(status_path),
    )
    expected_binding = {
        "dataset_id": "rdd2022",
        "dataset_version": source.get("version"),
        "population_id": "crddc2022-official-all-images-train-test",
        "split_manifest_sha256": file_sha256(split_path),
        "selection_manifest_sha256": file_sha256(selection_path),
    }
    if status.get("status") != "AUTHORIZED_FOR_MODEL_V2" or any(
        status.get(key) != value for key, value in expected_binding.items()
    ):
        raise ManifestBuildError(
            f"split V2 não autorizado ou binding stale: status={status.get('status')}"
        )
    if split.get("selection_manifest_sha256") != file_sha256(selection_path):
        raise ManifestBuildError("split V2 aponta para selection fingerprint divergente")

    selection = _jsonl(selection_path)
    indexed = {row["rel_path"]: row for row in selection}
    if len(indexed) != len(selection):
        raise ManifestBuildError("selection contém image_path duplicado")

    result: dict[str, list[dict]] = {role: [] for role in V2_NAMES}
    counts = {}
    for role, key in V2_ROLE_TO_SPLIT_KEY.items():
        listed = split["manifest_files"][key]
        list_path = PROJECT_ROOT / listed["path"]
        if listed["sha256"] != file_sha256(list_path):
            raise ManifestBuildError(f"{role}: fingerprint do arquivo de split diverge")
        paths = [
            line.strip()
            for line in require_local(list_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected = split["splits"][key]
        if len(paths) != expected["images"]:
            raise ManifestBuildError(f"{role}: cardinalidade diverge")
        for image_rel in paths:
            row = indexed.get(image_rel)
            if row is None:
                raise ManifestBuildError(f"{role}: path fora da seleção: {image_rel}")
            if not row.get("sha256") or not row.get("annotation_sha256"):
                raise ManifestBuildError(f"{role}: fingerprint ausente: {image_rel}")
            width, height, boxes = _boxes(
                PROJECT_ROOT / row["annotation_path"], row["annotation_sha256"]
            )
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
        counts[role] = {
            "total_images": len(paths),
            "positive_images": sum(bool(item["boxes"]) for item in result[role]),
            "negative_images": sum(not item["boxes"] for item in result[role]),
            "boxes": box_count,
        }
    validate_image_records(result)
    return result, {"authorized": True, "counts": counts, "split_sha256": expected_binding["split_manifest_sha256"]}


def build_v2() -> int:
    rows, meta = materialize_rdd2022_v2()
    hashes, counts, classes = {}, {}, {}
    for role, name in V2_NAMES.items():
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
    report = {
        "version": 1,
        "dataset_version_name": "rdd2022-model-v2-quality-rebuild",
        **provenance(__file__, source_dataset="rdd2022", source_version="2022-crddc", transform="materialização fail-closed do split V2 autorizado", params={"roles": list(V2_NAMES), "authorization_required": True}),
        "integrity": {"manifests_sha256": hashes, "split_sha256": meta["split_sha256"]},
        "counts": counts,
        "classes": classes,
        "role_semantics": {
            "TRAIN": "TRAIN_V2",
            "VALIDATION": "VALIDATION_V2: única fonte de seleção de checkpoint, augmentation, hiperparâmetro e operating point",
            "TEST": "FROZEN_INTERNAL_TEST_V2: abertura única após congelamento do operating point",
            "DOMAIN_SHIFT_PROBE": "DOMAIN_SHIFT_PROBE_V2 (população inteira do TEST V1: Czech + United_States): report-only; proibido em treino, mining e qualquer seleção",
        },
    }
    write_json_report("detection_manifests_v2.json", report)
    for role in V2_NAMES:
        item = counts[role]
        print(f"{role:<20} images={item['total_images']:<6} neg={item['negative_images']:<6} boxes={item['boxes']}")
    return 0


def build_experimental() -> int:
    """Derive a V1 TRAIN subset without the three human-ambiguous review images.

    The original source manifest and the human decisions stay untouched. This
    output is for the explicitly requested experimental run, not V3 approval.
    """
    package_path = DATASETS_DIR / "processed/annotation_review/review_6225e89cfebf.html"
    decisions_path = DATASETS_DIR / "processed/annotation_review/decisions_e446e5be364fe8f2.json"
    source_path = DATASETS_DIR / "manifests/detection_train_authorized.jsonl"
    artifact_contract = yaml.safe_load(
        require_local(DATASETS_DIR / "metadata/artifact_contract.yaml").read_text(encoding="utf-8")
    )
    smoke = artifact_contract["training_preparation"]
    if package_path != PROJECT_ROOT / smoke["smoke_review_package"]:
        raise ManifestBuildError("unexpected smoke review package path")
    package = stored_package(package_path, smoke["smoke_review_package_sha256"])
    response_path = DATASETS_DIR / "processed/annotation_review/export_8cb075492cbbb9a2.json"
    response = _json(response_path)
    validation = validate_decisions(package, response)
    if validation["reviewed"] != 8 or validation["pending"]:
        raise ManifestBuildError("eight complete human decisions required")
    decisions = _json(decisions_path)
    if (
        decisions.get("package_sha256") != package.get("package_sha256")
        or decisions.get("source_export_sha256") != digest(response)
        or decisions.get("decisions") != sorted(response["decisions"], key=lambda row: row["id"])
        or decisions.get("training_authorization") is not False
        or digest(decisions)[:16] != decisions_path.stem.removeprefix("decisions_")
    ):
        raise ManifestBuildError("imported human decisions differ from validated export")
    items = {item["id"]: item for item in package["items"]}
    if len(items) != len(package["items"]) or len(items) != 8:
        raise ManifestBuildError("review package has missing/duplicate identities")
    current = decisions.get("decisions", [])
    if len(current) != 8 or {d.get("id") for d in current} != set(items):
        raise ManifestBuildError("review decisions incomplete or duplicated")
    allowed = {"approve", "ambiguous"}
    if {d.get("decision") for d in current} - allowed:
        raise ManifestBuildError("review decisions changed; reassess exclusions")
    excluded = {
        items[d["id"]]["original"]["image_relpath"]: d["id"]
        for d in current if d["decision"] == "ambiguous"
    }
    if len(excluded) != 3:
        raise ManifestBuildError("expected three distinct ambiguous images")
    source = _jsonl(source_path)
    source_paths = {row["image_path"] for row in source}
    if not set(excluded) <= source_paths:
        raise ManifestBuildError("ambiguous image absent from historical TRAIN")
    rows = [row for row in source if row["image_path"] not in excluded]
    protected = {
        "VALIDATION": _jsonl(DATASETS_DIR / "manifests/detection_validation_authorized.jsonl"),
        "TEST": _jsonl(DATASETS_DIR / "manifests/detection_test_authorized.jsonl"),
    }
    validate_image_records({"TRAIN": rows, **protected})
    train_groups = {row["group"] for row in rows}
    if any(train_groups & {row["group"] for row in role_rows} for role_rows in protected.values()):
        raise ManifestBuildError("experimental TRAIN shares a country group with protected data")
    target = DATASETS_DIR / "manifests/detection_experimental_20260925_train.jsonl"
    write_text_safe(target, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    report = {
        "schema_version": 1,
        "status": "EXPERIMENTAL_NOT_APPROVED_FOR_SERVING",
        "authorization": "user request in this session, 2026-09-25; training only",
        "source_train_manifest": source_path.relative_to(PROJECT_ROOT).as_posix(),
        "source_train_sha256": file_sha256(source_path),
        "source_release_manifest": "datasets/manifests/rdd2022.json",
        "source_license_evidence": "datasets/metadata/licenses.md",
        "source_license": "CC BY 4.0 (official RDD2022 Figshare release)",
        "human_review_path": decisions_path.relative_to(PROJECT_ROOT).as_posix(),
        "human_review_sha256": file_sha256(decisions_path),
        "review_package_sha256": package["package_sha256"],
        "excluded_ambiguous": excluded,
        "derived_train_manifest": target.relative_to(PROJECT_ROOT).as_posix(),
        "derived_train_sha256": file_sha256(target),
        "train_images": len(rows),
        "train_boxes": sum(len(row["boxes"]) for row in rows),
        "train_class_boxes": dict(Counter(box["original_class"] for row in rows for box in row["boxes"])),
        "partition_check": "no image path, image hash, or country group overlap with V1 VALIDATION or TEST manifests; TEST images not opened",
        "original_annotations_modified": False,
    }
    write_json_report("detection_experimental_20260925.json", report)
    print(json.dumps({"train_images": report["train_images"], "train_class_boxes": report["train_class_boxes"], "excluded": len(excluded)}, ensure_ascii=False))
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", choices=("v1", "v2", "v3-draft", "experimental"), default="v1")
    generation = parser.parse_args().generation
    if generation == "experimental":
        return build_experimental()
    if generation == "v3-draft":
        return build_v3_draft()
    if generation == "v2":
        return build_v2()
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
        # O flag do contrato é marco manual: `validate_readiness` não o exige para `--run`.
        # Quando há dado e o flag está falso, o relatório diz isso em vez de ficar mudo.
        "reason": (
            "BLOCKED_NO_AUTHORIZED_DATA"
            if not total
            else None
            if model_readiness["system_ready_for_training"]
            else "READINESS_MILESTONE_NOT_PROMOTED: yolox_model_v1.json mantém "
            "system_ready_for_training=false; não bloqueia `app.ml.training --run`"
        ),
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
