"""Consent/provenance and activation gates. Fake scores only; no model inference."""

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.schemas.core import PhotoGatePolicy
from app.services.photo_reference import calibration_matches


def test_runtime_rejects_non_object_calibration_before_loading_models(tmp_path, monkeypatch):
    from app.services import photo_reference

    path = tmp_path / "calibration.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(photo_reference, "CALIBRATION", path)
    assert photo_reference.calibrated_policy(PhotoGatePolicy()) is None


spec = importlib.util.spec_from_file_location(
    "photo_calibration_cli", Path(__file__).resolve().parents[2] / "scripts/photo_gate/calibrate.py"
)
assert spec and spec.loader
calibrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrate)


def document():
    return {
        "schema": "urmind-photo-gate-calibration-v1",
        "activation_audit_id": str(uuid.uuid4()),
        "scene_sha256": "a" * 64,
        "face_sha256": "b" * 64,
        "blur_reviewed": True,
        "counts": {"street_positive": 20, "scene_negative": 20, "face_large": 5, "face_small": 5},
        "metrics": {
            "false_rejection": 0.0,
            "false_acceptance": 0.0,
            "face_large_recall": 1.0,
            "face_small_recall": 1.0,
            "face_false_positive": 0.0,
        },
        "scene_accept_margin": 0.02,
        "scene_reject_margin": -0.02,
        "dominant_face_ratio": 0.15,
    }


def test_fake_calibration_threshold_separates_cohorts_and_refuses_overlap():
    rows = [{"label": "street_positive", "margin": 0.1} for _ in range(20)] + [
        {"label": "scene_negative", "margin": -0.1} for _ in range(20)
    ]
    result = calibrate.threshold_curve(rows)
    assert result["meets_targets"]
    assert result["selected"]["false_rejection"] == result["selected"]["false_acceptance"] == 0
    for row in rows:
        row["margin"] = 0.1
    assert not calibrate.threshold_curve(rows)["meets_targets"]


@pytest.mark.parametrize("invalid", [None, "hash", "count", "metrics", "review", "audit", "policy"])
def test_activation_is_bound_to_hashes_counts_targets_review_audit_and_policy(invalid):
    item = document()
    if invalid == "hash":
        item["scene_sha256"] = "c" * 64
    if invalid == "count":
        item["counts"]["street_positive"] = 19
    if invalid == "metrics":
        item["metrics"]["false_acceptance"] = 0.2
    if invalid == "review":
        item["blur_reviewed"] = False
    if invalid == "audit":
        item["activation_audit_id"] = ""
    if invalid == "policy":
        item["scene_accept_margin"] = 0.3
    assert calibration_matches(
        item, scene_sha="a" * 64, face_sha="b" * 64, policy=PhotoGatePolicy()
    ) is (invalid is None)


@pytest.mark.parametrize("failure", [None, "consent", "duplicate", "scientific", "escape", "label"])
def test_corpus_validation_before_inference(tmp_path, failure):
    root = tmp_path / "repository"
    index = root / "datasets/manifests"
    index.mkdir(parents=True)
    folder = tmp_path / "external-consented"
    folder.mkdir()
    photo = folder / "image.png"
    Image.new("RGB", (32, 32), "green").save(photo)
    checksum = calibrate.digest(photo)
    (index / "hashes.json").write_text(
        json.dumps({"sha256": checksum if failure == "scientific" else "f" * 64})
    )
    filename = "../external-consented/image.png" if failure == "escape" else "image.png"
    if failure == "escape":
        Image.new("RGB", (32, 32), "blue").save(tmp_path / "outside.png")
        filename = "../outside.png"
    line = f"{filename},{'invalid' if failure == 'label' else 'street_positive'},,Fixture author,{'' if failure == 'consent' else 'test consent'},\n"
    (folder / "manifest.csv").write_text(
        "file,label,subtype,author,license_or_consent,notes\n"
        + line
        + (line if failure == "duplicate" else "")
    )
    if failure:
        with pytest.raises(ValueError):
            calibrate.validate_corpus(folder, root=root)
    else:
        records = calibrate.validate_corpus(folder, root=root)
        assert records[0]["sha256"] == checksum
        assert "author" not in records[0] and "license_or_consent" not in records[0]


def test_face_metrics_missing_cohort_is_unknown_not_perfect():
    result = calibrate.face_metrics(
        [{"label": "street_positive", "face_count": 0, "face_area": 0}], 0.15
    )
    assert result["face_large_recall"] is None and result["face_small_recall"] is None
    assert result["face_false_positive"] is None
