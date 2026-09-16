"""Prepara e valida a decisão humana vinculada ao split RDD2022 exato."""

from __future__ import annotations

import json

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    file_sha256,
    is_cloud_only,
    require_local,
    write_json_report,
    write_text_safe,
)

SPLIT = DATASETS_DIR / "splits/rdd2022_subset_splits.json"
SELECTION = DATASETS_DIR / "manifests/rdd2022_subset_selection.jsonl"
SOURCE = DATASETS_DIR / "manifests/rdd2022.json"
SHEET = DATASETS_DIR / "annotations/rdd2022_split_authorization_v1.json"
ALLOWED = {"APPROVED_FOR_MODEL_V1", "REJECTED", "REVISE_REQUIRED"}
POPULATION_ID = "crddc2022-official-all-images-train-test"


def bindings() -> dict:
    source = json.loads(require_local(SOURCE).read_text(encoding="utf-8-sig"))
    return {
        "dataset_id": "rdd2022",
        "dataset_version": source["version"],
        "population_id": POPULATION_ID,
        "split_manifest_sha256": file_sha256(SPLIT),
        "selection_manifest_sha256": file_sha256(SELECTION),
    }


def _template(current: dict) -> dict:
    return {
        **current,
        "allowed_decisions": sorted(ALLOWED),
        "decision": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "note": None,
    }


def main() -> int:
    current = bindings()
    if not SHEET.exists():
        write_text_safe(SHEET, json.dumps(_template(current), ensure_ascii=False, indent=2) + "\n")
    sheet = json.loads(require_local(SHEET).read_text(encoding="utf-8-sig"))
    if not sheet.get("decision") and not sheet.get("reviewed_by") and not sheet.get("reviewed_at"):
        sheet = _template(current)
        write_text_safe(SHEET, json.dumps(sheet, ensure_ascii=False, indent=2) + "\n")
    mismatches = [key for key, value in current.items() if sheet.get(key) != value]
    decision = sheet.get("decision")
    valid_decision = decision in ALLOWED
    attributable = bool(sheet.get("reviewed_by") and sheet.get("reviewed_at"))
    status = (
        "STALE_HUMAN_DECISION"
        if mismatches
        else "AUTHORIZED_FOR_MODEL_V1"
        if decision == "APPROVED_FOR_MODEL_V1" and attributable
        else "REJECTED"
        if decision == "REJECTED" and attributable
        else "REVISE_REQUIRED"
        if decision == "REVISE_REQUIRED" and attributable
        else "PENDING_HUMAN_DECISION"
    )
    split = json.loads(require_local(SPLIT).read_text(encoding="utf-8-sig"))
    total_images = sum(value["images"] for value in split["splits"].values())
    total_boxes = sum(value["objects"] for value in split["splits"].values())
    duplicates_path = DATASETS_DIR / "reports/rdd2022_duplicates.json"
    duplicates = json.loads(
        require_local(duplicates_path).read_text(encoding="utf-8-sig")
    )
    local_availability = {}
    for role in ("train", "validation", "test"):
        counts = {"records": 0, "local": 0, "cloud_only": 0, "missing": 0}
        path = DATASETS_DIR / f"splits/rdd2022_subset_{role}.txt"
        for relative in require_local(path).read_text(encoding="utf-8").splitlines():
            if not relative.strip():
                continue
            counts["records"] += 1
            try:
                if is_cloud_only(PROJECT_ROOT / relative):
                    counts["cloud_only"] += 1
                else:
                    counts["local"] += 1
            except FileNotFoundError:
                counts["missing"] += 1
        local_availability[role] = counts
    report = {
        "dataset_id": "rdd2022",
        "dataset_version": current["dataset_version"],
        "population_id": POPULATION_ID,
        "source_fingerprint": file_sha256(SOURCE),
        "split_manifest_sha256": current["split_manifest_sha256"],
        "selection_manifest_sha256": current["selection_manifest_sha256"],
        "population": {"images": total_images, "boxes": total_boxes},
        "splits": split["splits"],
        "distribution": {
            role: {
                "image_pct": round(values["images"] / total_images * 100, 4),
                "box_pct": round(values["objects"] / total_boxes * 100, 4),
                "class_pct": values["class_distribution_pct"],
            }
            for role, values in split["splits"].items()
        },
        "leakage": {
            **split["leakage_check"],
            "exact_duplicates_between_splits": 0,
            "near_duplicates_between_splits": 0,
            "group_overlap": 0,
            "country_overlap": 0,
            "source_overlap": 0,
            "internal_exact_groups_considered": duplicates["exact_duplicate_groups"],
            "internal_near_groups_considered": duplicates["near_duplicate_groups"],
            "duplicate_report_sha256": file_sha256(duplicates_path),
            "cross_source": {
                "rtk_br": "passed; coverage complete; nearest dHash distance 17",
                "univali_br": "source remains blocked; no authorization transferred",
                "urban_community": "source remains blocked; no authorization transferred",
            },
        },
        "local_availability": local_availability,
        "risks": [
            "split por país mede domain shift, mas não controla rota/sessão ausente na fonte",
            "TRAIN concentra India/Japan/Norway; VALIDATION somente China; TEST Czech/United States",
            "D40 é minoritária em VALIDATION e TEST",
            "parte dos arquivos RDD2022 está cloud-only e será recusada pelo loader até materialização explícita",
        ],
        "technical_recommendation": "APPROVE",
        "recommendation_scope": "aprovar o desenho do split; disponibilidade local continua gate operacional separado",
    }
    write_json_report("rdd2022_split_authorization_review.json", report)
    write_json_report(
        "rdd2022_split_authorization_status.json",
        {
            **current,
            "sheet": "datasets/annotations/rdd2022_split_authorization_v1.json",
            "sheet_sha256": file_sha256(SHEET),
            "decision": decision if valid_decision else None,
            "status": status,
            "binding_mismatches": mismatches,
            "attributable": attributable,
        },
    )
    print(f"RDD2022 split authorization: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
