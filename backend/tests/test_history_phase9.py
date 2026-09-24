from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.history import (
    ANALYTICS_KIND,
    class_frequency,
    describe_segment,
    grid_aggregation,
    hotspot_report,
    prediction_readiness,
    segment_history,
)

AS_OF = datetime(2026, 9, 23, tzinfo=UTC)
COVERAGE_START = AS_OF - timedelta(days=90)


def _row(days_ago: float, segment: str | None = "seg-1", cls: str = "URMIND_ROAD_D40", lat=-23.55):
    return {
        "event_id": f"e-{days_ago}-{segment}-{cls}",
        "road_segment_id": segment,
        "urmind_class": cls,
        "status": "detected",
        "occurred_at": AS_OF - timedelta(days=days_ago),
        "latitude": lat,
        "longitude": -46.63,
    }


def test_segment_history_counts_windows_recurrence_and_recency() -> None:
    rows = [_row(1), _row(10), _row(40, cls="URMIND_ROAD_D00"), _row(5, segment="seg-2")]
    history = {
        s["road_segment_id"]: s for s in segment_history(rows, AS_OF, coverage_start=COVERAGE_START)
    }
    seg = history["seg-1"]
    assert seg["events_last_7d"] == 1
    assert seg["events_last_30d"] == 2
    assert seg["events_last_90d"] == 3
    assert seg["recurrent_classes"] == ["URMIND_ROAD_D40"]
    assert seg["days_since_last_event"] == 1.0
    assert seg["confirmed_events_last_30d"] == 0
    assert (
        describe_segment(seg)
        == "0 eventos confirmados por revisão neste trecho nos últimos 30 dias"
    )


def test_future_rows_never_enter_the_description() -> None:
    rows = [_row(1), _row(-3)]  # the second one is after as_of
    assert segment_history(rows, AS_OF)[0]["total_events"] == 1
    assert class_frequency(rows, AS_OF) == {"URMIND_ROAD_D40": 1}
    assert sum(c["event_count"] for c in grid_aggregation(rows, AS_OF)) == 1


def test_events_without_segment_are_kept_out_of_segment_stats_but_in_the_grid() -> None:
    rows = [_row(1, segment=None)]
    assert segment_history(rows, AS_OF) == []
    assert grid_aggregation(rows, AS_OF)[0]["event_count"] == 1


def test_prediction_is_blocked_without_history_and_never_returns_a_probability() -> None:
    report = hotspot_report([_row(1), _row(2)], AS_OF, reviewed_events=0)
    assert report["analytics_kind"] == ANALYTICS_KIND
    assert report["prediction"]["prediction_status"] == "BLOCKED_HISTORY"
    flat = str(report).lower()
    assert "probab" not in flat.replace("no probability", "")
    assert prediction_readiness([], None)["prediction_status"] == "BLOCKED_HISTORY"


def test_public_sentence_counts_only_human_confirmed_events() -> None:
    confirmed = {**_row(2), "status": "confirmed"}
    unreviewed = {**_row(3), "event_id": "x", "status": "detected"}
    seg = segment_history([confirmed, unreviewed], AS_OF, coverage_start=COVERAGE_START)[0]
    assert seg["events_last_30d"] == 2
    assert seg["confirmed_events_last_30d"] == 1
    assert seg["unreviewed_events"] == 1
    assert (
        describe_segment(seg) == "1 evento confirmado por revisão neste trecho nos últimos 30 dias"
    )


def test_prediction_requirements_are_marked_provisional() -> None:
    report = prediction_readiness([], None)
    assert report["requirements_policy"] == "provisional; not measured thresholds"


@pytest.mark.parametrize("summary", [{}, {"confirmed_events_last_14d": None}])
def test_unavailable_window_is_not_reported_as_zero(summary) -> None:
    text = describe_segment(summary, days=14)
    assert "indisponível" in text
    assert "0 eventos" not in text


@pytest.mark.parametrize("days", [0, -1, True, 1.5])
def test_invalid_history_window_is_rejected(days) -> None:
    with pytest.raises(ValueError, match="window"):
        describe_segment({}, days=days)
    with pytest.raises(ValueError, match="window"):
        segment_history([], AS_OF, windows=(days,))


def test_computed_window_availability_survives_serialization() -> None:
    import json

    summary = json.loads(
        json.dumps(
            segment_history([_row(40)], AS_OF, windows=(14,), coverage_start=COVERAGE_START)[0]
        )
    )
    assert summary["window_availability"] == {"14": "available"}
    assert summary["confirmed_events_last_14d"] == 0
    assert describe_segment(summary, days=14).startswith("0 eventos")
    assert "indisponível" in describe_segment(summary, days=30)


def test_retrospective_report_never_claims_knowledge_at_occurrence_cutoff() -> None:
    report = hotspot_report([_row(3)], AS_OF)
    assert report["query_mode"] == "RETROSPECTIVE_ANALYTICS"
    assert report["knowledge_cutoff"] is None
    assert report["scientific_feature_eligible"] is False


def test_point_in_time_cannot_be_requested_from_current_status_rows() -> None:
    with pytest.raises(ValueError, match="immutable"):
        hotspot_report([_row(3)], AS_OF, query_mode="POINT_IN_TIME_ANALYTICS")


def test_partial_query_coverage_never_claims_a_complete_longer_window() -> None:
    report = hotspot_report([_row(3)], AS_OF, coverage_start=AS_OF - timedelta(days=7))
    segment = report["segments"][0]
    assert segment["window_availability"]["7"] == "available"
    assert segment["window_availability"]["30"] == "insufficient_history"
    assert segment["confirmed_events_last_30d"] is None
    assert segment["events_last_90d"] is None
    assert "indisponível" in describe_segment(segment)


@pytest.mark.parametrize("days", [7, 30, 90])
def test_unknown_coverage_preserves_observations_not_complete_counts(days) -> None:
    import json

    row = {**_row(1), "status": "confirmed"}
    report = json.loads(json.dumps(hotspot_report([row], AS_OF)))
    segment = report["segments"][0]
    assert report["coverage_start"] is None
    assert segment["window_availability"][str(days)] == "unknown"
    assert segment[f"events_last_{days}d"] is None
    assert segment[f"confirmed_events_last_{days}d"] is None
    assert segment["observed_window_counts"][f"events_last_{days}d"] == 1
    assert segment["observed_window_counts"][f"confirmed_events_last_{days}d"] == 1
    assert "indisponível" in describe_segment(segment, days)


@pytest.mark.parametrize("days", [7, 30, 90])
def test_complete_coverage_zero_is_not_missing(days) -> None:
    segment = segment_history([_row(100)], AS_OF, coverage_start=AS_OF - timedelta(days=120))[0]
    assert segment["window_availability"][str(days)] == "available"
    assert segment[f"events_last_{days}d"] == 0
    assert segment[f"confirmed_events_last_{days}d"] == 0
    assert describe_segment(segment, days).startswith("0 eventos")


def test_partial_counts_are_separate_from_complete_counts() -> None:
    segment = segment_history([_row(1)], AS_OF, coverage_start=AS_OF - timedelta(days=7))[0]
    assert segment["events_last_7d"] == 1
    assert segment["events_last_30d"] is None
    assert segment["observed_window_counts"]["events_last_30d"] == 1


def test_public_text_does_not_trust_count_without_coverage() -> None:
    assert "indisponível" in describe_segment({"confirmed_events_last_30d": 1})


def test_future_coverage_start_is_invalid_even_without_rows() -> None:
    with pytest.raises(ValueError, match="coverage"):
        segment_history([], AS_OF, coverage_start=AS_OF + timedelta(days=1))


def test_pit_report_uses_archived_rows_not_new_review_or_backdated_event() -> None:
    from copy import deepcopy

    from app.services.history import snapshot_hash, snapshot_report

    payload = {
        "schema_version": "urmind-history-snapshot-v1",
        "knowledge_cutoff": AS_OF.isoformat(),
        "coverage_start": COVERAGE_START.isoformat(),
        "observations": [{**_row(3), "occurred_at": (AS_OF - timedelta(days=3)).isoformat()}],
    }
    digest = snapshot_hash(payload)
    report = snapshot_report(payload, digest)
    assert report["query_mode"] == "POINT_IN_TIME_ANALYTICS"
    assert report["segments"][0]["confirmed_events_last_7d"] == 0
    changed = deepcopy(payload)
    changed["observations"][0]["status"] = "confirmed"
    with pytest.raises(ValueError, match="hash"):
        snapshot_report(changed, digest)
    changed = deepcopy(payload)
    changed["observations"].append(
        {**_row(2), "occurred_at": (AS_OF - timedelta(days=2)).isoformat()}
    )
    with pytest.raises(ValueError, match="hash"):
        snapshot_report(changed, digest)
    assert snapshot_report(payload, digest) == report


def test_pit_snapshot_cannot_claim_another_cutoff_or_future_rows() -> None:
    from app.services.history import snapshot_hash, snapshot_report

    payload = {
        "schema_version": "urmind-history-snapshot-v1",
        "knowledge_cutoff": AS_OF.isoformat(),
        "coverage_start": COVERAGE_START.isoformat(),
        "observations": [{**_row(-1), "occurred_at": (AS_OF + timedelta(days=1)).isoformat()}],
    }
    with pytest.raises(ValueError, match="window"):
        snapshot_report(payload, snapshot_hash(payload))
