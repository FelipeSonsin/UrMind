"""Phase 8 scaffolding: leakage protection, splits, readiness and promotion gate.

Rows are built by the real FeatureBuilder from synthetic in-memory records. This
is test fixture data only; it is never evidence for a model.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from app.api.v1.core import export_ground_truth, ground_truth_summary
from app.auth import AuthenticatedUser
from app.ml.tabular import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURE_PATHS,
    TabularExportError,
    binary_metrics,
    build_example,
    build_tabular_dataset_version,
    flatten_snapshot,
    group_split,
    leakage_groups,
    prior_baseline,
    promotion_gate,
    reliability_bins,
    temporal_split,
    train_xgboost,
    training_readiness,
)


@pytest.mark.asyncio
async def test_ground_truth_summary_and_export_traverse_1200_events_without_cap():
    event_ids = [uuid.UUID(int=value) for value in range(1200, 0, -1)]

    class Decisions:
        async def reviewed_event_ids(self, after, limit):
            return [
                identifier
                for identifier in event_ids
                if after is None or identifier.int < after.int
            ][:limit]

    class Service:
        decisions = Decisions()

        async def tabular_ground_truth(self, ids):
            return {
                "entries": [
                    {
                        "event_id": str(identifier),
                        "status": "consensus",
                        "decision": "confirm",
                        "eligible": True,
                        "reason": None,
                    }
                    for identifier in ids
                ],
                "rows": [
                    {"event_id": str(identifier), "target": 1, "road_segment_id": "seg-1"}
                    for identifier in ids
                ],
                "counts_by_class": {"D40": len(ids)},
            }

    reviewer = AuthenticatedUser("reviewer", None, "authenticated", urmind_role="reviewer")
    ordinary = AuthenticatedUser("owner", None, "authenticated")
    with pytest.raises(HTTPException) as denied:
        await ground_truth_summary(ordinary, Service(), Response())
    assert denied.value.status_code == 403
    summary = await ground_truth_summary(reviewer, Service(), Response())
    assert summary == {
        "reviewed_events": 1200,
        "eligible_events": 1200,
        "counts_by_class": {"D40": 1200},
        "confirmed": 1200,
        "rejected": 0,
        "corrected": 0,
        "conflicts": 0,
        "by_resolution_status": {"consensus": 1200},
        "ineligible_reasons": {},
        # Every row shares one road segment: one independent group, not 1200.
        "independent_groups": 1,
        "training_authorized": False,
    }
    stream = await export_ground_truth(reviewer, Service())
    lines = [json.loads(chunk) async for chunk in stream.body_iterator]
    assert lines[0]["training_authorized"] is False
    assert len(lines) == 1201
    assert len({row["event_id"] for row in lines[1:]}) == 1200


from app.services.features import FeatureInput, build_features

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_retrospective_history_cannot_be_relabelled_as_scientific_features() -> None:
    snapshot = _snapshot()
    snapshot["history"] = {
        "query_mode": "RETROSPECTIVE_ANALYTICS",
        "previous_events_same_segment": 9,
    }
    with pytest.raises(TabularExportError, match="retrospective"):
        flatten_snapshot(snapshot)


def _snapshot(
    *,
    segment: str | None = "seg-1",
    capture: uuid.UUID | None = None,
    occurred_at: datetime = T0,
    status: str = "detected",
    rain_temporal: str | None = None,
) -> dict:
    capture = capture or uuid.uuid4()
    detection = SimpleNamespace(
        id=uuid.uuid4(),
        capture_id=capture,
        urmind_class="URMIND_ROAD_D40",
        confidence=0.7,
        model_version_id=None,
        bbox={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        capture_id=capture,
        status=status,
        urmind_class="URMIND_ROAD_D40",
        occurred_at=occurred_at,
        location_accuracy_m=8.0,
        road_segment_id=segment,
        distance_to_road_m=3.0,
        factors={"evidence": {"detection_ids": [str(detection.id)], "capture_ids": [str(capture)]}},
    )
    contexts = []
    if rain_temporal is not None:
        ingested = occurred_at + (
            timedelta(hours=1) if rain_temporal == "post" else -timedelta(hours=1)
        )
        contexts.append(
            SimpleNamespace(
                source="open_meteo_rain",
                ingested_at=ingested,
                payload={
                    "status": "ok",
                    "fetched_at": ingested.isoformat(),
                    "data": {"rain_mm_24h": 12.0, "endpoint": "forecast"},
                },
            )
        )
    return build_features(
        FeatureInput(
            event=event,
            capture=SimpleNamespace(quality={}, source_location="gps_device", source="pwa_photo"),
            detections=[detection],
            road_segment=SimpleNamespace(highway="residential", jurisdiction="municipal")
            if segment
            else None,
            contexts=contexts,
            previous_event_times=[],
        )
    )


def _example(target: int = 1, **kwargs) -> object:
    snap = _snapshot(**kwargs)
    occurred = datetime.fromisoformat(snap["event"]["occurred_at"])
    return build_example(
        snap,
        snapshot_collected_at=occurred + timedelta(minutes=5),
        label_at=occurred + timedelta(days=1),
        review_confirmed=bool(target),
    )


def test_feature_allowlist_never_contains_decision_or_identity_fields() -> None:
    paths = {path for path, _ in FEATURE_COLUMNS.values()}
    assert not paths & FORBIDDEN_FEATURE_PATHS
    assert not any("status" in p or "review" in p or "severity" in p for p in paths)


def test_review_outcome_in_event_status_does_not_change_the_row() -> None:
    # The same evidence reviewed as confirmed or rejected must produce equal features.
    capture = uuid.uuid4()
    detected = flatten_snapshot(_snapshot(capture=capture, status="detected"))
    confirmed = flatten_snapshot(_snapshot(capture=capture, status="confirmed"))
    rejected = flatten_snapshot(_snapshot(capture=capture, status="rejected"))
    assert detected == confirmed == rejected


def test_snapshot_taken_after_the_label_is_refused() -> None:
    snap = _snapshot()
    occurred = datetime.fromisoformat(snap["event"]["occurred_at"])
    with pytest.raises(TabularExportError, match="vazamento"):
        build_example(
            snap,
            snapshot_collected_at=occurred + timedelta(days=2),
            label_at=occurred + timedelta(days=1),
            review_confirmed=True,
        )


def test_post_event_context_is_masked_as_missing() -> None:
    before = flatten_snapshot(_snapshot(rain_temporal="pre"))
    after = flatten_snapshot(_snapshot(rain_temporal="post"))
    assert before["rain_mm_24h"] == 12.0
    assert after["rain_mm_24h"] is None


@pytest.mark.parametrize("status", [None, "unknown", "unrecognized"])
def test_unknown_context_time_is_masked_as_missing(status) -> None:
    snapshot = _snapshot(rain_temporal="pre")
    snapshot["provenance"]["context"]["open_meteo_rain"]["temporal_status"] = status
    assert flatten_snapshot(snapshot)["rain_mm_24h"] is None


def test_seeded_group_split_is_invariant_to_input_order() -> None:
    examples = [_example(segment=f"seg-{i % 12}") for i in range(60)]
    assert (
        group_split(examples, seed=7).summary()
        == group_split(list(reversed(examples)), seed=7).summary()
    )


def test_dataset_digest_binds_group_and_temporal_lineage() -> None:
    example = _example()
    original = build_tabular_dataset_version("x", [example]).content_sha256
    for changed in (
        replace(example, capture_ids=("different-capture",)),
        replace(example, road_segment_id="different-road"),
        replace(example, label_at=example.label_at + timedelta(days=1)),
    ):
        assert build_tabular_dataset_version("x", [changed]).content_sha256 != original


def test_quantity_of_boolean_labels_does_not_establish_ground_truth() -> None:
    examples = [_example(i % 2, segment=f"seg-{i}") for i in range(200)]
    result = training_readiness(examples)
    assert result["status"] == "BLOCKED_DATA"
    assert any("Review" in reason for reason in result["blockers"])


def test_review_linkage_matches_event_time_author_and_binary_decision() -> None:
    snapshot = _snapshot()
    label_at = T0 + timedelta(days=1)
    review_fields = {
        "id": uuid.uuid4(),
        "event_id": snapshot["event"]["event_id"],
        "reviewer": "test-human-reviewer",
        "decision": "confirm",
        "created_at": label_at,
    }
    arguments = {
        "snapshot_collected_at": T0 + timedelta(minutes=5),
        "label_at": label_at,
        "review_confirmed": True,
    }
    example = build_example(snapshot, review=SimpleNamespace(**review_fields), **arguments)
    assert example.review_id == str(review_fields["id"])
    for invalid in (
        {"id": None},
        {"event_id": "other-event"},
        {"reviewer": None},
        {"decision": "reject"},
        {"decision": "correct"},
        {"created_at": T0},
    ):
        with pytest.raises(TabularExportError, match="Review"):
            build_example(
                snapshot, review=SimpleNamespace(**{**review_fields, **invalid}), **arguments
            )


def test_sufficient_rows_do_not_authorize_training_or_import_xgboost() -> None:
    examples = [
        replace(_example(i % 2, segment=f"seg-{i}"), review_id=f"fixture-review-{i}", detector_versions=("detector-v1",))
        for i in range(200)
    ]
    assert training_readiness(examples)["status"] == "BLOCKED_DATA"
    assert any("autorização de uso" in reason for reason in training_readiness(examples)["blockers"])
    assert training_readiness(examples)["training_authorized"] is False
    with pytest.raises(TabularExportError, match="BLOCKED_DATA"):
        train_xgboost(examples, seed=7)


def test_tabular_export_requires_operational_detector_lineage():
    from dataclasses import asdict

    from app.ml.tabular import validate_ground_truth_export

    example = replace(
        _example(), review_id="persisted-review", snapshot_id="assessment-fixture",
        review_status="consensus", detector_origin="persisted_detection",
    )
    row = asdict(example)
    row["occurred_at"] = example.occurred_at.isoformat()
    row["snapshot_collected_at"] = example.snapshot_collected_at.isoformat()
    row["label_at"] = example.label_at.isoformat()
    result = validate_ground_truth_export({"training_authorized": False, "rows": [row]})
    assert result["detector_lineage_present"] == 0
    assert result["readiness"]["status"] == "BLOCKED_DATA"
    assert any("detector" in reason for reason in result["readiness"]["blockers"])
    row["detector_versions"] = ["detector-v1"]
    assert validate_ground_truth_export({"training_authorized": False, "rows": [row]})["detector_lineage_present"] == 1
    row["snapshot_collected_at"] = (example.label_at + timedelta(minutes=1)).isoformat()
    with pytest.raises(TabularExportError, match="temporal leakage"):
        validate_ground_truth_export({"training_authorized": False, "rows": [row]})
    row["snapshot_collected_at"] = example.snapshot_collected_at.isoformat()
    for field, invalid, message in (
        ("capture_ids", [None], "lineage"),
        ("detector_versions", [None], "lineage"),
        ("occurred_at", example.occurred_at.replace(tzinfo=None).isoformat(), "timezone"),
    ):
        with pytest.raises(TabularExportError, match=message):
            validate_ground_truth_export({"training_authorized": False, "rows": [{**row, field: invalid}]})


def test_training_label_must_be_available_before_validation_cutoff() -> None:
    example = _example()
    example = replace(example, label_at=T0 + timedelta(days=40))
    result = temporal_split(
        [example], validation_from=T0 + timedelta(days=30), test_from=T0 + timedelta(days=45)
    )
    assert result["train"] == []
    assert result["dropped_for_label_availability"]["train"] == 1


def test_other_feature_schema_versions_are_refused() -> None:
    snap = _snapshot()
    snap["feature_schema_version"] = "urmind-features-v0"
    with pytest.raises(TabularExportError, match="feature schema"):
        flatten_snapshot(snap)


def test_group_split_keeps_segments_and_captures_in_one_split() -> None:
    shared_capture = uuid.uuid4()
    examples = [_example(segment=f"seg-{i % 12}") for i in range(60)]
    examples += [
        _example(segment=None, capture=shared_capture),
        _example(segment=None, capture=shared_capture),
    ]
    groups = leakage_groups(examples)
    split = group_split(examples)
    where = {}
    for name in ("train", "validation", "test"):
        for example in getattr(split, name):
            where.setdefault(groups[example.event_id], set()).add(name)
    assert all(len(splits) == 1 for splits in where.values())
    shared = [e for e in examples if str(shared_capture) in e.capture_ids]
    assert groups[shared[0].event_id] == groups[shared[1].event_id]


def test_temporal_split_drops_test_rows_that_share_a_segment_with_train() -> None:
    train = _example(segment="seg-a", occurred_at=T0)
    test_same_segment = _example(segment="seg-a", occurred_at=T0 + timedelta(days=60))
    test_new_segment = _example(segment="seg-b", occurred_at=T0 + timedelta(days=60))
    result = temporal_split(
        [train, test_same_segment, test_new_segment],
        validation_from=T0 + timedelta(days=30),
        test_from=T0 + timedelta(days=45),
    )
    assert result["train"] == [train]
    assert result["test"] == [test_new_segment]
    assert result["dropped_for_group_leakage"]["test"] == 1


def test_readiness_is_blocked_without_enough_ground_truth() -> None:
    readiness = training_readiness([_example(1), _example(0)])
    assert readiness["status"] == "BLOCKED_DATA"
    assert readiness["blockers"]


def test_xgboost_harness_fails_closed_before_touching_the_library() -> None:
    with pytest.raises(TabularExportError, match="BLOCKED_DATA"):
        train_xgboost([_example(1)], seed=1)


def test_dataset_version_is_deterministic_and_pins_the_feature_schema() -> None:
    examples = [_example(i % 2, segment=f"seg-{i}") for i in range(20)]
    first = build_tabular_dataset_version("tab-test", examples)
    second = build_tabular_dataset_version("tab-test", list(reversed(examples)))
    assert first.content_sha256 == second.content_sha256
    assert first.name == second.name == f"tab-test-{first.content_sha256[:12]}"
    assert first.split["status"] == "NOT_GENERATED"
    assert first.feature_schema_version == "urmind-features-v1"
    assert first.readiness["status"] == "BLOCKED_DATA"


@pytest.mark.asyncio
async def test_core_export_binds_immutable_dataset_version_without_claiming_a_split() -> None:
    from app.ml.tabular import (
        feature_schema_sha256,
        label_schema_sha256,
        validate_ground_truth_export,
    )
    from app.services.core import CoreService

    features = _snapshot()
    event_id = features["event"]["event_id"]
    assessment_id = uuid.uuid4()
    collected_at = T0 + timedelta(minutes=5)
    reviewed_at = T0 + timedelta(days=1)
    review_id = uuid.uuid4()
    votes = [
        {
            "event_id": event_id,
            "review_id": str(uuid.uuid4() if index == 0 else review_id),
            "reviewer": f"synthetic-reviewer-{index}",
            "reviewer_role": "reviewer",
            "reviewed_at": reviewed_at,
            "decision": "confirm",
            "inferred_class": "URMIND_ROAD_D40",
            "evidence": {"capture_ids": [features["event"]["capture_id"]]},
        }
        for index in range(2)
    ]
    assessment = SimpleNamespace(
        id=assessment_id,
        factors={
            "phase4_snapshot": {
                "assessment_id": str(assessment_id),
                "collected_at": collected_at.isoformat(),
                "features": features,
            }
        },
    )
    review = SimpleNamespace(
        id=review_id,
        reviewer="synthetic-reviewer-1",
        event_id=uuid.UUID(event_id),
        created_at=reviewed_at,
        decision="confirm",
    )
    decisions = SimpleNamespace(
        dataset_candidates=AsyncMock(return_value=votes),
        snapshot_before_review=AsyncMock(return_value=assessment),
        persisted_review=AsyncMock(return_value=review),
    )
    document = await CoreService(SimpleNamespace(), SimpleNamespace(), decisions).tabular_ground_truth()
    checked = validate_ground_truth_export(document)
    dataset = document["dataset"]
    assert dataset["name"] == f"review-export-{checked['content_sha256'][:12]}"
    assert dataset["feature_schema_sha256"] == feature_schema_sha256()
    assert dataset["label_schema_sha256"] == label_schema_sha256()
    assert dataset["target_version"] == "urmind-review-v1"
    assert dataset["split"]["status"] == "NOT_GENERATED"
    assert checked["readiness"]["status"] == "BLOCKED_DATA"


def test_metrics_calibration_and_baseline_are_defined_only_with_data() -> None:
    assert binary_metrics([], [])["brier"] is None
    metrics = binary_metrics([1, 0, 1, 1], [0.9, 0.2, 0.6, 0.4])
    assert metrics["accuracy"] == 0.75
    bins = reliability_bins([1, 0], [0.95, 0.05], bins=10)
    assert bins[9]["count"] == 1 and bins[0]["count"] == 1 and bins[5]["count"] == 0
    assert bins[5]["observed_rate"] is None
    baseline = prior_baseline([_example(1), _example(0), _example(1), _example(1)])
    assert baseline(_example(0)) == 0.75


def test_promotion_gate_requires_every_piece_of_evidence() -> None:
    assert promotion_gate({})["promotable"] is False
    partial = promotion_gate({"held_out_split": True, "beats_prior_baseline": True})
    assert partial["runtime_allowed"] is False
    complete = promotion_gate(
        {
            "held_out_split": True,
            "beats_prior_baseline": True,
            "calibration_reviewed": True,
            "leakage_audit_passed": True,
            "human_approval": True,
        }
    )
    assert complete["promotable"] is True


def test_target_protocol_keeps_confirmation_distinct_from_risk():
    from app.ml.tabular import labeling_protocol

    protocol = labeling_protocol()
    assert len(protocol["targets"]) == 5
    for name in ("severity", "risk", "priority", "recurrence"):
        assert "review_confirmed" in protocol["targets"][name]["forbidden_label_sources"]
        assert protocol["targets"][name]["training_authorized"] is False
    assert protocol["form"]["snapshot_sha256"] is None


# ---------------------------------------------------------------- authorized run
# Fixture signal only (confidence_max shifted by target); never evidence for a model.


def _fixture_export(n: int = 240) -> dict:
    import random
    from dataclasses import asdict

    from app.ml.tabular import PINNED_FEATURE_SCHEMA, VISUAL_LINEAGE_VERSION

    rng = random.Random(3)
    contract_sha = "a" * 64
    checkpoint = "b" * 64
    rows = []
    for i in range(n):
        target = i % 2
        occurred = T0 + timedelta(days=i / 2)
        collected = occurred + timedelta(minutes=5)
        # One detection, so mean and max confidence are the same value.
        confidence = min(0.99, max(0.01, rng.gauss(0.75 if target else 0.45, 0.1)))
        lineage = {
            "source": "model_versions.metrics.serving@assessment",
            "available_at": occurred.isoformat(),
            "latest_detection_at": occurred.isoformat(),
            "model_version_id": "detector-v1",
            "checkpoint_sha256": checkpoint,
            "class_order": ["URMIND_ROAD_D40"],
            "feature_version": VISUAL_LINEAGE_VERSION,
            "model_contract_sha256": contract_sha,
        }
        snapshot = {
            "feature_schema_version": PINNED_FEATURE_SCHEMA,
            "event": {
                "event_id": f"event-{i}",
                "capture_id": f"capture-{i}",
                "occurred_at": occurred.isoformat(),
            },
            "visual": {
                "detection_count": 1,
                "detection_classes": ["URMIND_ROAD_D40"],
                "detection_confidences": [confidence],
                "bounding_boxes": [{"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.4}],
                "model_version_ids": ["detector-v1"],
                "preprocessing_version": "model-contract-sha256:" + contract_sha,
                "postprocessing_version": "model-contract-sha256:" + contract_sha,
                "checkpoint_sha256": checkpoint,
                "class_order": ["URMIND_ROAD_D40"],
                "feature_version": VISUAL_LINEAGE_VERSION,
            },
            "location": {"road_segment_id": f"seg-{i}", "accuracy_m": None, "snap_distance_m": None},
            "context": {},
            "history": {},
            "missingness": {"history_unavailable": True, "road_segment_missing": False},
            "provenance": {"visual_lineage": lineage, "context": {}},
        }
        review = SimpleNamespace(
            id=f"fixture-review-{i}",
            reviewer="fixture-reviewer",
            event_id=f"event-{i}",
            created_at=occurred + timedelta(days=1),
            decision="confirm" if target else "reject",
        )
        example = build_example(
            snapshot,
            snapshot_collected_at=collected,
            label_at=review.created_at,
            review_confirmed=bool(target),
            review=review,
            snapshot_id=f"assessment-{i}",
            knowledge_cutoff=collected,
            review_status="consensus",
            detector_origin="persisted_detection",
        )
        example = replace(
            example,
            scene_group_id=f"scene-{i}",
            duplicate_group_id=f"dup-{i}",
            sequence_group_id=f"seq-{i}",
            use_authorized=True,
        )
        rows.append(json.loads(json.dumps(asdict(example), default=str)))
    return {"schema": "urmind-ground-truth-export-v1", "training_authorized": False, "rows": rows}


def _fixture_contract(**overrides) -> dict:
    return {
        "validation_from": (T0 + timedelta(days=60)).isoformat(),
        "calibration_from": (T0 + timedelta(days=80)).isoformat(),
        "test_from": (T0 + timedelta(days=100)).isoformat(),
        "seed": 7,
        "calibration_method": "platt",
        "n_estimators": 200,
        "early_stopping_rounds": 10,
        "params": {"max_depth": 2, "learning_rate": 0.1},
        "bootstrap_replicates": 200,
        **overrides,
    }


def _fixture_authorization(export: dict, contract: dict, **overrides) -> dict:
    from app.ml.tabular import dataset_digest, load_training_contract, parse_ground_truth_export

    return {
        "decision": "AUTHORIZED_FOR_TABULAR_TRAINING",
        "target": "review_confirmed",
        "dataset_sha256": dataset_digest(parse_ground_truth_export(export)),
        "contract_sha256": load_training_contract(contract).sha256(),
        "authorized_by": "test-human-authorizer",
        "authorized_at": T0.isoformat(),
        **overrides,
    }


def test_temporal_split_can_hold_a_separate_calibration_cohort() -> None:
    examples = [_example(segment=f"seg-{i}", occurred_at=T0 + timedelta(days=i)) for i in range(40)]
    result = temporal_split(
        examples,
        validation_from=T0 + timedelta(days=10),
        calibration_from=T0 + timedelta(days=20),
        test_from=T0 + timedelta(days=30),
    )
    assert [len(result[name]) for name in ("train", "validation", "calibration", "test")] == [
        9, 9, 9, 10,
    ]
    assert result["dropped_for_label_availability"] == {"train": 1, "validation": 1, "calibration": 1}
    with pytest.raises(TabularExportError, match="crescentes"):
        temporal_split(examples, T0 + timedelta(days=20), T0 + timedelta(days=30),
                       calibration_from=T0 + timedelta(days=10))


def test_contract_refuses_reserved_params_and_unordered_cutoffs() -> None:
    from app.ml.tabular import load_training_contract

    with pytest.raises(TabularExportError, match="reservados"):
        load_training_contract(_fixture_contract(params={"n_estimators": 5000}))
    with pytest.raises(TabularExportError, match="crescentes"):
        load_training_contract(_fixture_contract(test_from=T0.isoformat()))
    with pytest.raises(TabularExportError, match="calibração"):
        load_training_contract(_fixture_contract(calibration_method="beta"))
    assert load_training_contract(_fixture_contract()).sha256() == load_training_contract(
        _fixture_contract()
    ).sha256()


def test_authorization_is_bound_to_dataset_digest_and_contract(tmp_path) -> None:
    from app.ml.tabular import (
        load_authorization,
        load_training_contract,
        parse_ground_truth_export,
        run_tabular_training,
    )

    export, contract = _fixture_export(), _fixture_contract()
    examples = parse_ground_truth_export(export)
    other = load_authorization(_fixture_authorization(export, contract, dataset_sha256="0" * 64))
    assert training_readiness(examples, other)["training_authorized"] is False
    with pytest.raises(TabularExportError, match="BLOCKED_AUTHORIZATION"):
        train_xgboost(examples, 7, authorization=other, contract=load_training_contract(contract))
    authorization = _fixture_authorization(export, contract)
    changed = _fixture_contract(early_stopping_rounds=50)
    with pytest.raises(TabularExportError, match="contrato difere"):
        run_tabular_training(export, changed, authorization, tmp_path / "run")
    with pytest.raises(TabularExportError, match="BLOCKED_AUTHORIZATION"):
        load_authorization({**authorization, "decision": "PENDING"})
    assert not (tmp_path / "run").exists()


def test_authorized_run_early_stops_calibrates_evaluates_and_never_promotes(tmp_path) -> None:
    from app.ml.tabular import run_tabular_training

    export, contract = _fixture_export(), _fixture_contract()
    out = tmp_path / "run"
    report = run_tabular_training(export, contract, _fixture_authorization(export, contract), out)
    assert {p.name for p in out.iterdir()} == {"model.json", "calibration.json", "report.json"}
    assert report["best_iteration"] < contract["n_estimators"]
    assert all(count > 0 for count in report["split_counts"].values())
    assert report["leakage_findings"] == []
    metrics = report["test_metrics"]
    assert metrics["xgboost_calibrated"]["log_loss"] < metrics["prior_baseline"]["log_loss"]
    assert report["evaluation"]["beats_prior_baseline"] is True
    assert report["promotion"]["promotable"] is False
    assert report["runtime_allowed"] is False
    assert set(report["mean_abs_contribution_test"]) == set(FEATURE_COLUMNS)
    with pytest.raises(TabularExportError, match="não sobrescrevo"):
        run_tabular_training(export, contract, _fixture_authorization(export, contract), out)


def test_promoted_model_loader_fails_closed_without_bound_human_record(tmp_path) -> None:
    import hashlib

    from app.ml.tabular import load_promoted_model, run_tabular_training

    export, contract = _fixture_export(), _fixture_contract()
    out = tmp_path / "run"
    run_tabular_training(export, contract, _fixture_authorization(export, contract), out)
    with pytest.raises(TabularExportError, match="promotion.json ausente"):
        load_promoted_model(out)
    record = {
        "report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest(),
        "approved_by": "test-human-approver",
        "approved_at": T0.isoformat(),
        "calibration_reviewed": True,
        "human_approval": True,
    }
    (out / "promotion.json").write_text(json.dumps({**record, "report_sha256": "0" * 64}))
    with pytest.raises(TabularExportError, match="não vinculada"):
        load_promoted_model(out)
    (out / "promotion.json").write_text(json.dumps({**record, "human_approval": False}))
    with pytest.raises(TabularExportError, match="BLOCKED_PROMOTION"):
        load_promoted_model(out)
    (out / "promotion.json").write_text(json.dumps(record))
    model = load_promoted_model(out)
    assert 0.0 <= model.predict_snapshot(_snapshot()) <= 1.0
    (out / "calibration.json").write_text('{"method": "platt", "a": 9, "b": 9}\n')
    with pytest.raises(TabularExportError, match="artefato alterado"):
        load_promoted_model(out)


def test_runtime_without_approved_model_degrades_explicitly(tmp_path) -> None:
    import hashlib

    from app.ml.tabular import predict_review_confirmed, run_tabular_training, tabular_runtime

    snapshot = _snapshot()
    disabled = predict_review_confirmed(tabular_runtime(None), snapshot)
    assert disabled["status"] == "DISABLED" and disabled["probability"] is None
    export, contract = _fixture_export(), _fixture_contract()
    out = tmp_path / "run"
    run_tabular_training(export, contract, _fixture_authorization(export, contract), out)
    unavailable = predict_review_confirmed(tabular_runtime(out), snapshot)
    assert unavailable["status"] == "UNAVAILABLE" and unavailable["probability"] is None
    (out / "promotion.json").write_text(
        json.dumps(
            {
                "report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest(),
                "approved_by": "test-human-approver",
                "approved_at": T0.isoformat(),
                "calibration_reviewed": True,
                "human_approval": True,
            }
        )
    )
    available = predict_review_confirmed(tabular_runtime(out), snapshot)
    assert available["status"] == "AVAILABLE" and 0 <= available["probability"] <= 1
    assert available["advisory_only"] is True


def test_isotonic_calibration_is_refused_on_small_cohorts() -> None:
    from app.ml.tabular import fit_calibrator

    with pytest.raises(TabularExportError, match="platt"):
        fit_calibrator([0, 1] * 100, [0.3, 0.7] * 100, "isotonic")


def test_platt_calibration_fixes_overconfident_scores() -> None:
    from app.ml.tabular import apply_calibrator, fit_calibrator

    y = [1, 0] * 50 + [1] * 20
    p = [0.99 if i % 3 else 0.01 for i in range(len(y))]
    calibrator = fit_calibrator(y, p, "platt")
    before = binary_metrics(y, p)["log_loss"]
    after = binary_metrics(y, apply_calibrator(calibrator, p))["log_loss"]
    assert after < before


def test_ndjson_api_export_is_readable_by_the_cli(tmp_path) -> None:
    from app.ml.tabular import load_ground_truth_export, validate_ground_truth_export

    export = _fixture_export(4)
    path = tmp_path / "urmind-ground-truth.ndjson"
    header = {"schema": export["schema"], "training_authorized": False}
    path.write_text("\n".join(json.dumps(line) for line in [header, *export["rows"]]) + "\n")
    loaded = load_ground_truth_export(path)
    assert len(loaded["rows"]) == 4
    assert len(validate_ground_truth_export(loaded)["dataset_sha256"]) == 64
    path.write_text(json.dumps(header) + "\n")
    assert validate_ground_truth_export(load_ground_truth_export(path))["rows"] == 0
    path.write_text(json.dumps({"rows": []}) + "\n" + json.dumps({"rows": []}) + "\n")
    with pytest.raises(TabularExportError, match="cabeçalho"):
        load_ground_truth_export(path)


@pytest.mark.parametrize(
    "report",
    [
        "not json at all",
        "[]",
        '{"feature_schema_version": null}',
        "__MISSING_WEIGHTS_HASH__",
        "__WRONG_TYPES__",
    ],
)
def test_malformed_report_degrades_to_unavailable_without_raising(tmp_path, report) -> None:
    from app.ml.tabular import (
        FEATURE_COLUMNS,
        PINNED_FEATURE_SCHEMA,
        load_promoted_model,
        tabular_runtime,
    )

    base = {"feature_schema_version": PINNED_FEATURE_SCHEMA, "feature_columns": list(FEATURE_COLUMNS)}
    if report == "__MISSING_WEIGHTS_HASH__":
        report = json.dumps({**base, "artifacts": {}, "evaluation": {}, "best_iteration": 1})
    elif report == "__WRONG_TYPES__":
        report = json.dumps(
            {
                **base,
                "artifacts": ["model.json"],
                "evaluation": "x",
                "best_iteration": None,
            }
        )
    (tmp_path / "report.json").write_text(report, encoding="utf8")
    runtime = tabular_runtime(tmp_path)
    assert runtime["status"] == "UNAVAILABLE" and runtime["model"] is None
    assert runtime["reason"]
    if report.startswith("{") and "artifacts" in report:
        with pytest.raises(TabularExportError, match="model.json e calibration.json"):
            load_promoted_model(tmp_path)
