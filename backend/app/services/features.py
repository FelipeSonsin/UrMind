"""Canonical, decision-free event features from persisted evidence (Phase 4)."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

SCHEMA_VERSION = "urmind-features-v1"
VISUAL_LINEAGE_VERSION = "urmind-visual-lineage-v1"
RECENT_WINDOW_DAYS = 30
SEVERITY_DETECTION_SCHEMA_VERSION = "urmind-pavement-visual-severity-v1"
SEVERITY_DETECTION_FEATURE_ORDER = (
    "detected_class_D20",
    "detected_class_D40",
    "bbox_width_ratio",
    "bbox_height_ratio",
    "bbox_area_ratio",
    "bbox_center_y_ratio",
    "crop_brightness_mean",
    "crop_brightness_std",
    "crop_dark_fraction",
    "crop_edge_mean",
    "crop_color_range_mean",
)


def severity_detection_schema_sha256() -> str:
    """Identity of the shared per-detection visual severity feature contract."""
    payload = [SEVERITY_DETECTION_SCHEMA_VERSION, SEVERITY_DETECTION_FEATURE_ORDER, 32]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def build_severity_detection_features(
    urmind_class: str, bbox: dict[str, float], image: Any
) -> dict[str, float]:
    """Extract only image/box facts available both in Attain and detector inference.

    ``image`` must be in the same oriented pixel frame as ``bbox``. The label,
    reviewer, source filename, detector confidence and post-review context never
    enter this function. It predicts visual pavement distress level only.
    """
    if urmind_class not in {"URMIND_ROAD_D20", "URMIND_ROAD_D40"}:
        raise ValueError("visual severity unsupported for this detector class")
    if not isinstance(bbox, dict) or set(bbox) != {"x", "y", "width", "height"}:
        raise ValueError("visual severity bbox contract invalid")
    if any(
        type(bbox[key]) not in {int, float} or not math.isfinite(bbox[key])
        for key in ("x", "y", "width", "height")
    ):
        raise ValueError("visual severity bbox must be finite numeric")
    x, y, width, height = (float(bbox[key]) for key in ("x", "y", "width", "height"))
    if not (
        0 <= x < 1
        and 0 <= y < 1
        and 0 < width <= 1
        and 0 < height <= 1
        and x + width <= 1.000001
        and y + height <= 1.000001
    ):
        raise ValueError("visual severity bbox outside frame")
    from PIL import Image  # Imported only when this optional model path is used.

    if not isinstance(image, Image.Image) or image.width <= 0 or image.height <= 0:
        raise ValueError("visual severity requires a decoded image")
    left = max(0, min(image.width - 1, int(x * image.width)))
    top = max(0, min(image.height - 1, int(y * image.height)))
    right = max(left + 1, min(image.width, math.ceil((x + width) * image.width)))
    bottom = max(top + 1, min(image.height, math.ceil((y + height) * image.height)))
    crop = image.crop((left, top, right, bottom)).convert("RGB").resize(
        (32, 32), Image.Resampling.BILINEAR
    )
    pixels = list(crop.getdata())
    luma = [(0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255 for red, green, blue in pixels]
    mean = sum(luma) / len(luma)
    variance = sum((value - mean) ** 2 for value in luma) / len(luma)
    edges = 0.0
    for row in range(32):
        for column in range(32):
            index = row * 32 + column
            if column < 31:
                edges += abs(luma[index] - luma[index + 1])
            if row < 31:
                edges += abs(luma[index] - luma[index + 32])
    values = {
        "detected_class_D20": float(urmind_class == "URMIND_ROAD_D20"),
        "detected_class_D40": float(urmind_class == "URMIND_ROAD_D40"),
        "bbox_width_ratio": width,
        "bbox_height_ratio": height,
        "bbox_area_ratio": width * height,
        "bbox_center_y_ratio": y + height / 2,
        "crop_brightness_mean": mean,
        "crop_brightness_std": math.sqrt(variance),
        "crop_dark_fraction": sum(value < 0.25 for value in luma) / len(luma),
        "crop_edge_mean": edges / (2 * 32 * 31),
        "crop_color_range_mean": sum(max(pixel) - min(pixel) for pixel in pixels)
        / (len(pixels) * 255),
    }
    return {name: values[name] for name in SEVERITY_DETECTION_FEATURE_ORDER}


@dataclass(frozen=True)
class FeatureInput:
    """Already loaded records; callers must scope history to this event's segment and time."""

    event: Any
    capture: Any | None = None
    detections: Sequence[Any] = ()
    road_segment: Any | None = None
    contexts: Sequence[Any] = ()
    previous_event_times: Sequence[datetime] | None = None
    history_status: str | None = None
    has_original_location: bool | None = None
    has_snapped_point: bool | None = None
    vision_lineage: dict[str, Any] | None = None


def _aware_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _temporal_status(
    source: str, payload: dict[str, Any], occurred_at: datetime, ingested_at: Any
) -> str:
    """Describe source time, not truth of the external observation."""
    data = payload.get("data") or {}
    if source == "open_meteo_rain" and data.get("endpoint") == "archive":
        start = _aware_timestamp(data.get("window_start"))
        end = _aware_timestamp(data.get("window_end"))
        if start is not None and end is not None and start <= end <= occurred_at:
            return "historical_source"
    ingested = _aware_timestamp(ingested_at)
    provider_fetched = _aware_timestamp(payload.get("fetched_at"))
    if ingested is None or provider_fetched is None:
        return "unknown"
    return (
        "available_at_event"
        if ingested <= occurred_at and provider_fetched <= occurred_at
        else "post_event_context"
    )


def build_features(source: FeatureInput) -> dict[str, Any]:
    """Return JSON-compatible facts, derivations, missingness and field provenance.

    No network access, clock reads, database reads, model-specific thresholds or risk decisions.
    ``None`` means unknown/unavailable; ``False`` and zero are used only for observed negatives.
    """
    event = source.event
    evidence = (event.factors or {}).get("evidence", {})
    linked_ids = set(evidence.get("detection_ids") or ())
    primary_capture_id = str(event.capture_id) if event.capture_id else None
    declared_capture_ids = set(evidence.get("capture_ids") or ())
    detections = [
        detection
        for detection in source.detections
        if str(detection.id) in linked_ids
        and primary_capture_id is not None
        and primary_capture_id in declared_capture_ids
        and str(detection.capture_id) == primary_capture_id
    ]
    detections.sort(key=lambda detection: str(detection.id))
    context_rows = {row.source: row for row in source.contexts}
    contexts = {name: row.payload for name, row in context_rows.items()}
    pois = contexts.get("overpass_pois")
    rain = contexts.get("open_meteo_rain")
    poi_data = pois.get("data", {}) if pois and pois.get("status") == "ok" else None
    rain_data = rain.get("data", {}) if rain and rain.get("status") == "ok" else None
    nearest = (poi_data or {}).get("nearest") or {}

    def nearby(category: str) -> bool | None:
        if poi_data is None or category not in nearest:
            return None
        return nearest[category] is not None

    history = source.previous_event_times
    if history is not None:
        history = [when for when in history if when < event.occurred_at]
    recent = (
        sum(when >= event.occurred_at - timedelta(days=RECENT_WINDOW_DAYS) for when in history)
        if history is not None
        else None
    )
    visual = {
        "detection_count": len(detections),
        "detection_classes": [d.urmind_class for d in detections],
        "detection_confidences": [d.confidence for d in detections],
        "detection_ids": [str(d.id) for d in detections],
        "model_version_ids": [
            str(d.model_version_id) if d.model_version_id else None for d in detections
        ],
        "bounding_boxes": [d.bbox for d in detections],
        "capture_quality": source.capture.quality if source.capture else None,
        "preprocessing_version": (source.vision_lineage or {}).get("preprocessing_version"),
        "postprocessing_version": (source.vision_lineage or {}).get("postprocessing_version"),
        "checkpoint_sha256": (source.vision_lineage or {}).get("checkpoint_sha256"),
        "class_order": (source.vision_lineage or {}).get("class_order"),
        "feature_version": VISUAL_LINEAGE_VERSION,
        "model_contract_sha256": (source.vision_lineage or {}).get("model_contract_sha256"),
    }
    location = {
        "has_original_location": source.has_original_location,
        "has_snapped_point": source.has_snapped_point,
        "accuracy_m": event.location_accuracy_m,
        "location_source": source.capture.source_location if source.capture else None,
        "road_segment_id": str(event.road_segment_id) if event.road_segment_id else None,
        "snap_distance_m": event.distance_to_road_m,
        "road_highway": source.road_segment.highway if source.road_segment else None,
        "road_jurisdiction": source.road_segment.jurisdiction if source.road_segment else None,
    }
    event_features = {
        "event_id": str(event.id),
        "event_status": event.status,
        "event_class": event.urmind_class,
        "source_type": source.capture.source if source.capture else None,
        "occurred_at": event.occurred_at.isoformat(),
        "capture_id": str(event.capture_id) if event.capture_id else None,
        "evidence_count": len(linked_ids),
    }
    context = {
        "near_school": nearby("school"),
        "near_health_unit": nearby("health"),
        "crossing_nearby": nearby("crossing"),
        "rain_mm_24h": rain_data.get("rain_mm_24h") if rain_data else None,
        "reverse_geocoding_available": contexts.get("nominatim_reverse", {}).get("status") == "ok"
        if "nominatim_reverse" in contexts
        else None,
        "provider_status": {
            name: payload.get("status") for name, payload in sorted(contexts.items())
        },
    }
    history_features = {
        "previous_events_same_segment": len(history) if history is not None else None,
        "recent_events_same_segment_30d": recent,
        "has_previous_event_same_segment": bool(history) if history is not None else None,
        "order_status": source.history_status or ("observed" if history is not None else "unknown"),
    }
    missing = {
        "detection_missing": not detections,
        "evidence_lineage_incomplete": bool(linked_ids)
        and (primary_capture_id not in declared_capture_ids or not detections),
        "non_primary_evidence_excluded": len(detections) < len(linked_ids),
        "original_location_unknown": source.has_original_location is None,
        "road_segment_missing": source.road_segment is None,
        "accuracy_missing": event.location_accuracy_m is None,
        "history_unavailable": history is None,
        "context_unavailable": {
            name: payload.get("status") != "ok" for name, payload in sorted(contexts.items())
        },
        "poi_context_missing": poi_data is None,
        "rain_context_missing": rain_data is None,
    }
    return {
        "feature_schema_version": SCHEMA_VERSION,
        "visual": visual,
        "location": location,
        "event": event_features,
        "context": context,
        "history": history_features,
        "missingness": missing,
        "provenance": {
            "visual": "detections linked to the event's primary Capture; capture.quality; "
            "other deduplicated Captures do not determine visual severity",
            "visual_lineage": (
                (source.vision_lineage or {}) | {"feature_version": VISUAL_LINEAGE_VERSION}
            ),
            "location": "event location and snap; capture.source_location; road_segments",
            "event": "events; capture.source",
            "context": {
                name: {
                    "fetched_at": payload.get("fetched_at"),
                    "ingested_at": (
                        context_rows[name].ingested_at.isoformat()
                        if getattr(context_rows[name], "ingested_at", None) is not None
                        else None
                    ),
                    "provenance": payload.get("provenance"),
                    "temporal_status": _temporal_status(
                        name,
                        payload,
                        event.occurred_at,
                        getattr(context_rows[name], "ingested_at", None),
                    ),
                }
                for name, payload in sorted(contexts.items())
            },
            "history": "visible events on same road_segment_id with earlier occurred_at "
            "and serialized commit order; legacy order is unavailable",
            "transformations": {
                "recent_events_same_segment_30d": "count in [occurred_at - 30 days, occurred_at)"
            },
            "missing_policy": "None=unknown/unavailable; zero and False require observed data",
            "not_inferred": [
                "physical_depth",
                "physical_size",
                "severity",
                "risk",
                "priority",
                "action",
                "responsible_party",
                "prediction",
            ],
        },
    }
