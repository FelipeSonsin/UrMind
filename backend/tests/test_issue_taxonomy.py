from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ml.taxonomy import MODEL_V1_CANONICAL_CLASS_ORDER
from app.schemas.core import UrmindClass
from app.schemas.issue_taxonomy import (
    ISSUES,
    TAXONOMY_VERSION,
    DatasetReadiness,
    IssueFamily,
    ModelSupportStatus,
    ResponsibilityDomain,
    get_issue,
    model_may_emit,
    taxonomy_payload,
)

V2_CANDIDATES = {
    "URMIND_FALLEN_TREE",
    "URMIND_FALLEN_BRANCH",
    "URMIND_ILLEGAL_DUMPING",
    "URMIND_ROAD_DEBRIS",
    "URMIND_OPEN_MANHOLE",
    "URMIND_BLOCKED_DRAIN",
    "URMIND_FLOODED_ROAD",
    "URMIND_SIDEWALK_DAMAGE",
    "URMIND_SIDEWALK_OBSTRUCTION",
    "URMIND_DAMAGED_TRAFFIC_SIGN",
    "URMIND_FALLEN_TRAFFIC_SIGN",
    "URMIND_DAMAGED_STREETLIGHT_POLE",
    "URMIND_DAMAGED_BARRIER",
}


V3_ADDITIONS = {
    "URMIND_HAZARDOUS_TREE",
    "URMIND_VEGETATION_ON_POWER_LINES",
    "URMIND_VEGETATION_OBSTRUCTION",
    "URMIND_FALLEN_POWER_LINE",
    "URMIND_EXPOSED_WIRING",
    "URMIND_SINKHOLE",
    "URMIND_OPEN_TRENCH",
    "URMIND_DAMAGED_MANHOLE_COVER",
    "URMIND_FADED_ROAD_MARKING",
    "URMIND_ROAD_EROSION",
    "URMIND_DAMAGED_CURB",
    "URMIND_MISSING_CURB_RAMP",
    "URMIND_DAMAGED_TACTILE_PAVING",
    "URMIND_DAMAGED_BUS_STOP",
    "URMIND_OBSTRUCTED_TRAFFIC_SIGN",
    "URMIND_DAMAGED_TRAFFIC_LIGHT",
    "URMIND_ABANDONED_VEHICLE",
    "URMIND_WATER_LEAK",
}


def test_v3_candidates_have_evidence_limits_and_no_automatic_support() -> None:
    assert TAXONOMY_VERSION == "urmind-issue-taxonomy-v3"
    assert len(ISSUES) == 35
    for code in V3_ADDITIONS:
        issue = get_issue(code)
        assert issue is not None
        assert issue.model_support_status is ModelSupportStatus.DATA_REQUIRED
        assert issue.dataset_status is DatasetReadiness.NEEDS_MORE_DATA
        assert issue.photo_detectable in (True, False, "limited")
        assert issue.limitations and issue.risk_groups
        assert not model_may_emit(code)
    assert (
        get_issue("URMIND_FALLEN_POWER_LINE").triage_priority_hint == "maximum_pending_validation"
    )


def test_v2_codes_remain_readable_without_reclassification() -> None:
    assert V2_CANDIDATES <= {issue.issue_code for issue in ISSUES}
    assert all(get_issue(code) is not None for code in V2_CANDIDATES)


def test_codes_are_unique_and_cover_every_family() -> None:
    codes = [issue.issue_code for issue in ISSUES]
    assert len(codes) == len(set(codes))
    assert {issue.family for issue in ISSUES} == set(IssueFamily)
    assert V2_CANDIDATES <= set(codes)


def test_public_labels_reuse_registry_and_preserve_legacy_labels() -> None:
    from app.services.public_view import CLASS_LABELS, class_label

    for code, label in CLASS_LABELS.items():
        assert class_label(code) == label
    for code in V3_ADDITIONS:
        assert class_label(code) == get_issue(code).display_name_pt


def test_v1_road_classes_are_preserved_with_the_model_class_order() -> None:
    for code in MODEL_V1_CANONICAL_CLASS_ORDER:
        issue = get_issue(code)
        assert issue is not None
        assert issue.family is IssueFamily.ROAD_SURFACE
        assert model_may_emit(code)


def test_no_class_claims_an_active_model_while_only_a_rejected_shadow_exists() -> None:
    # The only visual model is EXPERIMENTAL_SHADOW and REJECTED on the Frozen Test.
    assert all(
        issue.model_support_status is not ModelSupportStatus.ACTIVE_MODEL for issue in ISSUES
    )


def test_every_new_class_starts_without_model_support() -> None:
    for code in V2_CANDIDATES:
        issue = get_issue(code)
        assert issue is not None
        assert issue.model_support_status in {
            ModelSupportStatus.DATA_REQUIRED,
            ModelSupportStatus.REVIEW_ONLY,
        }
        assert issue.dataset_status is not DatasetReadiness.CURATED_IN_USE
        assert not model_may_emit(code)
        assert issue.taxonomy_version == TAXONOMY_VERSION


@pytest.mark.parametrize("code", ["", "URMIND_UNKNOWN", "D40", "urmind_road_d40", "FALLEN_TREE"])
def test_unknown_or_non_canonical_codes_fail_closed(code: str) -> None:
    assert not model_may_emit(code)


def test_legacy_references_point_to_existing_v1_codes_only() -> None:
    legacy = {member.value for member in UrmindClass}
    for issue in ISSUES:
        assert set(issue.related_legacy_codes) <= legacy


def test_every_issue_has_the_required_definition_fields() -> None:
    for issue in ISSUES:
        assert issue.display_name_pt and issue.display_name_en
        assert issue.visual_definition and issue.description
        assert issue.included_examples and issue.excluded_examples
        assert issue.responsibility_domain
        assert isinstance(issue.responsibility_domain, ResponsibilityDomain)
        assert issue.legacy_responsibility_domain
        assert issue.possible_impact_domains
        assert issue.applicable_context_features


def test_public_taxonomy_endpoint_serves_the_canonical_registry() -> None:
    from app.main import app

    # No context manager: the lifespan (database) is not needed for this route.
    response = TestClient(app).get("/api/v1/public/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body == taxonomy_payload()
    fallen = next(i for i in body["issues"] if i["issue_code"] == "URMIND_FALLEN_TREE")
    assert fallen["model_may_emit"] is False
    assert fallen["model_support_status"] == "DATA_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issue_code",
    sorted(
        issue.issue_code
        for issue in ISSUES
        if issue.model_support_status is ModelSupportStatus.DATA_REQUIRED
    ),
)
async def test_worker_refuses_detection_of_a_data_required_class(monkeypatch, issue_code) -> None:
    from app import worker as worker_module
    from app.worker import Worker

    capture_id = uuid.uuid4()
    capture = SimpleNamespace(
        quality={}, storage_path="u/x.jpg", detections=[], source="pwa_photo", point="fixture-point"
    )
    job = {"msg_id": 1, "message": {"capture_id": str(capture_id)}, "read_ct": 1}
    final_states: list[str] = []
    added: list[object] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class Database:
        @staticmethod
        def sessionmaker():
            return Session()

    model = SimpleNamespace(id=uuid.uuid4())

    class Queue:
        def __init__(self, _session):
            pass

        async def read_job(self, _timeout):
            return job

        async def set_capture_inference(self, item, state):
            item.quality["inference"] = state

        async def configured_vision_model(self, *_args):
            return model

        async def has_detections_from(self, *_args):
            return False

    class Captures:
        def __init__(self, _session):
            pass

        async def get(self, _id):
            return capture

        async def add_detections(self, _capture, detections):
            added.extend(detections)

    class Storage:
        async def download(self, _path):
            return b"jpeg"

    detector = SimpleNamespace(
        detect=lambda *_a, **_k: [
            SimpleNamespace(
                urmind_class=issue_code,
                confidence=0.9,
                bbox={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            )
        ],
        provider="CPUExecutionProvider",
    )
    monkeypatch.setattr(worker_module, "InferenceRepository", Queue)
    monkeypatch.setattr(worker_module, "CaptureRepository", Captures)
    worker = Worker(Database(), Storage())
    worker._score_threshold = 0.25
    monkeypatch.setattr(worker, "_detector_for", lambda _model: detector)

    async def final_state(_capture_id, _job, status, _detail):
        final_states.append(status)

    monkeypatch.setattr(worker, "_final_state", final_state)
    assert await worker.process_one() is True
    assert final_states == ["model_not_available"]
    assert added == []


def test_frontend_playwright_fixture_matches_the_canonical_registry() -> None:
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[2] / "frontend/tests/taxonomy.fixture.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == taxonomy_payload()
