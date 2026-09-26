"""Preflight and resumable checkpoint for the proposed UrMind ML cycle.

Reads only preparation reports and TRAIN/VALIDATION metadata. Never opens TEST,
loads training samples, changes model state, or authorizes a training run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "datasets/reports/ml_preparation_state.json"
MIN_SMOKE_RAM_BYTES = 2_000_000_000
RDD_OFFICIAL_ARCHIVE_MD5 = "b62bd51d2ffcfaa76c60f234f0cc2bb3"
RDD_OFFICIAL_ARCHIVE_BYTES = 13_264_172_619


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read(relative: str) -> tuple[dict, str]:
    path = ROOT / relative
    return json.loads(path.read_text(encoding="utf8")), _digest(path)


def validated_review_imports(
    review: dict, folder: Path, *, bound_override: dict | None = None
) -> tuple[int, dict[str, str], dict[str, dict]]:
    """Count only intact imports bound to proposals in the current package."""
    from review_annotations import digest, validate_decisions

    bound = bound_override or {
        "package_sha256": review["package_sha256"],
        "items": [
            {
                "id": row["PROPOSAL_ID"],
                "proposed_class": row["PROPOSED_URMIND_CLASS"],
                "annotation_state_by_class": row["annotation_state_by_class"],
                "lineage_status": "STALE_PARENT"
                if (row.get("lineage_reconciliation") or {}).get("status")
                == "STALE_PARENT_NOT_REAUTHORIZED"
                else "BOUND",
            }
            for row in review["proposals"]
        ],
    }
    rows: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for path in sorted(folder.glob("decisions_*.json")):
        data = json.loads(path.read_text(encoding="utf8"))
        if data.get("package_sha256") != review["package_sha256"]:
            continue
        if path.stem != f"decisions_{digest(data)[:16]}":
            raise ValueError("imported review filename/content hash mismatch")
        if (
            data.get("schema_version") != 1
            or data.get("training_authorization") is not False
        ):
            raise ValueError("imported review schema/authorization mismatch")
        if (
            data.get("provenance_status") != "UNVERIFIED"
            or data.get("license_status") != "UNVERIFIED"
        ):
            raise ValueError("imported review claims rights verification")
        validate_decisions(
            bound,
            {
                "package_sha256": data["package_sha256"],
                "training_authorized": False,
                "decisions": data.get("decisions"),
            },
        )
        for row in data["decisions"]:
            old = rows.setdefault(row["id"], row)
            if old != row:
                raise ValueError("conflicting imported decisions require adjudication")
        hashes[
            path.relative_to(ROOT).as_posix()
            if path.is_relative_to(ROOT)
            else str(path)
        ] = _digest(path)
    return len(rows), hashes, rows


def _manifest_profile(path: Path) -> dict:
    """Read only TRAIN/VALIDATION metadata, one JSON line at a time."""
    dimensions: Counter[str] = Counter()
    images = boxes = 0
    if path.is_file():
        with path.open(encoding="utf8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                dimensions[f"{row['image_width']}x{row['image_height']}"] += 1
                images += 1
                boxes += len(row["boxes"])
    return {
        "index_bytes": path.stat().st_size if path.is_file() else None,
        "images": images,
        "boxes": boxes,
        "top_resolutions": dimensions.most_common(5),
        "decoded_rgb_640_bytes_per_image": 640 * 640 * 3,
    }


def _nvidia_memory() -> dict:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.free,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        total, free, used = (
            int(value.strip()) for value in result.stdout.splitlines()[0].split(",")
        )
        return {"total_mib": total, "free_mib": free, "used_mib": used}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"total_mib": None, "free_mib": None, "used_mib": None}


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def verify_smoke_release(
    smoke_package: dict, figshare: dict, rdd_manifest: dict, released: dict
) -> tuple[bool, bool]:
    archive: dict = next(
        (
            item
            for item in figshare.get("files", [])
            if item.get("name") == "RDD2022_released_through_CRDDC2022.zip"
        ),
        {},
    )
    provenance = (
        figshare.get("id") == 21431547
        and figshare.get("version") == 1
        and figshare.get("doi") == "10.6084/m9.figshare.21431547.v1"
        and archive.get("computed_md5") == RDD_OFFICIAL_ARCHIVE_MD5
        and archive.get("computed_md5") == released.get("archive_official_md5")
        and archive.get("size") == RDD_OFFICIAL_ARCHIVE_BYTES
        and archive.get("size") == released.get("bytes_to_release")
        and released.get("passed") is True
        and released.get("deleted") is True
        and released.get("extracted_files_crc_verified") == 85805
        and rdd_manifest.get("version") == "2022-crddc"
        and bool(smoke_package["items"])
        and all(
            item["original"].get("source_release_manifest")
            == "datasets/manifests/rdd2022.json"
            and item["original"].get("source_fingerprint")
            == item["media_sha256"].get("image_relpath")
            for item in smoke_package["items"]
        )
    )
    license_verified = (
        provenance
        and figshare.get("license", {}).get("name") == "CC BY 4.0"
        and rdd_manifest.get("license") == "CC BY 4.0"
    )
    return provenance, license_verified


def smoke_semantic_candidates(
    smoke_package: dict, decisions: dict[str, dict]
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Only one-box images with complete four-class review may enter a smoke proposal."""
    eligible: dict[str, list[str]] = {}
    excluded: dict[str, str] = {}
    for item in smoke_package["items"]:
        item_id = item["id"]
        class_name = item["proposed_class"]
        scope = item.get("review_scope_classes")
        if not scope or len(scope) != len(set(scope)) or class_name not in scope:
            raise ValueError("smoke class scope absent or inconsistent")
        row = decisions.get(item_id)
        if row is None:
            excluded[item_id] = "UNREVIEWED"
        elif row["decision"] != "approve":
            excluded[item_id] = row["decision"].upper()
        elif row["annotation_state_by_class"].get(
            class_name
        ) != "PRESENT_ANNOTATED" or any(
            row["annotation_state_by_class"].get(other) != "ABSENT_REVIEWED"
            for other in scope
            if other != class_name
        ):
            excluded[item_id] = "PARTIAL_OR_CONFLICTING_CLASS_COVERAGE"
        else:
            eligible.setdefault(class_name, []).append(item_id)
    return eligible, excluded


def build_preflight() -> dict:
    plan, plan_hash = _read("datasets/reports/training_class_plan.json")
    review, review_hash = _read("datasets/reports/review_summary.json")
    draft, draft_hash = _read("datasets/reports/urban_vision_v3_draft.json")
    coverage, coverage_hash = _read("datasets/reports/taxonomy_coverage.json")
    protocol, protocol_hash = _read(
        "datasets/annotations/tabular_labeling_protocol.json"
    )
    sys.path.insert(0, str(ROOT / "backend"))
    import psutil

    ram_before_torch = psutil.virtual_memory()
    process_before_torch = psutil.Process().memory_info().rss
    import torch

    disk = shutil.disk_usage(ROOT)
    available_ram = psutil.virtual_memory().available
    process_after_torch = psutil.Process().memory_info().rss
    cuda = torch.cuda.is_available()
    gpu = torch.cuda.get_device_properties(0) if cuda else None
    reviewed, imported_hashes, _ = validated_review_imports(
        review, ROOT / "datasets/processed/annotation_review"
    )
    import yaml  # type: ignore[import-untyped]
    from review_annotations import stored_package

    contract = yaml.safe_load(
        (ROOT / "datasets/metadata/artifact_contract.yaml").read_text(encoding="utf8")
    )["training_preparation"]
    smoke_package = stored_package(
        ROOT / contract["smoke_review_package"],
        contract["smoke_review_package_sha256"],
    )
    figshare, figshare_hash = _read("datasets/raw/rdd2022/source.figshare.json")
    rdd_manifest, rdd_manifest_hash = _read("datasets/manifests/rdd2022.json")
    released, release_hash = _read("datasets/reports/rdd_archive_release.json")
    source_provenance_verified, source_license_verified = verify_smoke_release(
        smoke_package, figshare, rdd_manifest, released
    )
    smoke_reviewed, smoke_import_hashes, smoke_decisions = validated_review_imports(
        {"package_sha256": smoke_package["package_sha256"]},
        ROOT / "datasets/processed/annotation_review",
        bound_override=smoke_package,
    )
    smoke_review_total = len(smoke_package["items"])
    smoke_classes = {
        item["id"]: item["proposed_class"] for item in smoke_package["items"]
    }
    smoke_review_by_class: dict[str, dict[str, int]] = {}
    for item_id, class_name in smoke_classes.items():
        decisions = smoke_review_by_class.setdefault(
            class_name, {"approved": 0, "corrected": 0, "rejected": 0, "ambiguous": 0}
        )
        if item_id in smoke_decisions:
            decision = smoke_decisions[item_id]["decision"]
            decisions[
                {
                    "approve": "approved",
                    "correct": "corrected",
                    "reject": "rejected",
                    "ambiguous": "ambiguous",
                }[decision]
            ] += 1
    from review_annotations import group_status

    smoke_unconfirmed_groups = sum(
        group_status(row["group"]) == "UNCONFIRMED"
        for row in smoke_decisions.values()
    )
    semantic_candidates, semantic_exclusions = smoke_semantic_candidates(
        smoke_package, smoke_decisions
    )
    semantic_class_coverage = all(
        semantic_candidates.get(class_name)
        for class_name in set(smoke_classes.values())
    )
    eligible_unconfirmed_groups = sum(
        group_status(smoke_decisions[item_id]["group"]) == "UNCONFIRMED"
        for item_ids in semantic_candidates.values()
        for item_id in item_ids
    )
    original_review_counts = Counter(
        (row["SOURCE"], row["SOURCE_CLASS"]) for row in review["proposals"]
    )
    long_rdd_norway = original_review_counts[("rdd2022", "D40")]
    future_review_count = review["HUMAN_REVIEW_TOTAL"] - long_rdd_norway
    class_order = plan.get("future_dataset", {}).get("CLASS_ORDER", [])
    approved = [
        r["CATEGORY"]
        for r in plan["categories"]
        if r.get("TRAINING_AUTHORIZED") is True
    ]
    smoke_blockers = []
    if smoke_reviewed < smoke_review_total:
        smoke_blockers.append("SMOKE_FOUR_CLASS_REVIEW_INCOMPLETE")
    if not semantic_class_coverage:
        smoke_blockers.append("SMOKE_SEMANTIC_CLASS_COVERAGE_INCOMPLETE")
    if eligible_unconfirmed_groups:
        smoke_blockers.append("SMOKE_GROUPS_UNCONFIRMED")
    if smoke_package["training_authorized"] is not False:
        raise ValueError("review package cannot authorize training")
    if not source_provenance_verified:
        smoke_blockers.append("RDD_OFFICIAL_RELEASE_PROVENANCE_UNVERIFIED")
    if not source_license_verified:
        smoke_blockers.append("RDD_RELEASE_LICENSE_UNVERIFIED")
    smoke_blockers.extend(
        (
            "NEW_CYCLE_TRAINING_AUTHORIZATION_ABSENT",
            "SCENE_GROUP_AND_NEAR_DUPLICATE_ISOLATION_UNVERIFIED",
            "V3_MODEL_DATASET_EVALUATOR_CONTRACT_PENDING",
        )
    )
    if not approved:
        smoke_blockers.append("NO_NEW_CYCLE_AUTHORIZED_CLASSES")
    if not draft.get("approved_images"):
        smoke_blockers.append("NO_AUTHORIZED_TRAIN_IMAGES")
    if draft.get("train_validation_split_status") != "AUTHORIZED":
        smoke_blockers.append("NO_AUTHORIZED_V3_SPLIT")
    if available_ram < MIN_SMOKE_RAM_BYTES:
        smoke_blockers.append("RAM_BELOW_CAUTIOUS_FLOOR")
    if not cuda:
        smoke_blockers.append("CUDA_UNAVAILABLE")
    stages = [
        {
            "stage": "four_class_smoke_review",
            "input_sha256": {
                "pinned_html": contract["smoke_review_package_sha256"],
                "review_package": smoke_package["package_sha256"],
                "figshare_snapshot": figshare_hash,
                "rdd_manifest": rdd_manifest_hash,
                "archive_release": release_hash,
                **smoke_import_hashes,
            },
            "result": (
                "PENDING_HUMAN_REVIEW"
                if smoke_reviewed < smoke_review_total
                else "SEMANTIC_CLASS_COVERAGE_INCOMPLETE"
                if not semantic_class_coverage
                else "CANDIDATE_GROUPS_UNCONFIRMED"
                if eligible_unconfirmed_groups
                else "REVIEW_IMPORTED_AUTHORIZATION_PENDING"
            ),
            "output": {
                "smoke_required_reviews": smoke_review_total,
                "reviewed": smoke_reviewed,
                "decisions_by_class": smoke_review_by_class,
                "unconfirmed_groups": smoke_unconfirmed_groups,
                "semantic_candidates_by_class": semantic_candidates,
                "semantic_exclusions": semantic_exclusions,
                "semantic_class_coverage": semantic_class_coverage,
                "group_status_by_item": {
                    item_id: group_status(row["group"])
                    for item_id, row in smoke_decisions.items()
                },
                "eligible_candidates_with_unconfirmed_group": eligible_unconfirmed_groups,
                "original_130_direct_smoke_blockers": 0,
                "original_130_long_run_norway_review_candidates": long_rdd_norway,
                "original_130_optional_or_future": future_review_count,
                "source_release_evidence_linked": review[
                    "SOURCE_RELEASE_EVIDENCE_LINKED"
                ],
                "training_authorized": False,
                "source_provenance_verified": source_provenance_verified,
                "source_license_verified": source_license_verified,
                "license_restriction": "CC BY 4.0 attribution required",
            },
            "next_action": "exclude three ambiguous proposals; resolve groups for five semantically eligible images, then authorize exact V3 smoke scope",
        },
        {
            "stage": "evidence_and_review",
            "input_sha256": {"review_summary": review_hash, **imported_hashes},
            "result": "PENDING_HUMAN_REVIEW"
            if reviewed < review["HUMAN_REVIEW_TOTAL"]
            else "REVIEW_IMPORT_PRESENT",
            "output": {
                "proposals": review["HUMAN_REVIEW_TOTAL"],
                "unique_reviewed": reviewed,
            },
            "next_action": "adjudicate semantic labels, per-image rights, grouping and new-cycle use",
        },
        {
            "stage": "class_mapping_and_conversion",
            "input_sha256": {
                "class_plan": plan_hash,
                "coverage": coverage_hash,
                "draft": draft_hash,
                "converter": _digest(ROOT / "scripts/datasets/build_detection_manifests.py"),
            },
            "result": "DRAFT_NO_AUTHORIZED_CLASSES"
            if not approved
            else "RECHECK_ELIGIBILITY",
            "output": {
                "class_order": class_order,
                "new_cycle_authorized_classes": approved,
                "draft_candidates": draft["proposals"],
                "verified_group_and_near_duplicate_evidence_required": True,
            },
            "next_action": "convert only eligible, fully reviewed images with complete active-class supervision",
        },
        {
            "stage": "splits_and_holdout",
            "input_sha256": {"class_plan": plan_hash},
            "result": "DRAFT_ONLY",
            "output": {"train_validation_final": False, "holdout_opened": False},
            "next_action": "adjudicate groups/duplicates; freeze independent holdout only after eligible corpus exists",
        },
        {
            "stage": "trainer_v3_interface",
            "input_sha256": {
                "trainer": _digest(ROOT / "backend/app/ml/training.py"),
                "loader": _digest(ROOT / "backend/app/ml/detection_dataset.py"),
                "evaluator": _digest(ROOT / "backend/app/ml/evaluator.py"),
            },
            "result": "PARTIAL_V1_LOADER_AND_MODEL_CONTRACT",
            "output": {
                "selected_contract_reaches_model_factory": True,
                "independent_validation_batch_supported": True,
                "authorized_v3_class_order": class_order,
                "v3_loader_ready": False,
            },
            "next_action": "bind versioned V3 class order through model, manifest loader, evaluator, ONNX and Worker after authorization; test parity",
        },
        {
            "stage": "yolox_smoke",
            "input_sha256": {
                "class_plan": plan_hash,
                "trainer": _digest(ROOT / "backend/app/ml/training.py"),
            },
            "result": "BLOCKED" if smoke_blockers else "TECHNICAL_PREFLIGHT_ONLY",
            "output": {
                "iterations": 0,
                "training_executed": False,
                "training_memory_measured": False,
                "frozen_test_access": False,
                "blockers": smoke_blockers,
            },
            "next_action": "resolve authorized TRAIN and grouping, finish V3 consumer, recheck RAM; request later smoke-training authorization",
        },
        {
            "stage": "xgboost",
            "input_sha256": {
                "labeling_protocol": protocol_hash,
                "tabular_export_code": _digest(ROOT / "backend/app/ml/tabular.py"),
            },
            "result": "PIPELINE_DRAFT_LABELS_PENDING",
            "output": {
                "targets": list(protocol["targets"]),
                "trained": False,
                "local_dependencies": {
                    package: _package_version(package)
                    for package in ("xgboost", "scikit-learn", "shap")
                },
            },
            "next_action": "collect independent target-specific labels with pre-review snapshots",
        },
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "producer": "scripts/datasets/preflight_training.py",
        "producer_sha256": _digest(Path(__file__)),
        "branch": subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "taxonomy_version": coverage.get("taxonomy_version"),
        "training_authorized": False,
        "training_executed": False,
        "smoke_training_executed": False,
        "readiness": {
            "SOFTWARE_READY": False,
            "SMOKE_DATA_READY": False,
            "SMOKE_PASSED": False,
            "LONG_TRAINING_READY": False,
            "SMOKE_CLASS_GATE": False,
            "SMOKE_SEMANTIC_CLASS_COVERAGE": semantic_class_coverage,
            "SMOKE_PROVENANCE_GATE": source_provenance_verified,
            "SMOKE_LICENSE_GATE": source_license_verified,
            "SMOKE_PARTITION_GATE": False,
            "HARDWARE_PREFLIGHT_FLOOR_MET": cuda
            and available_ram >= MIN_SMOKE_RAM_BYTES,
            "HARDWARE_GATE": False,
            "HISTORICAL_TEST_STATUS": "PRESERVED_V1_PROPOSAL_NOT_OPENED",
            "NEW_V3_HOLDOUT_STATUS": "ABSENT",
        },
        "hardware": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": cuda,
            "gpu": gpu.name if gpu else None,
            "gpu_vram_bytes": gpu.total_memory if gpu else None,
            "gpu_memory_nvidia_smi": _nvidia_memory() if cuda else None,
            "torch_vram_allocated_bytes": torch.cuda.memory_allocated(0)
            if cuda
            else None,
            "torch_vram_reserved_bytes": torch.cuda.memory_reserved(0)
            if cuda
            else None,
            "ram_total_bytes": ram_before_torch.total,
            "ram_available_before_torch_bytes": ram_before_torch.available,
            "ram_available_bytes": available_ram,
            "preflight_process_rss_before_torch_bytes": process_before_torch,
            "preflight_process_rss_after_torch_bytes": process_after_torch,
            "disk_free_bytes": disk.free,
            "authorized_train_present": bool(approved),
            "smoke_resource_floor_met": cuda and available_ram >= MIN_SMOKE_RAM_BYTES,
            "manifest_profiles": {
                role: _manifest_profile(
                    ROOT
                    / f"datasets/manifests/detection_{role.lower()}_authorized.jsonl"
                )
                for role in ("TRAIN", "VALIDATION")
            },
            "low_memory_profile": {
                "single_gpu": True,
                "distributed": False,
                "train_workers": 0,
                "validation_workers": 0,
                "persistent_workers": False,
                "pin_memory": False,
                "prefetch_factor": None,
                "image_ram_cache": False,
                "proposed_train_batch": 1,
                "proposed_validation_batch": 1,
                "input_size": [640, 640],
                "occupy_vram": False,
                "amp_requires_stability_test": True,
            },
            "note": "2 GB RAM is a conservative technical floor for attempting a loader/optimizer smoke, not a scientific data threshold",
        },
        "stages": stages,
        "resume_point": "After review JSON import and per-image rights/new-cycle authorization, bind V3 loader/evaluator/Worker contract, create approved split, remeasure RAM, then rerun preflight; never skip trainer readiness gate",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = build_preflight()
    if args.write:
        REPORT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
    print(
        json.dumps(
            {
                "hardware": result["hardware"],
                "stages": [
                    {"stage": row["stage"], "result": row["result"]}
                    for row in result["stages"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
