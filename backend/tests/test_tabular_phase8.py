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
                "entries": [{"event_id": str(identifier)} for identifier in ids],
                "rows": [{"event_id": str(identifier), "target": 1} for identifier in ids],
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
        replace(_example(i % 2, segment=f"seg-{i}"), review_id=f"fixture-review-{i}")
        for i in range(200)
    ]
    assert training_readiness(examples)["status"] == "READY"
    assert training_readiness(examples)["training_authorized"] is False
    with pytest.raises(TabularExportError, match="BLOCKED_AUTHORIZATION"):
        train_xgboost(examples, seed=7)


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
    assert first.feature_schema_version == "urmind-features-v1"
    assert first.readiness["status"] == "BLOCKED_DATA"


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
