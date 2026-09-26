"""Phase 4 features use only supplied evidence; fixtures are not production data."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.core import CoreService
from app.services.features import (
    SCHEMA_VERSION,
    VISUAL_LINEAGE_VERSION,
    FeatureInput,
    build_features,
)
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


@pytest.mark.asyncio
async def test_assessment_snapshot_freezes_serving_lineage_without_loading_detector():
    from app.ml.tabular import flatten_snapshot

    source = fixture(context=False, history=False)
    model_id = uuid4()
    source.event.model_version_id = model_id
    source.detections[0].model_version_id = model_id
    source.detections[0].created_at = WHEN
    source.detections[0].bbox = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    model = Row(
        metrics={
            "serving": {
                "checkpoint_sha256": "b" * 64,
                "class_names": ["URMIND_ROAD_D00"],
                "model_contract_sha256": "a" * 64,
            }
        },
        operational_status="EXPERIMENTAL_SHADOW",
    )
    captures = Row(get=AsyncMock(return_value=source.capture))
    events = Row(
        get=AsyncMock(return_value=source.event),
        segment=AsyncMock(return_value=source.road_segment),
        coordinates=AsyncMock(return_value={"latitude": 1.0, "snapped_latitude": 1.0}),
        contexts=AsyncMock(return_value=[]),
        previous_event_times=AsyncMock(return_value=(None, "unknown")),
        evidence_detections=AsyncMock(return_value=source.detections),
    )
    decisions = Row(model_version=AsyncMock(return_value=model))
    service = CoreService(captures, events, decisions)

    features, _ = await service._feature_snapshot(source.event.id)
    assert features["visual"]["checkpoint_sha256"] == "b" * 64
    assert features["visual"]["class_order"] == ["URMIND_ROAD_D00"]
    assert features["visual"]["feature_version"] == VISUAL_LINEAGE_VERSION
    assert features["provenance"]["visual_lineage"]["model_status"] == "EXPERIMENTAL_SHADOW"
    assert flatten_snapshot(features, knowledge_cutoff=datetime.now(UTC))

    source.detections[0].model_version_id = uuid4()
    unbound, _ = await service._feature_snapshot(source.event.id)
    assert unbound["visual"]["checkpoint_sha256"] is None

    source.detections[0].model_version_id = model_id
    source.detections[0].created_at = None
    without_detection_time, _ = await service._feature_snapshot(source.event.id)
    assert without_detection_time["visual"]["checkpoint_sha256"] is None


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


@pytest.mark.asyncio
async def test_assessment_persists_phase7_trace_with_same_snapshot_and_lineage(monkeypatch):
    source = fixture(segment=False, context=False, history=False)
    event = source.event
    event.model_version_id = source.detections[0].model_version_id
    features = build_features(source)
    events = Row(
        get=AsyncMock(return_value=event),
        set_status=AsyncMock(),
    )
    persisted_rows = []

    async def add_risk(event_id, payload, **refs):
        persisted_rows.append((event_id, payload, refs))
        return Row(
            id=refs["id"],
            severity=payload["severity"],
            priority_score=payload["priority_score"],
            uncertainty=payload["uncertainty"],
        )

    decisions = Row(
        latest_risk=AsyncMock(return_value=None),
        dataset_version_for_model=AsyncMock(return_value=uuid4()),
        responsibility=AsyncMock(return_value=None),
        action=AsyncMock(return_value=None),
        add_risk=add_risk,
    )
    service = CoreService(Row(), events, decisions)
    monkeypatch.setattr(service, "_feature_snapshot", AsyncMock(return_value=(features, [])))

    result = await service.assess_event(event.id)

    stored = persisted_rows[0][1]["factors"]
    trace = stored["decision_trace"]
    assert trace["trace_schema_version"] == "urmind-decision-trace-v2"
    assert trace["severity_source"] == "rules"
    assert trace["risk_source"] == "rules"
    assert trace["priority_source"] == "rules"
    assert trace["assessment_id"] == str(result["assessment_id"])
    assert trace["event_id"] == str(event.id)
    assert trace["feature_schema_version"] == stored["phase4_snapshot"]["feature_schema_version"]
    assert trace["ruleset_version"] == stored["phase5"]["ruleset_version"]
    assert trace["model_version_id"] == str(event.model_version_id)
    assert trace["dataset_version_id"] is not None
    assert trace["factors_missing"] == stored["phase5"]["factors_missing"]
    assert trace["evaluated_rules"] == stored["phase5"]["decision_trace"]["evaluated_rules"]
    assert trace["priority"] != trace["risk"]
    assert trace["context_snapshot_key"] == "phase4_snapshot.context_records"


async def _assess_with_tabular_runtime(monkeypatch, runtime):
    source = fixture(segment=False, context=False, history=False)
    event = source.event
    event.model_version_id = source.detections[0].model_version_id
    features = build_features(source)
    persisted_rows = []

    async def add_risk(event_id, payload, **refs):
        persisted_rows.append(payload)
        return Row(
            id=refs["id"],
            severity=payload["severity"],
            priority_score=payload["priority_score"],
            uncertainty=payload["uncertainty"],
        )

    decisions = Row(
        latest_risk=AsyncMock(return_value=None),
        dataset_version_for_model=AsyncMock(return_value=None),
        responsibility=AsyncMock(return_value=None),
        action=AsyncMock(return_value=None),
        add_risk=add_risk,
    )
    events = Row(get=AsyncMock(return_value=event), set_status=AsyncMock())
    service = CoreService(Row(), events, decisions)
    monkeypatch.setattr(service, "_feature_snapshot", AsyncMock(return_value=(features, [])))
    if runtime is not None:
        monkeypatch.setattr("app.services.core.configured_tabular_runtime", lambda: runtime)
    result = await service.assess_event(event.id)
    return result, persisted_rows[0]


def _decisions(payload):
    trace = payload["factors"]["decision_trace"]
    return (
        payload["severity"],
        trace["risk"],
        trace["priority"],
        trace["severity_source"],
        trace["risk_source"],
        trace["priority_source"],
        trace["action"],
        trace["responsibility"],
    )


@pytest.mark.asyncio
async def test_review_confirmed_advisory_is_disabled_without_promoted_model(monkeypatch):
    monkeypatch.delenv("TABULAR_MODEL_DIR", raising=False)
    from app.config import get_settings
    from app.ml import tabular

    get_settings.cache_clear()
    tabular._runtime_for.cache_clear()
    _, payload = await _assess_with_tabular_runtime(monkeypatch, None)
    advisory = payload["factors"]["decision_trace"]["review_confirmed_advisory"]
    assert advisory == {
        "target": "review_confirmed",
        "status": "DISABLED",
        "probability": None,
        "reason": "nenhum modelo tabular configurado",
        "advisory_only": True,
    }


@pytest.mark.asyncio
async def test_review_confirmed_advisory_is_recorded_but_never_changes_decisions(monkeypatch):
    class Model:
        report_sha256 = "c" * 64

        def predict_snapshot(self, snapshot):
            assert snapshot["feature_schema_version"]
            return 0.93

    available = {"status": "AVAILABLE", "reason": None, "model": Model()}
    disabled = {"status": "DISABLED", "reason": "nenhum modelo tabular configurado", "model": None}
    _, with_model = await _assess_with_tabular_runtime(monkeypatch, available)
    _, without = await _assess_with_tabular_runtime(monkeypatch, disabled)
    advisory = with_model["factors"]["decision_trace"]["review_confirmed_advisory"]
    assert advisory["status"] == "AVAILABLE"
    assert advisory["probability"] == 0.93
    assert advisory["advisory_only"] is True
    assert advisory["report_sha256"] == "c" * 64
    assert _decisions(with_model) == _decisions(without)


@pytest.mark.asyncio
async def test_review_confirmed_advisory_failure_does_not_block_assessment(monkeypatch):
    class Broken:
        report_sha256 = "d" * 64

        def predict_snapshot(self, snapshot):
            raise ValueError("schema drift")

    result, payload = await _assess_with_tabular_runtime(
        monkeypatch, {"status": "AVAILABLE", "reason": None, "model": Broken()}
    )
    advisory = payload["factors"]["decision_trace"]["review_confirmed_advisory"]
    assert advisory["status"] == "ERROR"
    assert advisory["probability"] is None
    assert "ValueError" in advisory["reason"]
    assert result["assessment_id"]
