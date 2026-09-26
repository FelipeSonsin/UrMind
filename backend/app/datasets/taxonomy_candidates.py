"""Candidatos de dataset para a taxonomia V3 (Fase 10A, pesquisa — sem download).

O arquivo `datasets/metadata/taxonomy_v2_dataset_candidates.json` é o registro
pesquisado. Este módulo o valida e deriva o estado de dado por classe, que
precisa coincidir com `dataset_status` em `app.schemas.issue_taxonomy`.

Regras conservadoras: campo desconhecido é `null`, nunca "permitido"; domínio
aéreo não vira READY; licença não comercial ou proveniência não verificada
não chegam a READY_FOR_CURATION.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.issue_taxonomy import (
    ISSUES,
    TAXONOMY_VERSION,
    DatasetReadiness,
    ModelSupportStatus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CANDIDATES_PATH = PROJECT_ROOT / "datasets" / "metadata" / "taxonomy_v2_dataset_candidates.json"

REQUIRED_FIELDS = (
    "SOURCE",
    "SOURCE_URL",
    "OWNER",
    "VERSION",
    "PUBLICATION",
    "LICENSE",
    "LICENSE_URL",
    "IMAGE_DOMAIN",
    "CAPTURE_DEVICE",
    "GEOGRAPHY",
    "ANNOTATION_TYPE",
    "IMAGE_COUNT",
    "INSTANCE_COUNT",
    "CLASS_LIST",
    "CLASS_MAPPING",
    "NEGATIVE_IMAGES",
    "EMPTY_IMAGES",
    "DOMAIN_MATCH",
    "TRAINING_ALLOWED",
    "COMMERCIAL_USE_ALLOWED",
    "REDISTRIBUTION_ALLOWED",
    "ATTRIBUTION_REQUIRED",
    "PROVENANCE_VERIFIED",
    "KNOWN_LIMITATIONS",
)
DOMAIN_MATCHES = frozenset({"GOOD_MATCH", "PARTIAL_MATCH", "DOMAIN_MISMATCH"})


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["candidates"]


def validate_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    """Devolve a lista de violações; vazia quando o registro é coerente."""
    candidate_codes = {
        issue.issue_code
        for issue in ISSUES
        if issue.model_support_status is not ModelSupportStatus.EXPERIMENTAL_MODEL
    }
    problems: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cid = item.get("id", "<sem id>")
        if cid in seen:
            problems.append(f"{cid}: id duplicado")
        seen.add(cid)
        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            problems.append(f"{cid}: campos ausentes {missing}")
            continue
        readiness = item.get("READINESS")
        if readiness not in {r.value for r in DatasetReadiness} or readiness == "CURATED_IN_USE":
            problems.append(f"{cid}: READINESS inválido {readiness!r}")
        source_missing = item.get("SOURCE_STATUS") == "NEEDS_DATA_SOURCE"
        if source_missing and (
            readiness != "NEEDS_MORE_DATA"
            or item["SOURCE_URL"] is not None
            or item["LICENSE"] is not None
            or item["PROVENANCE_VERIFIED"] is not False
            or item["TRAINING_ALLOWED"] is not None
        ):
            problems.append(f"{cid}: fonte ausente não autoriza licença, provenance ou treinamento")
        if item["DOMAIN_MATCH"] not in DOMAIN_MATCHES and not (
            source_missing and item["DOMAIN_MATCH"] is None
        ):
            problems.append(f"{cid}: DOMAIN_MATCH inválido")
        if item["DOMAIN_MATCH"] == "DOMAIN_MISMATCH" and readiness != "DOMAIN_MISMATCH":
            problems.append(f"{cid}: domínio incompatível não pode ter READINESS {readiness}")
        if readiness == DatasetReadiness.READY_FOR_CURATION.value and not (
            item["PROVENANCE_VERIFIED"] is True
            and item["TRAINING_ALLOWED"] is True
            and item["LICENSE"]
            and item["COMMERCIAL_USE_ALLOWED"] is not False
        ):
            problems.append(f"{cid}: READY_FOR_CURATION exige licença e proveniência verificadas")
        unknown = set(item.get("target_issue_codes") or []) - candidate_codes
        if unknown or not item.get("target_issue_codes"):
            problems.append(f"{cid}: target_issue_codes fora das classes candidatas {unknown}")
    return problems


def readiness_by_class(candidates: list[dict[str, Any]]) -> dict[str, DatasetReadiness]:
    """Melhor estado alcançável por classe candidata, a partir do registro pesquisado."""
    result: dict[str, DatasetReadiness] = {}
    for issue in ISSUES:
        if issue.model_support_status is ModelSupportStatus.EXPERIMENTAL_MODEL:
            continue
        states = {
            c["READINESS"]
            for c in candidates
            if issue.issue_code in c.get("target_issue_codes", [])
        }
        if DatasetReadiness.READY_FOR_CURATION.value in states:
            result[issue.issue_code] = DatasetReadiness.READY_FOR_CURATION
        elif DatasetReadiness.NEEDS_HUMAN_REVIEW.value in states:
            result[issue.issue_code] = DatasetReadiness.NEEDS_HUMAN_REVIEW
        else:
            result[issue.issue_code] = DatasetReadiness.NEEDS_MORE_DATA
    return result


def coverage_matrix() -> dict[str, Any]:
    """Planning inventory only: never converts candidates or legacy approval into capability."""
    candidates = load_candidates()
    researched = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    problems = validate_candidates(candidates)
    if problems:
        raise ValueError(problems)
    local = {
        "URMIND_ROAD_D00": [("rdd2022", "bbox:D00")],
        "URMIND_ROAD_D10": [("rdd2022", "bbox:D10")],
        "URMIND_ROAD_D20": [("rdd2022", "bbox:D20")],
        "URMIND_ROAD_D40": [
            ("rdd2022", "bbox:D40"),
            ("univali_br", "semantic_mask:POTHOLE"),
            ("rtk_br", "semantic_mask:pothole"),
            ("urban_community", "bbox:pothole"),
        ],
        "URMIND_FADED_ROAD_MARKING": [("rdd2022", "bbox:D43/D44; candidate mapping")],
        "URMIND_OPEN_MANHOLE": [("urban_community", "bbox:open_manhole; unverified mapping")],
    }
    contextual = {
        "URMIND_HAZARDOUS_TREE",
        "URMIND_VEGETATION_ON_POWER_LINES",
        "URMIND_FALLEN_POWER_LINE",
        "URMIND_EXPOSED_WIRING",
        "URMIND_ABANDONED_VEHICLE",
        "URMIND_WATER_LEAK",
        "URMIND_MISSING_CURB_RAMP",
    }
    scenes = {"URMIND_FLOODED_ROAD", "URMIND_ROAD_EROSION", "URMIND_SINKHOLE"}
    categories: list[dict[str, Any]] = []
    for issue in ISSUES:
        code = issue.issue_code
        sources = [c for c in candidates if code in c.get("target_issue_codes", [])]
        available = local.get(code, [])
        blockers = ["HUMAN_REVIEW_REQUIRED", "GROUPING_UNRESOLVED", "EVALUATION_NOT_READY"]
        blockers += (
            ["PARTIAL_ANNOTATIONS", "MAPPING_UNVERIFIED"]
            if available
            else ["MISSING_DATA", "MISSING_ANNOTATIONS"]
        )
        if sources and any(not c["PROVENANCE_VERIFIED"] for c in sources):
            blockers.append("LICENSE_UNRESOLVED")
        action = (
            f"Revisar {', '.join(s for s, _ in available)} para {code}: confirmar todas as instâncias, "
            "negativos explícitos, grupos e direitos; registrar decisões por imagem."
            if available
            else f"Preparar coleta licenciada de {issue.display_name_pt}: cena completa, ativo normal como "
            "contraste, grupo/local/instante e duas revisões; verificar candidatos externos antes de adquirir."
        )
        categories.append(
            {
                "issue_code": code,
                "taxonomy_version": TAXONOMY_VERSION,
                "name": issue.display_name_pt,
                "definition": issue.visual_definition,
                "task": "attribute+contextual_review"
                if code in contextual
                else "mask+scene"
                if code in scenes
                else "bbox+state_review",
                "local_sources": [{"id": s, "annotation_type": a} for s, a in available],
                "external_sources": [
                    {
                        "id": c["id"],
                        "url": c["SOURCE_URL"],
                        "version": c["VERSION"],
                        "annotation_type": c["ANNOTATION_TYPE"],
                        "license": c["LICENSE"],
                        "provenance_verified": c["PROVENANCE_VERIFIED"],
                    }
                    for c in sources
                ],
                "research_followups": [
                    f
                    for f in researched.get("research_followups", [])
                    if code in f["target_issue_codes"]
                ],
                "approved_images": 0,
                "approved_instances": 0,
                "independent_approved_groups": 0,
                "count_scope": "new_cycle_approval; historical labels are not included",
                "annotation_states": {
                    "PRESENT_ANNOTATED": "candidate_only" if available else "unknown",
                    "ABSENT_REVIEWED": 0,
                    "NOT_ANNOTATED": "unknown",
                    "AMBIGUOUS": "unmeasured",
                },
                "annotation_coverage": "not established for all active categories per image",
                "scientific_use": "preparation_only",
                "human_review": "PENDING",
                "evaluation_evidence": None,
                "blockers": blockers,
                "next_action": action,
                "collection_form": {
                    "issue_code": code,
                    "taxonomy_version": TAXONOMY_VERSION,
                    "image_sha256": None,
                    "source": None,
                    "license": None,
                    "rights_holder": None,
                    "consent_record": None,
                    "captured_at": None,
                    "scene_group": None,
                    "sequence_id": None,
                    "duplicate_group": None,
                    "original_annotation": None,
                    "derived_annotation": None,
                    "object_state": None,
                    "context_evidence": None,
                    "incident_id": None,
                    "related_issue_codes": [],
                    "reviewer": None,
                    "reviewed_at": None,
                    "decision": None,
                    "reason": None,
                    "adjudication": None,
                    "annotation_state_by_class": {
                        item.issue_code: "NOT_ANNOTATED" for item in ISSUES
                    },
                    "training_authorized": False,
                },
                "blocker_actions": {
                    blocker: {
                        "MISSING_DATA": "Use this category's collection_form; inspect linked sources and approve rights/budget before acquisition.",
                        "MISSING_ANNOTATIONS": "Label original scene with the specified task and inclusion/exclusion examples; retain original.",
                        "PARTIAL_ANNOTATIONS": "Complete human labels per active class or restrict image to its annotated task; no YOLOX background conversion.",
                        "MAPPING_UNVERIFIED": "Adjudicate native label against definition, object state and explicit exclusions; version derived mapping.",
                        "LICENSE_UNRESOLVED": "Record per-image rights and source version, distinguish code license from imagery.",
                        "GROUPING_UNRESOLVED": "Bind panorama/session/route/time and exact/near duplicate group before assigning roles.",
                        "HUMAN_REVIEW_REQUIRED": "Use offline package or collection form, two reviewers plus adjudication; no automatic approval.",
                        "EVALUATION_NOT_READY": "Pre-register category thresholds, uncertainty and group support before any future final evaluation.",
                    }[blocker]
                    for blocker in blockers
                },
                "acceptance_criteria": {
                    "semantics": list(issue.included_examples),
                    "exclude": list(issue.excluded_examples),
                    "requirements": [
                        "image-bound licensed provenance",
                        "two reviewers and adjudication",
                        "complete labels for active tasks",
                        "group isolation",
                        "new-cycle authorization",
                        "per-category independent evaluation",
                    ],
                    "metrics": [
                        "precision",
                        "recall",
                        "hard-negative false positives",
                        "group support",
                        "domain and size strata",
                    ],
                    "ap": "required for bbox/mask task only",
                    "numeric_operating_limits": "human approval required before final lock",
                },
                "limitations": list(issue.limitations),
            }
        )
    return {
        "schema_version": 1,
        "taxonomy_version": TAXONOMY_VERSION,
        "REQUESTED_PRODUCT_SCOPE": [i.issue_code for i in ISSUES],
        "TRAINABLE_CLASS_SET": [],
        "VALIDATED_MODEL_CAPABILITIES": [],
        "categories": categories,
        "source_inventory_evidence": "datasets/STATUS.md; historical per-source reports retained; protected sets not reopened",
        "training_authorized": False,
        "partial_supervision_policy": "complete human labels or restrict task; NOT_ANNOTATED is never background",
    }


def training_class_plan(coverage: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive a preparation plan, never an authorization or detector head."""
    expected = {issue.issue_code for issue in ISSUES}
    rows = coverage["categories"]
    if (
        coverage["taxonomy_version"] != TAXONOMY_VERSION
        or len(rows) != len(expected)
        or {row["issue_code"] for row in rows} != expected
    ):
        raise ValueError("coverage taxonomy mismatch")
    detector = {f"URMIND_ROAD_{code}" for code in ("D00", "D10", "D20", "D40")}
    context = {
        "URMIND_SIDEWALK_OBSTRUCTION",
        "URMIND_VEGETATION_ON_POWER_LINES",
        "URMIND_VEGETATION_OBSTRUCTION",
        "URMIND_MISSING_CURB_RAMP",
        "URMIND_OBSTRUCTED_TRAFFIC_SIGN",
    }
    review = {"URMIND_HAZARDOUS_TREE", "URMIND_ABANDONED_VEHICLE", "URMIND_WATER_LEAK"}
    wave2 = {
        "URMIND_FALLEN_TREE",
        "URMIND_ILLEGAL_DUMPING",
        "URMIND_ROAD_DEBRIS",
        "URMIND_OPEN_MANHOLE",
        "URMIND_FLOODED_ROAD",
        "URMIND_SIDEWALK_DAMAGE",
        "URMIND_DAMAGED_TRAFFIC_SIGN",
        "URMIND_FADED_ROAD_MARKING",
    }
    classifications = []
    missing = []
    for row in rows:
        code = row["issue_code"]
        primary = (
            "DETECTOR_CLASS"
            if code in detector
            else "CONTEXT_ATTRIBUTE"
            if code in context
            else "REVIEW_ONLY"
            if code in review
            else "DATA_NOT_READY"
        )
        rationale = {
            "DETECTOR_CLASS": "Visible pavement morphology with existing native bbox and inclusion/exclusion protocol; new-cycle permission still absent.",
            "CONTEXT_ATTRIBUTE": "Requires relation to crossing, usable sidewalk, sign or wiring; annotate object plus scene relation, not an isolated-object label.",
            "REVIEW_ONLY": "Photo supports signs, not future tree failure, abandonment history or water origin; qualified contextual review required.",
            "DATA_NOT_READY": "Visual candidate under current definition; task-specific annotations, mapping or usable source evidence still missing.",
        }[primary]
        classifications.append(
            {
                "CATEGORY": code,
                "PRIMARY_STATE": primary,
                "RATIONALE": rationale,
                "VISUAL_DEFINITION": row["definition"],
                "ANNOTATION_PROTOCOL": row["acceptance_criteria"],
                "WAVE": "WAVE_1" if code in detector else "WAVE_2" if code in wave2 else "WAVE_3",
                "NEXT_ACTION": row["next_action"],
                "BLOCKER_ACTIONS": row["blocker_actions"],
                "TRAINING_AUTHORIZED": False,
                "EVIDENCE": "datasets/reports/taxonomy_coverage.json",
            }
        )
        if "MISSING_DATA" not in row["blockers"]:
            continue
        missing.append(
            {
                "CATEGORY": code,
                "MINIMUM_DATA_NEEDED": "Proposed curation pilot: 30 positive scenes and 30 reviewed negative scenes; not a training/evaluation sufficiency threshold.",
                "ANNOTATION_TYPE": "object localization plus scene relation"
                if primary == "CONTEXT_ATTRIBUTE"
                else "structured inspection/context review, not a YOLOX label"
                if primary == "REVIEW_ONLY"
                else row["task"],
                "POSITIVE_EXAMPLES_REQUIRED": {
                    "pilot_scenes": 30,
                    "definition": row["definition"],
                    "examples": row["acceptance_criteria"]["semantics"],
                },
                "NEGATIVE_EXAMPLES_REQUIRED": {
                    "pilot_scenes": 30,
                    "absence_requires_human_review": True,
                },
                "HARD_NEGATIVES": row["acceptance_criteria"]["exclude"],
                "INDEPENDENT_GROUPS_REQUIRED": {
                    "pilot_minimum_proposed": 15,
                    "basis": "scene/session/panorama, not folder or country; proposed planning floor, not power analysis",
                },
                "DOMAIN_REQUIREMENTS": [
                    "street-level Brazil target domain",
                    "at least three distinct locations in pilot",
                    "day/shadow and occlusion strata",
                    "normal asset and damage contrast",
                    "size and viewpoint strata",
                ],
                "LICENSE_REQUIREMENTS": [
                    "original creator/source URL",
                    "image-level rights or traceable official release grant",
                    "license version and attribution",
                    "intended training use explicitly authorized",
                    "uploader CC0 alone insufficient",
                ],
                "KNOWN_SOURCE_CANDIDATES": row["external_sources"] + row["research_followups"],
                "SOURCE_FALLBACK": "consented local collection using collection_form; do not infer rights from availability",
                "ESTIMATED_STORAGE": {
                    "pilot_images": 60,
                    "assumed_bytes_per_image_range": [500000, 2000000],
                    "metadata_allowance_fraction": 0.10,
                    "range_bytes": [33000000, 132000000],
                    "measured_source_bytes": None,
                    "acquisition_authorized": False,
                    "preflight": "check source cap, aggregate cap and 10 GB free disk before acquisition; reuse containers, no duplicate images",
                },
                "HUMAN_REVIEW_REQUIRED": True,
                "COLLECTION_FORM": row["collection_form"],
                "ACCEPTANCE": row["acceptance_criteria"],
                "PRIMARY_STATE": primary,
                "NEXT_ACTION": row["next_action"],
            }
        )
    plan = {
        "schema_version": 1,
        "taxonomy_version": TAXONOMY_VERSION,
        "classification_scope": "learning suitability only; DETECTOR_CLASS is not training authorization",
        "categories": classifications,
        "NOT_PHOTO_DETECTABLE_rationale": "Empty: v3 definitions are observable signs or explicitly apparent/possible states. Energization, leak cause, accident probability and future failure are not detector targets.",
        "waves_policy": "WAVE_1 closest to curation, not ready; WAVE_2 researched candidates needing rights/annotations; WAVE_3 new collection/context/inspection. All 35 retained.",
        "future_dataset": {
            "name_reserved": "urmind-urban-vision-v3",
            "state": "PROPOSED_NOT_CREATED",
            "CLASS_ORDER": [],
            "class_order_policy": "Only explicitly approved DETECTOR_CLASS entries may enter. Preserve approved native order as starting proposal; freeze order with dataset and model contracts before any train/export.",
            "ANNOTATION_SCHEMA": "reuse detection manifest producer; original bbox/mask retained, versioned derived xyxy in original pixels, dimensions, source label, four independent audit states, image/class completeness",
            "PROVENANCE_SCHEMA": [
                "source_release",
                "source_url",
                "original_creator",
                "license_evidence",
                "image_sha256",
                "annotation_sha256",
                "derivation_version",
                "review_scope_hash",
                "authorization_id",
            ],
            "GROUP_SCHEMA": [
                "source",
                "country",
                "location",
                "session",
                "sequence",
                "panorama",
                "exact_duplicate_group",
                "reviewed_near_duplicate_group",
                "protected_exclusion_group",
            ],
            "NEGATIVE_POLICY": "ABSENT_REVIEWED for every active class; missing/empty labels are not negatives.",
            "PARTIAL_ANNOTATION_POLICY": "Complete human annotation or restrict image to annotated task. Current YOLOX loss has no demonstrated per-class ignore support; do not mix partially labeled images into multiclass training.",
            "REVIEW_POLICY": "Two independent qualified reviewers, adjudicate disagreement, bind decisions to immutable bytes/version/scope; source rights and training authorization separate.",
            "DATASET_VERSIONING_POLICY": "Immutable manifests through existing producer/registry; changes to mapping, annotations, order, groups or rights invalidate prior authorization; preserve all history.",
            "authorization_form": {
                key: None
                for key in (
                    "dataset_version",
                    "manifest_sha256",
                    "class_order_sha256",
                    "taxonomy_version",
                    "purpose",
                    "allowed_roles",
                    "excluded_groups_sha256",
                    "rights_review",
                    "semantic_review",
                    "completeness_review",
                    "group_review",
                    "decision_owner",
                    "decision_at",
                    "expiry_or_review_trigger",
                )
            },
            "split_assignment": None,
            "training_authorized": False,
            "official_evaluation_authorized": False,
        },
        "gates": {
            "YOLOX_DATA_GATE": "OPEN",
            "YOLOX_CLASS_GATE": "OPEN",
            "YOLOX_HOLDOUT_GATE": "OPEN",
        },
        "xgboost": {
            "protocol": "datasets/annotations/tabular_labeling_protocol.json",
            "review_confirmed_is_other_targets": False,
            "severity": "Collect independent on-site damage rubric, units/photos, qualified reviewers and adjudication; blind to detector/rule scores.",
            "risk": "Prospective exposure register and adverse outcomes within a predeclared horizon; follow-up/censoring and timestamped evidence, not review_confirmed or rule score.",
            "priority": "Independent expert ranking under explicit policy/capacity, paired comparisons and adjudication; separate from severity and observed outcomes.",
            "minimum_collection": "First approve target rubric/horizon/policy; pilot 30 diverse independent cases per target with two reviewers to measure disagreement/missingness. Pilot is not training sufficiency; risk outcomes need actual follow-up.",
            "point_in_time": "Use existing CoreService.tabular_ground_truth snapshots before review, operational detector features and version, no perfect-label substituted features.",
            "training_authorized": False,
        },
    }
    return plan, {
        "schema_version": 1,
        "taxonomy_version": TAXONOMY_VERSION,
        "categories": missing,
        "total_categories": len(missing),
        "counts_are_proposed_pilot_floors": True,
        "total_estimated_range_bytes": [33000000 * len(missing), 132000000 * len(missing)],
        "acquisition_authorized": False,
        "budget_action": "Upper bound exceeds current headroom; stage one approved source/category at a time after measured preflight. Do not delete data to fit.",
    }
