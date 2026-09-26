from __future__ import annotations

from app.datasets.taxonomy_candidates import (
    coverage_matrix,
    load_candidates,
    readiness_by_class,
    training_class_plan,
    validate_candidates,
)


def test_preparation_partitions_scope_without_authorizing_detector_head():
    plan, missing = training_class_plan(coverage_matrix())
    assert len(plan["categories"]) == 35
    assert len({r["CATEGORY"] for r in plan["categories"]}) == 35
    assert {r["PRIMARY_STATE"] for r in plan["categories"]} <= {
        "DETECTOR_CLASS",
        "CONTEXT_ATTRIBUTE",
        "REVIEW_ONLY",
        "NOT_PHOTO_DETECTABLE",
        "DATA_NOT_READY",
    }
    assert not plan["future_dataset"]["CLASS_ORDER"]
    assert all(not r["TRAINING_AUTHORIZED"] for r in plan["categories"])
    assert missing["total_categories"] == 29
    assert all(
        r["HUMAN_REVIEW_REQUIRED"] and not r["ESTIMATED_STORAGE"]["acquisition_authorized"]
        for r in missing["categories"]
    )


def test_preparation_rejects_missing_or_duplicate_category():
    import pytest

    coverage = coverage_matrix()
    coverage["categories"][-1] = coverage["categories"][0]
    with pytest.raises(ValueError, match="taxonomy mismatch"):
        training_class_plan(coverage)


from app.schemas.issue_taxonomy import get_issue


def test_research_registry_is_internally_consistent() -> None:
    assert validate_candidates(load_candidates()) == []


def test_taxonomy_dataset_status_matches_the_researched_registry() -> None:
    for code, readiness in readiness_by_class(load_candidates()).items():
        issue = get_issue(code)
        assert issue is not None
        assert issue.dataset_status is readiness, code


def test_validator_rejects_optimistic_entries() -> None:
    base = load_candidates()[0]
    aerial_ready = {
        **base,
        "id": "x",
        "DOMAIN_MATCH": "DOMAIN_MISMATCH",
        "READINESS": "READY_FOR_CURATION",
    }
    unverified_ready = {
        **base,
        "id": "y",
        "PROVENANCE_VERIFIED": False,
        "READINESS": "READY_FOR_CURATION",
    }
    noncommercial_ready = {
        **base,
        "id": "z",
        "COMMERCIAL_USE_ALLOWED": False,
        "READINESS": "READY_FOR_CURATION",
    }
    active_class = {**base, "id": "w", "target_issue_codes": ["URMIND_ROAD_D40"]}
    problems = validate_candidates(
        [aerial_ready, unverified_ready, noncommercial_ready, active_class]
    )
    assert any(p.startswith("x:") for p in problems)
    assert any(p.startswith("y:") for p in problems)
    assert any(p.startswith("z:") for p in problems)
    assert any(p.startswith("w:") for p in problems)


def test_no_candidate_is_ready_for_curation_in_this_round() -> None:
    # Nothing was downloaded or human-reviewed yet; READY would overstate the evidence.
    assert all(c["READINESS"] != "READY_FOR_CURATION" for c in load_candidates())


def test_full_scope_matrix_keeps_all_categories_without_authorizing_them():
    from app.datasets.taxonomy_candidates import coverage_matrix
    from app.schemas.issue_taxonomy import ISSUES

    result = coverage_matrix()
    assert set(result["REQUESTED_PRODUCT_SCOPE"]) == {i.issue_code for i in ISSUES}
    assert len(result["categories"]) == 35
    assert result["TRAINABLE_CLASS_SET"] == []
    assert result["VALIDATED_MODEL_CAPABILITIES"] == []
    for row in result["categories"]:
        assert row["next_action"] and row["acceptance_criteria"]
        assert row["approved_instances"] == 0
        assert row["annotation_states"]["NOT_ANNOTATED"] == "unknown"
