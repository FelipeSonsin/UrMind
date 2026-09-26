import importlib.util
import sys
from pathlib import Path

import pytest


def module():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts/datasets"))
    spec = importlib.util.spec_from_file_location(
        "review_annotations", root / "scripts/datasets/review_annotations.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_stored_package_checks_hash_and_rejects_protected_input(tmp_path, monkeypatch):
    import json

    m = module()
    monkeypatch.setattr(sys.modules["_core"], "PROJECT_ROOT", tmp_path)
    path = tmp_path / "review.html"
    path.write_text(
        "const P="
        + json.dumps({"sheets": {"datasets/manifests/detection_test_authorized.jsonl": "x"}})
    )
    with pytest.raises(ValueError, match="HTML hash mismatch"):
        m.stored_package(path, "wrong")
    with pytest.raises(ValueError, match="outside allowed"):
        m.stored_package(path, m.file_sha256(path))


def test_summary_does_not_invent_semantic_confidence_or_uploader_rights():
    m = module()
    row = {
        "id": "urban:0",
        "source_sheet": "urban_community",
        "proposed_class": "URMIND_ROAD_D40",
        "original": {
            "image_relpath": "image.jpg",
            "source_label": "pothole",
            "bbox_xyxy": [0, 0, 1, 1],
        },
        "media_sha256": {"image_relpath": "x"},
        "annotation_state_by_class": {"URMIND_ROAD_D40": "NOT_ANNOTATED"},
    }
    result = m.summarize_review(
        {"package_sha256": "x", "items": [row]},
        {"urban_community": {"license": "CC0", "checksum_verified": "sim"}},
        [],
    )
    assert result["PROVENANCE_BLOCKED"] == 1
    assert result["HUMAN_REVIEW_READY"] == 1  # inspection readiness is not rights approval
    assert result["by_category"]["URMIND_ROAD_D40"]["HIGH_CONFIDENCE"] is None
    assert result["by_category"]["URMIND_ROAD_D40"]["BAD_LABEL"] is None
    assert result["proposals"][0]["LICENSE_STATUS"] == "LICENSE_UNKNOWN"
    assert result["training_authorized"] is False


def test_source_csv_declaration_never_proves_image_rights():
    m = module()
    row = {
        "id": "rdd_train_D40:0",
        "source_sheet": "train",
        "proposed_class": "URMIND_ROAD_D40",
        "original": {"image_relpath": "image.jpg", "source_label": "D40"},
        "media_sha256": {"image_relpath": "x"},
        "annotation_state_by_class": {"URMIND_ROAD_D40": "NOT_ANNOTATED"},
    }
    result = m.summarize_review(
        {"package_sha256": "x", "items": [row]},
        {"rdd2022": {"license": "CC BY 4.0", "checksum_verified": "sim"}},
        [],
    )
    assert result["proposals"][0]["LICENSE_STATUS"] == "LICENSE_UNKNOWN"
    assert result["proposals"][0]["PROVENANCE_STATUS"] == "PROVENANCE_UNKNOWN"
    assert result["PROVENANCE_BLOCKED"] == 1


def test_review_does_not_promote_human_decision_to_training_permission():
    m = module()
    package = {"package_sha256": "a" * 64, "items": [{"id": "one"}]}
    decision = {
        "id": "one",
        "decision": "approve",
        "coverage": "PRESENT_ANNOTATED",
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": "session-1",
        "reason": "original inspected",
        "history": [],
    }
    result = m.validate_decisions(
        package, {"package_sha256": "a" * 64, "training_authorized": False, "decisions": [decision]}
    )
    assert result["training_authorized"] is False
    assert result["reviewed"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed_by": ""},
        {"group": None},
        {"coverage": ""},
        {"decision": "correct", "correction": None},
    ],
)
def test_incomplete_human_decision_is_refused(change):
    m = module()
    decision = {
        "id": "one",
        "decision": "approve",
        "coverage": "PRESENT_ANNOTATED",
        "reviewed_by": "r",
        "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": "g",
        "reason": "reviewed",
        "history": [],
        **change,
    }
    with pytest.raises((ValueError, TypeError)):
        m.validate_decisions(
            {"package_sha256": "a", "items": [{"id": "one"}]},
            {"package_sha256": "a", "training_authorized": False, "decisions": [decision]},
        )


@pytest.mark.parametrize("raw", ["", ".", "...", "UNKNOWN", "N/A", "TBD", "UNCONFIRMED", "nao confirmado"])
def test_unknown_group_preserved_without_inventing_scene(raw):
    m = module()
    decision = {
        "id": "one", "decision": "approve", "coverage": "PRESENT_ANNOTATED",
        "reviewed_by": "r", "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": raw, "reason": "reviewed", "history": [],
    }
    response = {"package_sha256": "a", "training_authorized": False, "decisions": [decision]}
    assert m.validate_decisions({"package_sha256": "a", "items": [{"id": "one"}]}, response)["reviewed"] == 1
    assert m.group_status(raw) == "UNCONFIRMED"
    assert decision["group"] == raw


def test_reviewer_reported_group_is_not_automatically_confirmed():
    assert module().group_status("route-123") == "REPORTED_UNVERIFIED"


def test_stale_package_is_refused():
    with pytest.raises(ValueError):
        module().validate_decisions(
            {"package_sha256": "current", "items": []}, {"package_sha256": "old", "decisions": []}
        )


def test_stale_parent_cannot_receive_approval():
    m = module()
    with pytest.raises(ValueError, match="stale parent"):
        m.validate_decisions(
            {"package_sha256": "a", "items": [{"id": "one", "lineage_status": "STALE_PARENT"}]},
            {
                "package_sha256": "a",
                "training_authorized": False,
                "decisions": [{"id": "one", "decision": "approve"}],
            },
        )


def test_unreviewed_classes_cannot_be_changed_by_export():
    with pytest.raises(ValueError, match="other classes"):
        module().validate_decisions(
            {
                "package_sha256": "a",
                "items": [
                    {
                        "id": "one",
                        "proposed_class": "A",
                        "annotation_state_by_class": {"A": "NOT_ANNOTATED", "B": "NOT_ANNOTATED"},
                    }
                ],
            },
            {
                "package_sha256": "a",
                "training_authorized": False,
                "decisions": [
                    {
                        "id": "one",
                        "decision": "approve",
                        "coverage": "PRESENT_ANNOTATED",
                        "annotation_state_by_class": {
                            "A": "PRESENT_ANNOTATED",
                            "B": "ABSENT_REVIEWED",
                        },
                    }
                ],
            },
        )


def test_smoke_review_requires_coverage_for_every_active_class():
    m = module()
    item = {
        "id": "smoke-one",
        "proposed_class": "A",
        "review_scope_classes": ["A", "B"],
        "annotation_state_by_class": {"A": "NOT_ANNOTATED", "B": "NOT_ANNOTATED"},
    }
    decision = {
        "id": "smoke-one",
        "decision": "approve",
        "coverage": "PRESENT_ANNOTATED",
        "annotation_state_by_class": {"A": "PRESENT_ANNOTATED", "B": "ABSENT_REVIEWED"},
        "reviewed_by": "person",
        "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": "scene-1",
        "reason": "both active categories inspected",
        "history": [],
    }
    bound = {"package_sha256": "a", "items": [item]}
    response = {"package_sha256": "a", "training_authorized": False, "decisions": [decision]}
    assert m.validate_decisions(bound, response)["reviewed"] == 1
    with pytest.raises(ValueError, match="active-class"):
        m.validate_decisions(
            bound,
            {
                **response,
                "decisions": [
                    {**decision, "annotation_state_by_class": {"A": "PRESENT_ANNOTATED"}}
                ],
            },
        )
    with pytest.raises(ValueError, match="other classes"):
        m.validate_decisions(
            bound,
            {
                **response,
                "decisions": [
                    {
                        **decision,
                        "annotation_state_by_class": {
                            **decision["annotation_state_by_class"],
                            "C": "ABSENT_REVIEWED",
                        },
                    }
                ],
            },
        )


def test_import_is_idempotent_and_keeps_authorization_separate(tmp_path):
    import json

    m = module()
    package = {"package_sha256": "a" * 64, "items": [{"id": "one"}]}
    response = {
        "package_sha256": "a" * 64,
        "training_authorized": False,
        "decisions": [
            {
                "id": "one",
                "decision": "reject",
                "coverage": "AMBIGUOUS",
                "reviewed_by": "person",
                "reviewed_at": "2026-09-25T12:00:00+00:00",
                "group": "route-1",
                "reason": "not the proposed category",
                "history": [],
            }
        ],
    }
    first = m.import_decisions(package, response, tmp_path)
    second = m.import_decisions(package, response, tmp_path)
    assert first["import_status"] == "IMPORTED"
    assert second["import_status"] == "ALREADY_IMPORTED"
    saved = json.loads(Path(first["path"]).read_text(encoding="utf8"))
    assert saved["training_authorization"] is False
    assert saved["provenance_status"] == "UNVERIFIED"
    assert saved["license_status"] == "UNVERIFIED"
    assert len(list(tmp_path.glob("decisions_*.json"))) == 1


def test_import_refuses_training_flag_and_duplicate_decisions(tmp_path):
    m = module()
    package = {"package_sha256": "a", "items": [{"id": "one"}]}
    row = {
        "id": "one",
        "decision": "reject",
        "coverage": "AMBIGUOUS",
        "reviewed_by": "person",
        "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": "route-1",
        "reason": "unclear",
        "history": [],
    }
    with pytest.raises(ValueError, match="forbid training"):
        m.import_decisions(
            package,
            {"package_sha256": "a", "training_authorized": True, "decisions": [row]},
            tmp_path,
        )
    with pytest.raises(ValueError, match="duplicate"):
        m.import_decisions(
            package,
            {"package_sha256": "a", "training_authorized": False, "decisions": [row, row]},
            tmp_path,
        )
