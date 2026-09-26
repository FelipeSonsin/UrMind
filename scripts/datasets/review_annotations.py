"""Offline annotation review over existing sheets. Never writes approvals to training inputs.

python -B scripts/datasets/review_annotations.py --dry-run
python -B scripts/datasets/review_annotations.py --build
python -B scripts/datasets/review_annotations.py --validate decisions.json
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from _core import PROJECT_ROOT, file_sha256, measure_dir, require_local

SHEETS = (
    "datasets/annotations/univali_instance_audit_v1.jsonl",
    "datasets/annotations/urban_community_box_audit_v1.jsonl",
)
OUTPUT = PROJECT_ROOT / "datasets/processed/annotation_review"
STATES = {"PRESENT_ANNOTATED", "ABSENT_REVIEWED", "NOT_ANNOTATED", "AMBIGUOUS"}


def stored_package(path: Path, expected_sha256: str) -> dict:
    """Read the pinned existing package, not raw XMLs or a new sample selection."""
    path = require_local(path)
    if file_sha256(path) != expected_sha256:
        raise ValueError("review HTML hash mismatch")
    document = path.read_text(encoding="utf8")
    payload, _ = json.JSONDecoder().raw_decode(document.split("const P=", 1)[1])
    allowed = set(SHEETS) | {
        "datasets/manifests/detection_train_authorized.jsonl",
        "datasets/manifests/rdd2022_subset_selection.jsonl",
        "datasets/manifests/rdd2022.json",
        "datasets/manifests/urban_community_boxes.jsonl",
        "datasets/manifests/univali_br_boxes.jsonl",
    }
    for name, fingerprint in payload["sheets"].items():
        if name not in allowed:
            raise ValueError("package input outside allowed review scope")
        if file_sha256(require_local(PROJECT_ROOT / name)) != fingerprint:
            raise ValueError("stale package input")
    for item in payload["items"]:
        original = item["original"]
        if original.get("annotation_path") and file_sha256(
            require_local(PROJECT_ROOT / original["annotation_path"])
        ) != original.get("annotation_sha256"):
            raise ValueError("current annotation hash mismatch")
        for key, media_key in (
            ("image_relpath", "image_data"),
            ("mask_relpath", "mask_data"),
        ):
            if key not in item["original"]:
                continue
            fingerprint = item["media_sha256"][key]
            embedded = base64.b64decode(
                item.pop(media_key).split(",", 1)[1], validate=True
            )
            if hashlib.sha256(embedded).hexdigest() != fingerprint:
                raise ValueError("embedded media hash mismatch")
            if file_sha256(local_media(item["original"][key])) != fingerprint:
                raise ValueError("current media hash mismatch")
    expected = payload.pop("package_sha256")
    if digest(payload) != expected:
        raise ValueError("package metadata hash mismatch")
    payload["package_sha256"] = expected
    ids = [item["id"] for item in payload["items"]]
    if len(ids) != len(set(ids)) or any(
        item["human_decision"] is not None for item in payload["items"]
    ):
        raise ValueError("duplicate proposal or unexpected human decision")
    return payload


def summarize_review(
    bound: dict,
    source_records: dict,
    urban_rows: list[dict],
    release_evidence: dict[str, dict] | None = None,
) -> dict:
    """No automated semantic score or human decision is invented."""
    proposals = []
    urban_index = {row["image_relpath"]: row for row in urban_rows}
    for item in bound["items"]:
        original = item["original"]
        source = (
            "rdd2022"
            if item["id"].startswith("rdd_train_")
            else "urban_community"
            if "urban_community" in item["source_sheet"]
            else "univali_br"
        )
        blocked = source == "urban_community"
        recorded = source_records[source]
        source_release = (release_evidence or {}).get(source, {})
        release_linked = bool(
            source_release.get("checksum_verified") is True
            and source_release.get("source_url") == recorded.get("official_source_url")
            and item["media_sha256"].get("image_relpath")
            and (
                original.get("source_manifest_sha256")
                or original.get("image_relpath")
                in source_release.get("image_paths", set())
                and item["media_sha256"].get("mask_relpath")
            )
        )
        release_recorded = (
            not blocked
            and recorded.get("checksum_verified") == "sim"
            and recorded.get("license") == "CC BY 4.0"
        )
        source_class = original.get("source_label") or "POTHOLE"
        lineage = None
        if blocked:
            current = urban_index.get(original["image_relpath"], {})
            matches = any(
                [box[k] for k in ("xmin", "ymin", "xmax", "ymax")]
                == original["bbox_xyxy"]
                for box in current.get("boxes", [])
            )
            lineage = {
                "current_manifest_contains_same_box": matches,
                "old_parent_sha256": original.get("source_manifest_sha256"),
                "status": "STALE_PARENT_NOT_REAUTHORIZED",
                "action": "Keep original sheet; after rights resolution regenerate through prepare_urban_community_human_audit and rebind reviewed scope. Same coordinates do not prove provenance.",
            }
        ambiguities = [
            "annotation completeness unknown",
            "scene/session group not adjudicated",
        ]
        ambiguities += (
            ["small area does not prove wrong label", "pothole versus patch/shadow"]
            if source_class == "D40"
            else [
                "native D43/D44 mapping unverified",
                "worn versus normal road marking",
            ]
            if source_class in {"D43", "D44"}
            else [
                "connected component may be fragment/noise",
                "empty mask is not reviewed negative",
            ]
            if source == "univali_br"
            else [
                "uploader is not necessarily image author",
                "per-image rights unknown",
                "stale parent manifest",
            ]
        )
        proposals.append(
            {
                "PROPOSAL_ID": item["id"],
                "IMAGE_ID": original.get("image_id") or original["image_relpath"],
                "IMAGE_SHA256": item["media_sha256"]["image_relpath"],
                "SOURCE": source,
                "SOURCE_CLASS": source_class,
                "PROPOSED_URMIND_CLASS": item["proposed_class"],
                "ANNOTATION_TYPE": "semantic_mask + derived component bbox"
                if source == "univali_br"
                else "original bbox",
                "LICENSE_STATUS": "LICENSE_UNKNOWN",
                "PROVENANCE_STATUS": "PROVENANCE_UNKNOWN",
                "SOURCE_RELEASE_RECORDED": release_recorded,
                "SOURCE_RELEASE_EVIDENCE_LINKED": release_linked,
                "evidence_chain": {
                    "official_source_url": recorded.get("official_source_url"),
                    "release_version": recorded.get("version"),
                    "release_manifest": source_release.get("manifest_path"),
                    "release_manifest_sha256": source_release.get("manifest_sha256"),
                    "source_scan_manifest": source_release.get("scan_path"),
                    "source_scan_sha256": source_release.get("scan_sha256"),
                    "release_checksum_status": source_release.get("checksum_verified"),
                    "original_image_path": original.get("image_relpath"),
                    "original_image_sha256": item["media_sha256"].get("image_relpath"),
                    "original_annotation_sha256": original.get("annotation_sha256"),
                    "original_mask_sha256": item["media_sha256"].get("mask_relpath"),
                    "source_manifest": original.get("source_manifest"),
                    "source_manifest_sha256": original.get("source_manifest_sha256"),
                    "group": original.get("group"),
                    "restrictions": "new-cycle use, completeness and groups require adjudication; Urban third-party rights unresolved",
                },
                "verification_scope": "Historical official release/checksum declaration preserved. This report has no image-bound rights adjudication; UNKNOWN applies to new-cycle evidence, not revocation of historical V1 authorization."
                if not blocked
                else "uploader declaration insufficient; no per-image rights evidence",
                "source_evidence": source_records[source],
                "GROUP_ID": original.get("group"),
                "REVIEW_REASON": "; ".join(ambiguities),
                "AMBIGUITIES": ambiguities,
                "lineage_reconciliation": lineage,
                "review_status": "PENDING",
                "human_validated": False,
                "training_authorized": False,
                "review_package_id": item["id"],
                "annotation_state_by_class": item["annotation_state_by_class"],
                "provenance_resolution_form": {
                    key: None
                    for key in (
                        "original_image_url",
                        "original_author",
                        "original_release",
                        "license_url",
                        "license_version",
                        "rights_evidence_sha256",
                        "attribution",
                        "identity_match_method",
                        "reviewer",
                        "reviewed_at",
                        "permitted_uses",
                    )
                },
                "provenance_next_action": "Recover original author/source and image license; uploader CC0 and filename match insufficient; keep excluded until adjudication."
                if blocked
                else "Bind existing official release and license evidence to exact package image hashes and intended new-cycle use; reuse prior evidence, do not repeat source audit without a mismatch.",
            }
        )
    by_class = {}
    for code in sorted({item["PROPOSED_URMIND_CLASS"] for item in proposals}):
        rows = [item for item in proposals if item["PROPOSED_URMIND_CLASS"] == code]
        by_class[code] = {
            "TOTAL_PROPOSALS": len(rows),
            "HIGH_CONFIDENCE": None,
            "AMBIGUOUS": None,
            "BAD_LABEL": None,
            "MISSING_ANNOTATIONS": None,
            "PROVENANCE_BLOCKED": sum(
                r["PROVENANCE_STATUS"] == "PROVENANCE_UNKNOWN" for r in rows
            ),
            "UNREVIEWED": len(rows),
            "interpretation": "null means not assessed, not zero; counts refer to proposals, not distinct images",
        }
    return {
        "schema_version": 1,
        "package_sha256": bound["package_sha256"],
        "proposals": proposals,
        "by_category": by_class,
        "HUMAN_REVIEW_TOTAL": len(proposals),
        "HUMAN_REVIEW_READY": len(proposals),
        "PROVENANCE_BLOCKED": sum(
            r["PROVENANCE_STATUS"] == "PROVENANCE_UNKNOWN" for r in proposals
        ),
        "SOURCE_RELEASE_RECORDED": sum(r["SOURCE_RELEASE_RECORDED"] for r in proposals),
        "SOURCE_RELEASE_EVIDENCE_LINKED": sum(
            r["SOURCE_RELEASE_EVIDENCE_LINKED"] for r in proposals
        ),
        "readiness_meaning": "local material ready for quarantined human inspection only; no provenance/rights approval or training permission implied",
        "human_validated": 0,
        "training_authorized": False,
    }


def prepare_plans() -> None:
    """Reachable preparation producer; no authorization, splitting or training."""
    import yaml  # type: ignore[import-untyped]

    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.datasets.taxonomy_candidates import training_class_plan

    contract = yaml.safe_load(
        require_local(
            PROJECT_ROOT / "datasets/metadata/artifact_contract.yaml"
        ).read_text(encoding="utf8")
    )["training_preparation"]
    package_path = PROJECT_ROOT / contract["review_package"]
    bound = stored_package(package_path, contract["review_package_sha256"])
    registry = json.loads(
        require_local(
            PROJECT_ROOT / "datasets/metadata/artifact_registry.json"
        ).read_text(encoding="utf8")
    )
    hashes = {row["path"]: row["sha256"] for row in registry["artifacts"]}
    coverage_path = "datasets/reports/taxonomy_coverage.json"
    if file_sha256(require_local(PROJECT_ROOT / coverage_path)) != hashes.get(
        coverage_path
    ):
        raise ValueError("stale coverage registry entry")
    coverage = json.loads((PROJECT_ROOT / coverage_path).read_text(encoding="utf8"))
    plan, missing = training_class_plan(coverage)
    sources_path = require_local(PROJECT_ROOT / "datasets/metadata/sources.csv")
    sources = {
        row["dataset_name"]: row
        for row in csv.DictReader(sources_path.open(encoding="utf-8-sig"))
    }
    urban_path = require_local(
        PROJECT_ROOT / "datasets/manifests/urban_community_boxes.jsonl"
    )
    urban = [
        json.loads(line) for line in urban_path.read_text(encoding="utf8").splitlines()
    ]
    release_evidence = {}
    for source in ("rdd2022", "univali_br"):
        relative = f"datasets/manifests/{source}.json"
        path = require_local(PROJECT_ROOT / relative)
        manifest = json.loads(path.read_text(encoding="utf8"))
        release_evidence[source] = {
            "manifest_path": relative,
            "manifest_sha256": file_sha256(path),
            "source_url": manifest.get("source"),
            "checksum_verified": manifest.get("split", {})
            .get("inventory", {})
            .get("checksums_verified")
            is True,
        }
    scan_relative = "datasets/manifests/univali_br_mask_scan.jsonl"
    scan_path = require_local(PROJECT_ROOT / scan_relative)
    release_evidence["univali_br"]["scan_path"] = scan_relative
    release_evidence["univali_br"]["scan_sha256"] = file_sha256(scan_path)
    release_evidence["univali_br"]["image_paths"] = {
        json.loads(line)["image_relpath"]
        for line in scan_path.read_text(encoding="utf8").splitlines()
    }
    summary = summarize_review(bound, sources, urban, release_evidence)
    for category in plan["categories"]:
        summary["by_category"].setdefault(
            category["CATEGORY"],
            {
                "TOTAL_PROPOSALS": 0,
                "HIGH_CONFIDENCE": None,
                "AMBIGUOUS": None,
                "BAD_LABEL": None,
                "MISSING_ANNOTATIONS": None,
                "PROVENANCE_BLOCKED": 0,
                "UNREVIEWED": 0,
                "interpretation": "No proposals in this package; not evidence of absence or readiness.",
            },
        )
    inputs = {
        coverage_path: file_sha256(PROJECT_ROOT / coverage_path),
        "datasets/metadata/sources.csv": file_sha256(sources_path),
        contract["review_package"]: contract["review_package_sha256"],
        **bound["sheets"],
        **{
            row["manifest_path"]: row["manifest_sha256"]
            for row in release_evidence.values()
        },
        scan_relative: release_evidence["univali_br"]["scan_sha256"],
    }
    for relative in (
        "backend/app/schemas/issue_taxonomy.py",
        "backend/app/datasets/taxonomy_candidates.py",
        "scripts/datasets/review_annotations.py",
        "datasets/metadata/artifact_contract.yaml",
        "datasets/metadata/storage_budget.yaml",
        "datasets/annotations/tabular_labeling_protocol.json",
    ):
        inputs[relative] = file_sha256(require_local(PROJECT_ROOT / relative))
    distributions = {}
    role_sets = {}
    for role in ("TRAIN", "VALIDATION"):
        relative = f"datasets/manifests/detection_{role.lower()}_authorized.jsonl"
        path = require_local(PROJECT_ROOT / relative)
        if file_sha256(path) != hashes.get(relative):
            raise ValueError("stale historical detection manifest")
        inputs[relative] = file_sha256(path)
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf8").splitlines()
        ]
        if any(row["split"] != role for row in rows):
            raise ValueError("unexpected protected role")
        classes: Counter[str] = Counter()
        small: Counter[str] = Counter()
        country_classes: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            for box in row["boxes"]:
                label = box["original_class"]
                classes[label] += 1
                country_classes[row["group"]][label] += 1
                x1, y1, x2, y2 = box["bbox"]
                scale = 640 / max(row["image_width"], row["image_height"])
                if (x2 - x1) * (y2 - y1) * scale * scale < 32**2:
                    small[label] += 1
        role_sets[role] = {row["source_fingerprint"] for row in rows}
        distributions[role] = {
            "images": len(rows),
            "boxes_by_class": dict(classes),
            "country_group_distribution": {
                key: dict(value) for key, value in country_classes.items()
            },
            "small_boxes_at_640_area_lt_32_squared": dict(small),
            "small_box_policy": "diagnostic only; no cut/removal",
            "empty_labels_not_reviewed_negatives": sum(
                not row["boxes"] for row in rows
            ),
            "unique_fingerprints": len(role_sets[role]),
            "duplicate_rows_by_existing_fingerprint": len(rows) - len(role_sets[role]),
            "completeness": "UNKNOWN for new active class set",
            "group_limitation": "country groups do not establish independent sessions or near-duplicate isolation",
        }
    plan["historical_rdd_revalidation"] = {
        "distributions": distributions,
        "train_validation_fingerprint_overlap": len(
            role_sets["TRAIN"] & role_sets["VALIDATION"]
        ),
        "D40_Norway": {
            "preserve_boxes": 461,
            "small_boxes_recorded": 430,
            "evidence": "datasets/STATUS.md: prior scoped TRAIN XML audit; pilot 24 existing proposals",
            "action": "review morphology/box tightness by scale; preserve all until explicit derived correction",
        },
        "D43_D44": {
            "TRAIN_boxes": {"D43": 310, "D44": 3195},
            "TRAIN_images": {"D43": 286, "D44": 2615},
            "evidence": "datasets/STATUS.md: prior TRAIN XML count; reused, no XML rescan",
            "pilot_proposals": 24,
            "mapping_authorized": False,
            "action": "two reviewers compare original XML label, image and v3 definition; reject normal markings and incomplete scenes; approve mapping separately from boxes",
        },
        "near_duplicates": "Unresolved; reuse historical evidence conservatively, no protected identities used to select samples",
        "new_cycle_authorized": False,
    }
    urban_audit_path = require_local(
        PROJECT_ROOT / "datasets/reports/urban_community_audit.json"
    )
    audit = json.loads(urban_audit_path.read_text(encoding="utf8"))
    inputs["datasets/reports/urban_community_audit.json"] = file_sha256(
        urban_audit_path
    )
    manhole = audit["folders"]["open_manhole"]
    plan["open_manhole"] = {
        "urban_historical_images": manhole["images"],
        "urban_historical_boxes": manhole["boxes"],
        "urban_status": "PROVENANCE_UNKNOWN; LICENSE_UNKNOWN; folder/id map only, not semantic approval",
        "RTK": "storm-drain is semantic class of drain, not evidence of open manhole or blockage",
        "other_local_sources": "No verified damage-state labels demonstrated in current inventory; BDD/scene/keypoint/prediction sources remain context only",
        "external_candidates": next(
            c
            for c in coverage["categories"]
            if c["issue_code"] == "URMIND_OPEN_MANHOLE"
        )["external_sources"],
        "distinctions": {
            "OPEN_MANHOLE": "opening without seated cover",
            "DAMAGED_MANHOLE_COVER": "cover remains, visibly broken/displaced",
            "BLOCKED_DRAIN": "drain inlet visibly occluded",
            "NORMAL_MANHOLE": "intact cover/inlet; only a reviewed negative",
        },
        "action": "resolve per-image original author and rights, then prepare separate native-label review; existing 50 Urban proposals are potholes, not open manholes",
    }
    budget = yaml.safe_load(
        require_local(PROJECT_ROOT / "datasets/metadata/storage_budget.yaml").read_text(
            encoding="utf8"
        )
    )
    used = sum(
        measure_dir(PROJECT_ROOT / folder).logical_size
        for folder in ("datasets", "models", "mlruns")
    )
    missing["budget_snapshot"] = {
        "used_bytes": used,
        "global_cap_bytes": int(budget["limits"]["MAX_TOTAL_ML_STORAGE_GB"] * 1e9),
        "headroom_bytes": int(budget["limits"]["MAX_TOTAL_ML_STORAGE_GB"] * 1e9) - used,
        "source_caps": budget["datasets"],
        "new_sources_require_cap_assignment": True,
        "no_acquisition_performed": True,
    }
    for filename, artifact in (
        ("training_class_plan.json", plan),
        ("review_summary.json", summary),
        ("missing_data_plan.json", missing),
    ):
        artifact["input_sha256"] = inputs
        artifact["producer"] = "scripts/datasets/review_annotations.py --plan"
        (PROJECT_ROOT / "datasets/reports" / filename).write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf8",
            newline="\n",
        )
    print(
        json.dumps(
            {
                "categories": len(plan["categories"]),
                "proposals": len(summary["proposals"]),
                "missing_data": len(missing["categories"]),
                "training_authorized": False,
            }
        )
    )


def rdd_review_proposals() -> tuple[list[dict], dict]:
    """Small stratified review pilot, only from the existing authorized TRAIN membership."""
    relative = "datasets/manifests/detection_train_authorized.jsonl"
    path = require_local(PROJECT_ROOT / relative)
    rows = [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]
    manifest_hash = file_sha256(path)
    groups = defaultdict(list)
    for row in rows:
        if row["split"] != "TRAIN":
            raise ValueError("protected role in TRAIN manifest")
        if "Norway" in row["image_path"]:
            for box in row["boxes"]:
                if box["original_class"] == "D40":
                    x1, y1, x2, y2 = box["bbox"]
                    groups["D40"].append(
                        (
                            (x2 - x1)
                            * (y2 - y1)
                            / (row["image_width"] * row["image_height"]),
                            row,
                            box["bbox"],
                            None,
                        )
                    )
        # Marking labels were not carried into the V1 four-class detection boxes.
        image = PROJECT_ROOT / row["image_path"]
        xml = image.parent.parent / "annotations/xmls" / (image.stem + ".xml")
        if not xml.exists():
            raise ValueError("missing TRAIN annotation")
        root = ET.parse(require_local(xml)).getroot()
        for obj in root.findall("object"):
            label = obj.findtext("name")
            if label in {"D43", "D44"}:
                coordinates = [obj.findtext(f"bndbox/{k}") for k in ("xmin", "ymin", "xmax", "ymax")]
                if any(value is None for value in coordinates):
                    raise ValueError("incomplete source bounding box")
                box = [
                    float(value) for value in coordinates if value is not None
                ]
                groups[label].append(
                    (
                        (box[2] - box[0])
                        * (box[3] - box[1])
                        / (row["image_width"] * row["image_height"]),
                        row,
                        box,
                        xml,
                    )
                )
    proposals = []
    for label, entries in groups.items():
        entries.sort(key=lambda item: (item[0], item[1]["image_path"]))
        n = min(24 if label == "D40" else 12, len(entries))
        for ordinal in range(n):
            _, row, box, xml = entries[
                round(ordinal * (len(entries) - 1) / max(1, n - 1))
            ]
            proposals.append(
                {
                    "id": f"rdd_train_{label}:{ordinal}",
                    "image_relpath": row["image_path"],
                    "image_width": row["image_width"],
                    "image_height": row["image_height"],
                    "bbox_xyxy": box,
                    "source_label": label,
                    "group": row["group"],
                    "source_manifest": relative,
                    "source_manifest_sha256": manifest_hash,
                    "annotation_sha256": file_sha256(xml)
                    if xml
                    else row["annotation_fingerprint"],
                    "source_fingerprint": row["source_fingerprint"],
                    "proposed_class": "URMIND_ROAD_D40"
                    if label == "D40"
                    else "URMIND_FADED_ROAD_MARKING",
                    "review_scope": "pilot only; no population-wide approval",
                    "decision": None,
                }
            )
    return proposals, {relative: file_sha256(path)}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def local_media(relative: str) -> Path:
    path = (PROJECT_ROOT / relative).resolve()
    if not path.is_relative_to((PROJECT_ROOT / "datasets/raw").resolve()):
        raise ValueError("media outside dataset roots")
    return require_local(path)


def smoke_rdd_package() -> dict:
    """Eight TRAIN candidates for a technical smoke review, never authorization."""
    paths = (
        "datasets/manifests/detection_train_authorized.jsonl",
        "datasets/manifests/rdd2022_subset_selection.jsonl",
        "datasets/manifests/rdd2022.json",
    )
    registry = json.loads(
        require_local(
            PROJECT_ROOT / "datasets/metadata/artifact_registry.json"
        ).read_text(encoding="utf8")
    )
    registered = {item["path"]: item["sha256"] for item in registry["artifacts"]}
    bindings = {}
    for relative in paths:
        current = file_sha256(require_local(PROJECT_ROOT / relative))
        if registered.get(relative) != current:
            raise ValueError(f"registered RDD source stale: {relative}")
        bindings[relative] = current
    model = json.loads(
        require_local(PROJECT_ROOT / "datasets/metadata/yolox_model_v1.json").read_text(
            encoding="utf8"
        )
    )
    class_pairs = dict(
        zip(model["class_names"], model["canonical_class_names"], strict=True)
    )
    class_order = list(model["canonical_class_names"])
    selected = {}
    with (PROJECT_ROOT / paths[0]).open(encoding="utf8") as stream:
        for line in stream:
            row = json.loads(line)
            boxes = row.get("boxes", [])
            group = row.get("group")
            if (
                row.get("split") != "TRAIN"
                or row.get("dataset_id") != "rdd2022"
                or row.get("authorization_status") != "TRAIN_AUTHORIZED"
                or group not in {"country:India", "country:Japan"}
                or len(boxes) != 1
                or any(
                    row.get(flag)
                    for flag in (
                        "human_pending",
                        "quarantine",
                        "duplicate_rejected",
                        "blocked_source",
                    )
                )
            ):
                continue
            box = boxes[0]
            label = box.get("original_class")
            if box.get("canonical_class") != class_pairs.get(label):
                continue
            key = (group, label)
            if key in selected:
                continue
            try:
                image = local_media(row["image_path"])
            except (OSError, RuntimeError):
                continue  # cloud-only is never hydrated by this selector
            if file_sha256(image) != row["source_fingerprint"]:
                raise ValueError("candidate image hash mismatch")
            selected[key] = row
            if len(selected) == 8:
                break
    if len(selected) != 8:
        raise ValueError(
            "eight local TRAIN candidates across four classes/two countries unavailable"
        )
    wanted = {row["image_path"] for row in selected.values()}
    lineage = {}
    with (PROJECT_ROOT / paths[1]).open(encoding="utf8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("rel_path") in wanted:
                if row["rel_path"] in lineage:
                    raise ValueError("duplicate selection identity")
                lineage[row["rel_path"]] = row
    if set(lineage) != wanted:
        raise ValueError("selected image missing from bound selection manifest")
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.schemas.issue_taxonomy import ISSUES

    definitions = {issue.issue_code: issue.visual_definition for issue in ISSUES}
    items = []
    estimated = 1_000_000
    for (group, label), row in sorted(selected.items()):
        source = lineage[row["image_path"]]
        if (
            source.get("sha256") != row["source_fingerprint"]
            or source.get("annotation_sha256") != row["annotation_fingerprint"]
            or source.get("group") != group
        ):
            raise ValueError("selected source/annotation/group lineage mismatch")
        annotation = require_local(PROJECT_ROOT / source["annotation_path"])
        if file_sha256(annotation) != row["annotation_fingerprint"]:
            raise ValueError("selected annotation hash mismatch")
        image = local_media(row["image_path"])
        estimated += ((image.stat().st_size + 2) // 3) * 4
        canonical = class_pairs[label]
        original = {
            "image_relpath": row["image_path"],
            "image_width": row["image_width"],
            "image_height": row["image_height"],
            "bbox_xyxy": row["boxes"][0]["bbox"],
            "source_label": label,
            "group": group,
            "source_manifest": paths[0],
            "source_manifest_sha256": bindings[paths[0]],
            "annotation_path": source["annotation_path"],
            "annotation_sha256": row["annotation_fingerprint"],
            "source_fingerprint": row["source_fingerprint"],
            "source_release_manifest": paths[2],
            "rights_note": "RDD official release; new-cycle image use remains unapproved",
            "review_scope": "selected smoke image only; not source-wide quality audit",
        }
        items.append(
            {
                "id": f"smoke_rdd_{group.split(':')[1]}_{label}",
                "source_sheet": paths[0],
                "original": original,
                "media_sha256": {"image_relpath": row["source_fingerprint"]},
                "proposed_class": canonical,
                "review_scope_classes": class_order,
                "definition": definitions[canonical],
                "annotation_state_by_class": {
                    name: "NOT_ANNOTATED" for name in class_order
                },
                "training_authorized": False,
                "coverage_default": "NOT_ANNOTATED",
                "human_decision": None,
                "lineage_status": "HISTORICAL_V1_TRAIN_ONLY",
            }
        )
    payload = {
        "schema_version": 1,
        "sheets": bindings,
        "items": items,
        "scope": "eight RDD TRAIN proposals for technical smoke review; V3 authorization absent",
        "estimated_output_bytes": estimated,
        "training_authorized": False,
    }
    payload["package_sha256"] = digest(payload)
    return payload


def package(include_rdd: bool = False) -> dict:
    """Only the two existing, explicitly scoped human audit sheets are admitted."""
    items = []
    bindings = {}
    estimated = 0
    for relative in SHEETS:
        sheet = require_local(PROJECT_ROOT / relative)
        bindings[relative] = file_sha256(sheet)
        for index, line in enumerate(
            sheet.read_text(encoding="utf-8-sig").splitlines()
        ):
            row = json.loads(line)
            lineage_status = "SHEET_BOUND_PROPOSAL"
            if row.get("source_manifest"):
                parent = (PROJECT_ROOT / row["source_manifest"]).resolve()
                if not parent.is_relative_to(
                    (PROJECT_ROOT / "datasets/manifests").resolve()
                ):
                    raise ValueError("parent outside canonical manifests")
                if row["source_manifest"] not in {
                    "datasets/manifests/univali_br_boxes.jsonl",
                    "datasets/manifests/urban_community_boxes.jsonl",
                }:
                    raise ValueError("parent is not an allowed audit manifest")
                current_hash = file_sha256(require_local(parent))
                bindings[row["source_manifest"]] = current_hash
                if current_hash != row.get("source_manifest_sha256"):
                    lineage_status = "STALE_PARENT"
            media = {
                key: row[key]
                for key in ("image_relpath", "mask_relpath")
                if row.get(key)
            }
            hashes = {}
            for key, filename in media.items():
                path = local_media(filename)
                hashes[key] = file_sha256(path)
                estimated += ((path.stat().st_size + 2) // 3) * 4
            items.append(
                {
                    "id": f"{Path(relative).stem}:{index}",
                    "source_sheet": relative,
                    "original": row,
                    "media_sha256": hashes,
                    "proposed_class": "URMIND_ROAD_D40",
                    "training_authorized": False,
                    "coverage_default": "NOT_ANNOTATED",
                    "human_decision": None,
                    "lineage_status": lineage_status,
                }
            )
    if include_rdd:
        proposals, parents = rdd_review_proposals()
        bindings.update(parents)
        for row in proposals:
            path = local_media(row["image_relpath"])
            actual = file_sha256(path)
            if actual != row["source_fingerprint"]:
                raise ValueError("stale RDD image")
            estimated += ((path.stat().st_size + 2) // 3) * 4
            items.append(
                {
                    "id": row["id"],
                    "source_sheet": row["source_manifest"],
                    "original": row,
                    "media_sha256": {"image_relpath": actual},
                    "proposed_class": row["proposed_class"],
                    "training_authorized": False,
                    "coverage_default": "NOT_ANNOTATED",
                    "human_decision": None,
                    "lineage_status": "TRAIN_MANIFEST_BOUND",
                }
            )
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.schemas.issue_taxonomy import ISSUES

    definitions = {issue.issue_code: issue.visual_definition for issue in ISSUES}
    for item in items:
        item["definition"] = definitions[item["proposed_class"]]
        item["annotation_state_by_class"] = {
            issue.issue_code: "NOT_ANNOTATED" for issue in ISSUES
        }
    result = {
        "schema_version": 1,
        "sheets": bindings,
        "items": items,
        "scope": "existing annotation audit; not app occurrence review",
        "estimated_output_bytes": estimated + 1_000_000,
        "training_authorized": False,
    }
    result["package_sha256"] = digest(result)
    return result


def group_status(raw_group: str) -> str:
    """A reviewer-entered group is never independent-scene evidence by itself."""
    unknown = {
        "", ".", "...", "-", "?", "UNKNOWN", "UNCONFIRMED", "N/A", "NA",
        "NONE", "NULL", "TBD", "NOT CONFIRMED", "TO BE CONFIRMED",
        "NAO CONFIRMADO", "NÃO CONFIRMADO", "NÃO SEI", "SEM GRUPO",
        "DESCONHECIDO",
    }
    return "UNCONFIRMED" if raw_group.strip().upper() in unknown else "REPORTED_UNVERIFIED"


def validate_decisions(bound: dict, response: dict) -> dict:
    if response.get("package_sha256") != bound["package_sha256"]:
        raise ValueError("stale review package")
    if response.get("training_authorized") is not False:
        raise ValueError("review export must explicitly forbid training authorization")
    if not isinstance(response.get("decisions"), list):
        raise TypeError("review decisions must be a list")
    known = {r["id"] for r in bound["items"]}
    items = {r["id"]: r for r in bound["items"]}
    seen = set()
    for row in response.get("decisions", []):
        if row.get("id") not in known or row["id"] in seen:
            raise ValueError("unknown or duplicate review row")
        seen.add(row["id"])
        if (
            items[row["id"]].get("lineage_status") == "STALE_PARENT"
            and row.get("decision") == "approve"
        ):
            raise ValueError(
                "stale parent requires canonical lineage reconciliation before approval"
            )
        if row.get("decision") not in {"approve", "correct", "reject", "ambiguous"}:
            raise ValueError("invalid decision")
        if row.get("coverage") not in STATES:
            raise ValueError("invalid annotation coverage")
        defaults = items[row["id"]].get("annotation_state_by_class")
        if defaults is not None:
            scope = items[row["id"]].get("review_scope_classes")
            if scope:
                submitted = row.get("annotation_state_by_class")
                if (
                    not isinstance(submitted, dict)
                    or len(scope) != len(set(scope))
                    or any(submitted.get(name) not in STATES for name in scope)
                    or submitted.get(items[row["id"]]["proposed_class"])
                    != row["coverage"]
                ):
                    raise ValueError(
                        "smoke review requires explicit active-class coverage"
                    )
                expected = {**defaults, **{name: submitted[name] for name in scope}}
            else:
                expected = {
                    **defaults,
                    items[row["id"]]["proposed_class"]: row["coverage"],
                }
            if row.get("annotation_state_by_class") != expected:
                raise ValueError(
                    "review does not authorize coverage changes for other classes"
                )
        for key in ("reviewed_by", "reviewed_at", "reason"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"missing {key}")
        if not isinstance(row.get("group"), str):
            raise TypeError("missing group field")
        if datetime.fromisoformat(row["reviewed_at"]).tzinfo is None:
            raise ValueError("review timestamp must include timezone")
        if row["decision"] == "correct" and not row.get("correction"):
            raise ValueError("correction requires explicit derived annotation proposal")
        if not isinstance(row.get("history"), list):
            raise TypeError("decision history required")
    return {
        "reviewed": len(seen),
        "pending": len(known - seen),
        "training_authorized": False,
        "status": "AWAITING_ADJUDICATION_AND_CANONICAL_IMPORT",
    }


def import_decisions(
    bound: dict, response: dict, destination_root: Path = OUTPUT
) -> dict:
    """Persist a validated, immutable review export; never mutate source annotations."""
    result = validate_decisions(bound, response)
    payload = {
        "schema_version": 1,
        "package_sha256": bound["package_sha256"],
        "source_export_sha256": digest(response),
        "semantic_review_status": "PARTIAL" if result["pending"] else "REVIEWED",
        "provenance_status": "UNVERIFIED",
        "license_status": "UNVERIFIED",
        "training_authorization": False,
        "decisions": sorted(response["decisions"], key=lambda row: row["id"]),
    }
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / f"decisions_{digest(payload)[:16]}.json"
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if destination.exists():
        if destination.read_text(encoding="utf8") != content:
            raise ValueError("decision import collision")
        status = "ALREADY_IMPORTED"
    else:
        destination.write_text(content, encoding="utf8")
        status = "IMPORTED"
    return {**result, "import_status": status, "path": str(destination)}


HTML = r"""<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'">
<title>UrMind — revisão local de anotações</title>
<style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:24px}svg,img{max-width:100%;max-height:65vh}label{display:block;margin:10px}textarea{width:95%;height:70px}pre{white-space:pre-wrap}button,input,select{font:inherit;padding:8px}</style>
<h1>Revisão humana de anotações</h1>
<p>Não é revisão de ocorrência do app. Aprovar esta linha não autoriza treino nem valida outras imagens.
Buraco: cavidade/perda de material do pavimento. Sombra, poça, remendo e tampa não bastam.
NOT_ANNOTATED não é negativo. Confirme todas as instâncias desta classe na imagem.</p>
<label>Revisor <input id="reviewer"></label><button id="prev">Anterior</button><button id="next">Próxima</button><b id="position"></b>
<h2 id="definition"></h2><p id="lineage"></p><div id="media"></div><details><summary>Original, grupo e procedência</summary><pre id="original"></pre></details>
<label>Decisão <select id="decision"><option value="">Pendente</option><option value="approve">Aprovar esta anotação</option><option value="correct">Propor correção</option><option value="reject">Rejeitar</option><option value="ambiguous">Ambíguo</option></select></label>
<label id="coverage-label">Cobertura da classe <select id="coverage"><option>NOT_ANNOTATED</option><option>PRESENT_ANNOTATED</option><option>ABSENT_REVIEWED</option><option>AMBIGUOUS</option></select></label><div id="class-coverage"></div>
<label>Grupo de cena/sess?o, se conhecido (deixe vazio quando desconhecido) <input id="group"></label>
<label>Justificativa, completude e direitos <textarea id="reason"></textarea></label>
<label>Correção proposta (classe/caixas/objetos ausentes; não altera original)<textarea id="correction"></textarea></label>
<button id="save">Registrar decisão</button><button id="export">Exportar histórico JSON</button>
<label>Retomar export anterior <input id="resume" type="file" accept="application/json"></label><p id="status"></p>
<script>
const P=__PACKAGE__;let index=0, decisions={};const $=id=>document.getElementById(id);
function show(){const item=P.items[index],o=item.original;$('position').textContent=` ${index+1}/${P.items.length}`;
$('definition').textContent=item.proposed_class+': '+item.definition;$('lineage').textContent=item.lineage_status;$('decision').querySelector('[value=approve]').disabled=item.lineage_status==='STALE_PARENT';$('original').textContent=JSON.stringify({...item,image_data:undefined,mask_data:undefined},null,2);$('media').replaceChildren();
const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${o.image_width} ${o.image_height}`);
const img=document.createElementNS(ns,'image');img.setAttribute('href',item.image_data);img.setAttribute('width',o.image_width);img.setAttribute('height',o.image_height);svg.append(img);
if(o.bbox_xyxy){const [x,y,x2,y2]=o.bbox_xyxy,r=document.createElementNS(ns,'rect');Object.entries({x,y,width:x2-x,height:y2-y,fill:'none',stroke:'red','stroke-width':3}).forEach(([k,v])=>r.setAttribute(k,v));svg.append(r)}$('media').append(svg);
if(item.mask_data){const mask=document.createElement('img');mask.src=item.mask_data;mask.alt='Máscara original';$('media').append(mask)}
const d=decisions[item.id]||{};for(const k of ['decision','group','reason','correction'])$(k).value=d[k]||'';$('coverage').value=d.coverage||'NOT_ANNOTATED';
$('coverage-label').hidden=!!item.review_scope_classes;$('class-coverage').replaceChildren();
for(const name of item.review_scope_classes||[]){const label=document.createElement('label'),select=document.createElement('select');select.dataset.className=name;for(const state of ['NOT_ANNOTATED','PRESENT_ANNOTATED','ABSENT_REVIEWED','AMBIGUOUS']){const option=document.createElement('option');option.value=state;option.textContent=state;select.append(option)}select.value=(d.annotation_state_by_class||{})[name]||'NOT_ANNOTATED';label.textContent=name+' ';label.append(select);$('class-coverage').append(label)}}
$('save').onclick=()=>{const item=P.items[index],old=decisions[item.id];if((item.lineage_status==='STALE_PARENT'&&$('decision').value==='approve')||!$('reviewer').value.trim()||!$('decision').value||!$('reason').value.trim()||($('decision').value==='correct'&&!$('correction').value.trim())){alert('Preencha revisor, decisão, justificativa e correção quando aplicável.');return}
const states={...item.annotation_state_by_class};for(const select of $('class-coverage').querySelectorAll('select'))states[select.dataset.className]=select.value;
const coverage=item.review_scope_classes?states[item.proposed_class]:$('coverage').value;states[item.proposed_class]=coverage;
decisions[item.id]={id:item.id,decision:$('decision').value,coverage,annotation_state_by_class:states,group:$('group').value,reason:$('reason').value,correction:$('correction').value,reviewed_by:$('reviewer').value,reviewed_at:new Date().toISOString(),history:old?[...(old.history||[]),{...old,history:undefined}]:[]};$('status').textContent='Registrado nesta sessão. Exporte para preservar.';};
$('next').onclick=()=>{index=Math.min(index+1,P.items.length-1);show()};$('prev').onclick=()=>{index=Math.max(index-1,0);show()};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({package_sha256:P.package_sha256,training_authorized:false,decisions:Object.values(decisions)},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='annotation_review_decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
$('resume').onchange=async e=>{try{const d=JSON.parse(await e.target.files[0].text());if(d.package_sha256!==P.package_sha256)throw Error('Pacote divergente');decisions=Object.fromEntries(d.decisions.map(r=>[r.id,r]));show()}catch(err){alert(String(err))}};show();
</script>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--build", action="store_true")
    group.add_argument("--validate", type=Path)
    group.add_argument("--import-decisions", type=Path)
    group.add_argument(
        "--plan",
        action="store_true",
        help="reuse pinned package; produce preparation reports only",
    )
    parser.add_argument(
        "--include-rdd",
        action="store_true",
        help="TRAIN-only D40/marking pilot; reads TRAIN XMLs",
    )
    parser.add_argument(
        "--smoke-rdd",
        action="store_true",
        help="eight local TRAIN candidates for D00/D10/D20/D40 smoke review only",
    )
    args = parser.parse_args()
    if args.plan:
        prepare_plans()
        return 0
    if args.validate or args.import_decisions:
        import yaml

        contract = yaml.safe_load(
            require_local(
                PROJECT_ROOT / "datasets/metadata/artifact_contract.yaml"
            ).read_text(encoding="utf8")
        )["training_preparation"]
        name = "smoke_review_package" if args.smoke_rdd else "review_package"
        if name not in contract or f"{name}_sha256" not in contract:
            raise ValueError("review package not pinned in artifact contract")
        bound = stored_package(
            PROJECT_ROOT / contract[name], contract[f"{name}_sha256"]
        )
        export = require_local(args.validate or args.import_decisions)
        response = json.loads(export.read_text(encoding="utf8"))
        result = (
            validate_decisions(bound, response)
            if args.validate
            else import_decisions(bound, response)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.smoke_rdd and args.include_rdd:
        raise ValueError("smoke review and broad RDD pilot are separate scopes")
    bound = (
        smoke_rdd_package() if args.smoke_rdd else package(include_rdd=args.include_rdd)
    )
    used = sum(
        measure_dir(PROJECT_ROOT / folder).logical_size
        for folder in ("datasets", "models", "mlruns")
    )
    estimate = bound["estimated_output_bytes"]
    print(
        json.dumps(
            {
                "rows": len(bound["items"]),
                "current_bytes": used,
                "estimated_final_bytes": used + estimate,
                "output_estimate": estimate,
                "training_authorized": False,
            }
        )
    )
    if args.dry_run:
        return 0
    # Read current policy rather than substituting a per-source cap for the global cap.
    import yaml

    budget = yaml.safe_load(
        require_local(
            PROJECT_ROOT / "datasets/metadata/storage_budget.yaml"
        ).read_text()
    )
    if used + estimate > budget["limits"]["MAX_TOTAL_ML_STORAGE_GB"] * 1_000_000_000:
        raise ValueError("storage budget exceeded")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / f"review_{bound['package_sha256'][:12]}.html"
    if destination.exists():
        raise ValueError(
            "existing package preserved; do not overwrite historical review"
        )
    for item in bound["items"]:
        for key, dest in (
            ("image_relpath", "image_data"),
            ("mask_relpath", "mask_data"),
        ):
            if key in item["original"]:
                path = local_media(item["original"][key])
                if file_sha256(path) != item["media_sha256"][key]:
                    raise ValueError("media changed during packaging")
                mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                item[dest] = (
                    f"data:{mime};base64,"
                    + base64.b64encode(path.read_bytes()).decode()
                )
    payload = json.dumps(bound, ensure_ascii=True).replace("<", "\\u003c")
    document = HTML.replace("__PACKAGE__", payload)
    if len(document.encode()) > estimate:
        raise ValueError("output exceeds preflight estimate")
    destination.write_text(document, encoding="utf8")
    print(destination.relative_to(PROJECT_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
