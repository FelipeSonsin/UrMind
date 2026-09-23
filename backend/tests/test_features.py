"""Phase 4 features use only supplied evidence; fixtures are not production data."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Row
from uuid import uuid4

from app.services.features import SCHEMA_VERSION, FeatureInput, build_features
from app.services.risk import FeatureRules, assess_features, replay_features

WHEN = datetime(2026, 9, 1, tzinfo=UTC)


def fixture(*, segment=True, context=True, history=True, detection=True):
    capture_id, detection_id, segment_id = uuid4(), uuid4(), uuid4()
    event = Row(
        id=uuid4(),
        capture_id=capture_id,
        road_segment_id=segment_id if segment else None,
        factors={
            "evidence": {
                "detection_ids": [str(detection_id)] if detection else [],
                "capture_ids": [str(capture_id)],
            }
        },
        occurred_at=WHEN,
        status="detected",
        urmind_class="URMIND_ROAD_D00",
        location_accuracy_m=80.0,
        distance_to_road_m=4.0 if segment else None,
    )
    capture = Row(source="photo", source_location="gps_device", quality={"blur": False})
    detections = [
        Row(
            id=detection_id,
            capture_id=capture_id,
            urmind_class="URMIND_ROAD_D00",
            confidence=0.82,
            model_version_id=uuid4(),
            bbox={"x": 0.1, "width": 0.2},
        )
    ]
    contexts = (
        [
            Row(
                source="overpass_pois",
                ingested_at=WHEN,
                payload={
                    "status": "ok",
                    "fetched_at": WHEN.isoformat(),
                    "provenance": {"provider": "overpass"},
                    "data": {
                        "nearest": {"school": {"distance_m": 42}, "health": None, "crossing": None}
                    },
                },
            ),
            Row(
                source="open_meteo_rain",
                ingested_at=WHEN,
                payload={
                    "status": "context_unavailable",
                    "fetched_at": WHEN.isoformat(),
                    "provenance": {"provider": "open_meteo"},
                    "data": {},
                },
            ),
        ]
        if context
        else []
    )
    return FeatureInput(
        event=event,
        capture=capture,
        detections=detections if detection else [],
        road_segment=Row(highway="residential", jurisdiction=None) if segment else None,
        contexts=contexts,
        previous_event_times=[WHEN - timedelta(days=2), WHEN - timedelta(days=60)]
        if history and segment
        else ([] if segment else None),
        has_original_location=True,
        has_snapped_point=segment,
    )


def test_complete_snapshot_is_reproducible_and_versioned():
    source = fixture()
    first = build_features(source)
    assert first == build_features(source)
    json.dumps(first)
    assert first["feature_schema_version"] == SCHEMA_VERSION
    assert first["visual"]["detection_count"] == 1
    assert first["history"]["previous_events_same_segment"] == 2
    assert first["history"]["recent_events_same_segment_30d"] == 1
    assert first["context"]["near_school"] is True
    assert first["context"]["near_health_unit"] is False
    assert first["context"]["rain_mm_24h"] is None
    assert first["missingness"]["context_unavailable"]["open_meteo_rain"] is True
    assert "severity" not in first
    assert "physical_depth" in first["provenance"]["not_inferred"]


def test_context_temporality_never_relabels_post_event_query_as_event_fact():
    source = fixture()
    source.contexts[0].payload["fetched_at"] = (WHEN + timedelta(days=1)).isoformat()
    source.contexts[0].ingested_at = WHEN + timedelta(days=1)
    features = build_features(source)
    assert (
        features["provenance"]["context"]["overpass_pois"]["temporal_status"]
        == "post_event_context"
    )
    source.contexts[1].payload["data"] = {
        "endpoint": "archive",
        "window_start": (WHEN - timedelta(days=1)).isoformat(),
        "window_end": WHEN.isoformat(),
        "rain_mm_24h": 2.0,
    }
    source.contexts[1].payload["status"] = "ok"
    source.contexts[1].payload["fetched_at"] = (WHEN + timedelta(days=1)).isoformat()
    source.contexts[1].ingested_at = WHEN + timedelta(days=1)
    assert (
        build_features(source)["provenance"]["context"]["open_meteo_rain"]["temporal_status"]
        == "historical_source"
    )


def test_provider_timestamp_cannot_precede_actual_ingestion():
    source = fixture()
    source.contexts[0].payload["fetched_at"] = (WHEN - timedelta(days=1)).isoformat()
    source.contexts[0].ingested_at = WHEN + timedelta(days=1)
    result = build_features(source)
    assert result["provenance"]["context"]["overpass_pois"]["temporal_status"] == (
        "post_event_context"
    )
    source.contexts[0].ingested_at = None
    assert (
        build_features(source)["provenance"]["context"]["overpass_pois"]["temporal_status"]
        == "unknown"
    )


def test_unverifiable_legacy_order_does_not_become_zero_history():
    source = fixture(history=False)
    features = build_features(replace(source, previous_event_times=None))
    assert features["history"]["previous_events_same_segment"] is None
    assert features["history"]["recent_events_same_segment_30d"] is None
    assert features["missingness"]["history_unavailable"] is True


def test_absence_is_not_a_negative_observation():
    output = build_features(fixture(segment=False, context=False, history=False, detection=False))
    assert output["context"]["near_school"] is None
    assert output["history"]["previous_events_same_segment"] is None
    assert output["location"]["road_segment_id"] is None
    assert output["visual"]["detection_count"] == 0
    assert output["missingness"]["history_unavailable"] is True


def test_observed_zero_history_and_low_accuracy_remain_facts():
    source = fixture(history=False)
    output = build_features(source)
    assert output["history"]["previous_events_same_segment"] == 0
    assert output["history"]["has_previous_event_same_segment"] is False
    assert output["location"]["accuracy_m"] == 80.0


def test_unlinked_detection_does_not_enter_event_features():
    source = fixture()
    source.detections[0].capture_id = uuid4()
    output = build_features(source)
    assert output["visual"]["detection_count"] == 0
    assert output["missingness"]["evidence_lineage_incomplete"] is True


def test_extra_capture_cannot_raise_visual_evidence_even_if_listed():
    source = fixture()
    extra_capture_id = uuid4()
    extra = Row(
        id=uuid4(),
        capture_id=extra_capture_id,
        urmind_class="URMIND_ROAD_D00",
        confidence=0.99,
        model_version_id=uuid4(),
        bbox={"x": 0.1, "width": 0.2},
    )
    source.event.factors["evidence"]["capture_ids"].append(str(extra_capture_id))
    source.event.factors["evidence"]["detection_ids"].append(str(extra.id))
    source.detections.append(extra)
    output = build_features(source)
    assert output["visual"]["detection_count"] == 1
    assert output["visual"]["detection_confidences"] == [0.82]
    assert output["missingness"]["non_primary_evidence_excluded"] is True


def test_phase5_separates_impact_severity_risk_and_priority():
    source = fixture()
    source.event.urmind_class = "URMIND_ROAD_D40"
    source.detections[0].urmind_class = "URMIND_ROAD_D40"
    result = assess_features(build_features(source))
    assert result["impact"]["potential_domains"] == ["mobility", "infrastructure"]
    assert result["severity"] == "high"
    assert result["risk"]["ordinal_level"] == "high"
    assert result["priority"]["attention_lane"] == "review_required"  # GPS 80 m
    assert result["ruleset_version"] == "urmind-risk-rules-v1"
    assert result["feature_schema_version"] == SCHEMA_VERSION
    assert result["decision_trace"]["evaluated_rules"]
    assert result["feature_provenance"]["context"]["overpass_pois"]["provenance"] == {
        "provider": "overpass"
    }
    assert result["provider_status"]["open_meteo_rain"] == "context_unavailable"
    assert result == assess_features(build_features(source))
    assert not {"responsible", "action", "prediction"} & result.keys()
    json.dumps(result)


def test_phase5_absence_is_not_zero_or_false():
    result = assess_features(build_features(fixture(segment=False, context=False, history=False)))
    assert result["severity"] == "low"
    assert result["factors_used"]["previous_events_same_segment"] is None
    assert result["factors_used"]["near_school"] is None
    assert "history.previous_events_same_segment" in result["factors_missing"]
    assert "context.sensitive_proximity" in result["factors_missing"]
    assert "context.near_school" in result["factors_missing"]
    assert result["priority"]["attention_lane"] == "review_required"
    assert "location.road_segment_id" in result["factors_missing"]


def test_phase5_low_confidence_suppresses_class_claim():
    source = fixture()
    source.detections[0].confidence = 0.2
    result = assess_features(build_features(source))
    assert result["severity"] == "unknown"
    assert result["impact"]["status"] == "unknown"
    assert result["risk"]["ordinal_level"] == "unknown"
    assert result["uncertainty"]["level"] == "high"


def test_phase5_observed_recurrence_and_ruleset_change_are_traced():
    source = fixture()
    source.event.location_accuracy_m = 8
    features = build_features(source)
    default = assess_features(features)
    changed = assess_features(
        features,
        rules=FeatureRules(version="urmind-risk-rules-experiment", min_visual_confidence=0.9),
    )
    assert default["risk"]["ordinal_level"] == "medium"
    assert default["priority"]["attention_lane"] == "elevated"
    assert changed["severity"] == "unknown"
    assert changed["ruleset_version"] != default["ruleset_version"]
    assert changed["decision_trace"]["provisional_parameters"]["min_visual_confidence"] == 0.9


def test_phase5_rejects_unknown_feature_schema():
    import pytest

    features = build_features(fixture())
    features["feature_schema_version"] = "other"
    with pytest.raises(ValueError, match="unsupported feature schema"):
        assess_features(features)


def test_phase5_requires_new_version_when_rules_change():
    import pytest

    with pytest.raises(ValueError, match="new ruleset version"):
        assess_features(build_features(fixture()), rules=FeatureRules(min_visual_confidence=0.9))


def test_phase5_replay_uses_only_registered_unchanged_ruleset():
    import copy

    import pytest

    features = build_features(fixture())
    original = assess_features(features)
    assert replay_features(features, original) == original

    unknown = copy.deepcopy(original)
    unknown["ruleset_version"] = "urmind-risk-rules-future"
    with pytest.raises(ValueError, match="versão de regras indisponível"):
        replay_features(features, unknown)

    tampered = copy.deepcopy(original)
    tampered["decision_trace"]["provisional_parameters"]["min_visual_confidence"] = 0.9
    with pytest.raises(ValueError, match="parâmetros do ruleset persistido divergentes"):
        replay_features(features, tampered)


def test_phase5_partial_context_remains_partial():
    source = fixture()
    del source.contexts[0].payload["data"]["nearest"]["health"]
    result = assess_features(build_features(source))
    assert "context.near_health_unit" in result["factors_missing"]
    assert result["uncertainty"]["level"] == "high"  # GPS remains imprecise
    assert "context.near_school" not in result["factors_missing"]


def test_phase5_malformed_confidence_and_missing_point_do_not_pass_gates():
    source = fixture()
    source.event.location_accuracy_m = 8
    source.detections[0].confidence = 1.5
    features = build_features(source)
    features["location"]["has_original_location"] = False
    result = assess_features(features)
    assert result["severity"] == "unknown"
    assert result["priority"]["attention_lane"] == "review_required"
    assert "location.original_location" in result["factors_missing"]
    assert "visual.matching_detection_confidence" in result["factors_missing"]
