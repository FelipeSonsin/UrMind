from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.core import (
    BoundingBox,
    CaptureCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EvidenceMode,
    LocationSource,
    UrmindClass,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_bounding_box_rejects_box_outside_frame():
    with pytest.raises(ValidationError):
        BoundingBox(x=0.8, y=0.1, width=0.5, height=0.1)


def test_capture_requires_source_location_with_coordinate():
    with pytest.raises(ValidationError, match="source_location"):
        CaptureCreate(
            capture_key="cap-0001",
            source=CaptureSource.PWA_PHOTO,
            captured_at=NOW,
            coordinate=Coordinate(latitude=-23.55, longitude=-46.63, accuracy_m=8),
        )


def test_capture_rejects_declared_location_without_coordinate():
    with pytest.raises(ValidationError, match="exige coordenada"):
        CaptureCreate(
            capture_key="cap-0002",
            source=CaptureSource.PWA_PHOTO,
            source_location=LocationSource.GPS_DEVICE,
            captured_at=NOW,
        )


def test_capture_accepts_photo_with_gps_and_detection():
    capture = CaptureCreate(
        capture_key="cap-0003",
        source=CaptureSource.PWA_PHOTO,
        source_location=LocationSource.GPS_DEVICE,
        captured_at=NOW,
        coordinate=Coordinate(latitude=-23.55, longitude=-46.63, accuracy_m=8),
        detections=[
            {
                "urmind_class": UrmindClass.ROAD_D40,
                "confidence": 0.91,
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.2},
            }
        ],
    )
    assert capture.detections[0].urmind_class is UrmindClass.ROAD_D40


def test_image_only_event_cannot_carry_coordinate():
    with pytest.raises(ValidationError, match="image_only"):
        EventCreate(
            event_key="evt-0001",
            urmind_class=UrmindClass.ROAD_D40,
            evidence_mode=EvidenceMode.IMAGE_ONLY,
            occurred_at=NOW,
            coordinate=Coordinate(latitude=-23.55, longitude=-46.63),
        )


def test_photo_event_requires_coordinate():
    with pytest.raises(ValidationError, match="exige coordenada"):
        EventCreate(
            event_key="evt-0002",
            urmind_class=UrmindClass.ROAD_D40,
            evidence_mode=EvidenceMode.PHOTO,
            occurred_at=NOW,
        )


def test_sensor_only_event_cannot_claim_visual_confidence():
    with pytest.raises(ValidationError, match="confiança visual"):
        EventCreate(
            event_key="evt-0003",
            urmind_class=UrmindClass.ROAD_D40,
            evidence_mode=EvidenceMode.SENSOR_ONLY,
            occurred_at=NOW,
            coordinate=Coordinate(latitude=-23.55, longitude=-46.63),
            visual_confidence=0.8,
        )
