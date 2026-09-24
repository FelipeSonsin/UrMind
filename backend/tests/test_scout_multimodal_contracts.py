from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.core import CaptureSource, LocationSource
from app.schemas.multimodal import (
    AudioObservation,
    IMUObservation,
    Missingness,
    photo_only_observations,
)
from app.schemas.scout import ClockSource, ScoutPayloadV1, scout_payload_to_capture

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def _payload(**overrides):
    base = {
        "device_id": "SCOUT-01",
        "firmware_version": "0.0.1-dev",
        "capture_id": "f-000123",
        "captured_at": NOW,
        "clock_source": "gateway_receipt",
        "frame": {"width": 640, "height": 480, "bytes": 1000, "sha256": "a" * 64},
        "gnss": {
            "latitude": -23.55,
            "longitude": -46.63,
            "accuracy_m": 4.0,
            "heading_deg": 90,
            "speed_mps": 1.2,
            "fixed_at": NOW,
        },
    }
    return ScoutPayloadV1.model_validate({**base, **overrides})


def test_scout_payload_converges_to_the_canonical_capture_without_detections() -> None:
    payload = _payload()
    capture = scout_payload_to_capture(payload, storage_path="scout/x.jpg")
    assert capture.source is CaptureSource.SCOUT
    assert capture.source_location is LocationSource.GPS_SCOUT
    assert capture.detections == []
    assert capture.capture_key == "scout-SCOUT-01-f-000123"
    assert capture.quality["clock_source"] == "gateway_receipt"
    assert capture.quality["location_attestation"] == "device_claim_unverified"


def test_resent_frame_has_the_same_idempotency_key() -> None:
    assert _payload().idempotency_key == _payload(firmware_version="0.0.2").idempotency_key


@pytest.mark.parametrize(
    "overrides",
    [
        {"captured_at": datetime(2026, 9, 23, 12)},  # noqa: DTZ001 — naive clock on purpose
        {"clock_source": "scout_gnss", "gnss": None},  # GNSS clock without a fix
        {"gnss": {"latitude": 0, "longitude": 0, "accuracy_m": 0, "fixed_at": NOW}},
        {"capture_id": "../../etc"},  # path-like ids refused
        {"payload_version": "scout-payload-v2"},
        {"detections": [{"urmind_class": "URMIND_ROAD_D40"}]},  # device cannot send results
        {
            "imu": {
                "window_start": NOW,
                "window_end": NOW - timedelta(seconds=1),
                "sample_rate_hz": 100,
                "samples": 0,
            }
        },
    ],
)
def test_invalid_scout_payloads_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        _payload(**overrides)


def test_scout_without_gnss_has_no_invented_location() -> None:
    capture = scout_payload_to_capture(_payload(gnss=None))
    assert capture.coordinate is None
    assert capture.source_location is LocationSource.UNKNOWN
    assert ClockSource.GATEWAY_RECEIPT.value == capture.quality["clock_source"]


def test_photo_only_mode_is_a_valid_multimodal_set_with_declared_missingness() -> None:
    obs = photo_only_observations(
        capture_id="c1",
        captured_at=NOW,
        visual_quality={"width": 1000},
        location={"location_source": "gps_device", "accuracy_m": 8.0},
    )
    assert obs.mode == "photo_only"
    assert obs.imu.missingness is Missingness.MISSING and obs.imu.payload == {}
    assert obs.audio.missing_reason
    assert obs.context.missingness is Missingness.UNAVAILABLE


def test_missing_modalities_cannot_carry_values() -> None:
    with pytest.raises(ValidationError):
        IMUObservation(
            source="none", missingness="MISSING", missing_reason="x", payload={"rms": 0.0}
        )
    with pytest.raises(ValidationError):
        IMUObservation(source="none", missingness="MISSING")


def test_uncalibrated_audio_cannot_report_absolute_db_spl() -> None:
    with pytest.raises(ValidationError, match="calibração"):
        AudioObservation(
            source="inmp441", observed_at=NOW, missingness="PRESENT", payload={"db_spl": 80}
        )


def test_sensors_cannot_stand_in_for_missing_visual_evidence() -> None:
    obs = photo_only_observations(
        capture_id="c1", captured_at=NOW, visual_quality={}, location=None
    ).model_dump()
    obs["visual"] = {"source": "none", "missingness": "MISSING", "missing_reason": "sem câmera"}
    obs["imu"] = {"source": "mpu6050", "observed_at": NOW, "missingness": "PRESENT"}
    from app.schemas.multimodal import MultimodalObservationSet

    with pytest.raises(ValidationError, match="visual"):
        MultimodalObservationSet.model_validate(obs)


def test_gnss_fix_after_the_frame_is_rejected_and_fix_age_is_recorded() -> None:
    later_fix = {
        "latitude": -23.55,
        "longitude": -46.63,
        "accuracy_m": 4.0,
        "fixed_at": NOW + timedelta(seconds=5),
    }
    with pytest.raises(ValidationError, match="fix GNSS"):
        _payload(gnss=later_fix)
    stale = {**later_fix, "fixed_at": NOW - timedelta(minutes=10)}
    capture = scout_payload_to_capture(_payload(gnss=stale))
    assert capture.quality["gnss_fix_age_s"] == 600.0
