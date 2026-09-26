"""The resumable ML checkpoint must never trust modified human-review imports."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/datasets"))


def test_preflight_counts_only_intact_known_decisions(tmp_path, monkeypatch):
    import review_annotations

    spec = importlib.util.spec_from_file_location(
        "preflight_training", ROOT / "scripts/datasets/preflight_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    review = {
        "package_sha256": "a" * 64,
        "proposals": [
            {
                "PROPOSAL_ID": "one",
                "PROPOSED_URMIND_CLASS": "A",
                "annotation_state_by_class": {"A": "NOT_ANNOTATED"},
                "lineage_reconciliation": None,
            }
        ],
    }
    bound = {
        "package_sha256": review["package_sha256"],
        "items": [
            {
                "id": "one",
                "proposed_class": "A",
                "annotation_state_by_class": {"A": "NOT_ANNOTATED"},
            }
        ],
    }
    decision = {
        "id": "one",
        "decision": "reject",
        "coverage": "AMBIGUOUS",
        "annotation_state_by_class": {"A": "AMBIGUOUS"},
        "reviewed_by": "person",
        "reviewed_at": "2026-09-25T12:00:00+00:00",
        "group": "route-1",
        "reason": "not target",
        "history": [],
    }
    folder = tmp_path / "review"
    imported = review_annotations.import_decisions(
        bound,
        {
            "package_sha256": review["package_sha256"],
            "training_authorized": False,
            "decisions": [decision],
        },
        folder,
    )
    assert module.validated_review_imports(review, folder)[0] == 1
    path = Path(imported["path"])
    tampered = json.loads(path.read_text(encoding="utf8"))
    tampered["decisions"][0]["id"] = "unknown"
    path.write_text(json.dumps(tampered), encoding="utf8")
    with pytest.raises(ValueError, match="filename/content hash mismatch"):
        module.validated_review_imports(review, folder)


def test_manifest_profile_streams_metadata_without_loading_images(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "preflight_training", ROOT / "scripts/datasets/preflight_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    manifest = tmp_path / "train.jsonl"
    manifest.write_text(
        json.dumps({"image_width": 1280, "image_height": 720, "boxes": [{"bbox": [1, 2, 3, 4]}]})
        + "\n"
        + json.dumps({"image_width": 1280, "image_height": 720, "boxes": []})
        + "\n",
        encoding="utf8",
    )
    profile = module._manifest_profile(manifest)
    assert profile["images"] == 2
    assert profile["boxes"] == 1
    assert profile["top_resolutions"] == [("1280x720", 2)]
    assert profile["index_bytes"] == manifest.stat().st_size


def test_smoke_release_requires_complete_matching_source_evidence():
    spec = importlib.util.spec_from_file_location(
        "preflight_training", ROOT / "scripts/datasets/preflight_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    package = {
        "items": [
            {
                "original": {
                    "source_release_manifest": "datasets/manifests/rdd2022.json",
                    "source_fingerprint": "a" * 64,
                },
                "media_sha256": {"image_relpath": "a" * 64},
            }
        ]
    }
    source = {
        "id": 21431547,
        "version": 1,
        "doi": "10.6084/m9.figshare.21431547.v1",
        "license": {"name": "CC BY 4.0"},
        "files": [
            {
                "name": "RDD2022_released_through_CRDDC2022.zip",
                "size": module.RDD_OFFICIAL_ARCHIVE_BYTES,
                "computed_md5": module.RDD_OFFICIAL_ARCHIVE_MD5,
            }
        ],
    }
    manifest = {"version": "2022-crddc", "license": "CC BY 4.0"}
    released = {
        "archive_official_md5": module.RDD_OFFICIAL_ARCHIVE_MD5,
        "bytes_to_release": module.RDD_OFFICIAL_ARCHIVE_BYTES,
        "passed": True,
        "deleted": True,
        "extracted_files_crc_verified": 85805,
    }
    assert module.verify_smoke_release(package, source, manifest, released) == (
        True,
        True,
    )
    assert module.verify_smoke_release(
        package,
        {**source, "files": [{**source["files"][0], "computed_md5": None}]},
        manifest,
        released,
    ) == (False, False)
    assert module.verify_smoke_release(
        package,
        {**source, "files": [{**source["files"][0], "computed_md5": "b" * 32}]},
        manifest,
        {**released, "archive_official_md5": "b" * 32},
    ) == (False, False)
    assert module.verify_smoke_release(
        package, source, {**manifest, "license": "unknown"}, released
    ) == (True, False)
    assert module.verify_smoke_release({"items": []}, source, manifest, released) == (False, False)


def test_smoke_candidates_exclude_partial_review_without_blocking_other_images():
    spec = importlib.util.spec_from_file_location(
        "preflight_training", ROOT / "scripts/datasets/preflight_training.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    package = {
        "items": [
            {"id": "good", "proposed_class": "A", "review_scope_classes": ["A", "B"]},
            {"id": "partial", "proposed_class": "A", "review_scope_classes": ["A", "B"]},
            {"id": "ambiguous", "proposed_class": "B", "review_scope_classes": ["A", "B"]},
        ]
    }
    decisions = {
        "good": {
            "decision": "approve",
            "annotation_state_by_class": {"A": "PRESENT_ANNOTATED", "B": "ABSENT_REVIEWED"},
        },
        "partial": {
            "decision": "approve",
            "annotation_state_by_class": {"A": "PRESENT_ANNOTATED", "B": "NOT_ANNOTATED"},
        },
        "ambiguous": {
            "decision": "ambiguous",
            "annotation_state_by_class": {"A": "ABSENT_REVIEWED", "B": "AMBIGUOUS"},
        },
    }
    eligible, excluded = module.smoke_semantic_candidates(package, decisions)
    assert eligible == {"A": ["good"]}
    assert excluded == {
        "partial": "PARTIAL_OR_CONFLICTING_CLASS_COVERAGE",
        "ambiguous": "AMBIGUOUS",
    }
