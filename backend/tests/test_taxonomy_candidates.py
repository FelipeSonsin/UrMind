from __future__ import annotations

from app.datasets.taxonomy_candidates import (
    load_candidates,
    readiness_by_class,
    validate_candidates,
)
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
