"""Fase 8 — pipeline tabular (snapshot imutável → DatasetVersion → baseline → XGBoost).

Nada aqui entra no runtime. O Worker continua decidindo por regras
(`assess_features`); este módulo leva um export point-in-time até um modelo
avaliado: contrato pré-registrado, autorização humana vinculada ao hash do
dataset, early stopping em VALIDATION, calibração em coorte própria, teste
final contra o prior e artefatos com hash. `load_promoted_model` só carrega um
modelo com registro humano de promoção; ninguém o chama no Worker ainda.

Proteções contra vazamento, cada uma com teste:

- **Allowlist de features.** Só campos declarados em `FEATURE_COLUMNS` saem do
  snapshot. `event_status`, ids, revisões, adjudicação, severidade/prioridade e
  qualquer campo de decisão ficam fora por construção.
- **Tempo do rótulo.** Um exemplo só entra se o snapshot foi coletado antes do
  rótulo humano (`snapshot_collected_at < label_at`).
- **Contexto pós-evento.** Contexto marcado `post_event_context` é tratado como
  ausente, não como feature.
- **Grupos.** RoadSegment, Capture e Event nunca atravessam splits: a chave de
  grupo é a componente conexa sobre segmento e capturas.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.ml.splits import DatasetSplit, SplitRatios, find_leakage, split_by_group
from app.services.features import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from app.services.features import (
    SEVERITY_DETECTION_FEATURE_ORDER,
    SEVERITY_DETECTION_SCHEMA_VERSION,
    VISUAL_LINEAGE_VERSION,
    build_severity_detection_features,
    severity_detection_schema_sha256,
)

TABULAR_SCHEMA_VERSION = "urmind-tabular-v1"
TABULAR_EXTRACTOR_VERSION = "urmind-tabular-extractor-v4"
# The export is pinned: a snapshot built by another FeatureBuilder version is refused.
PINNED_FEATURE_SCHEMA = FEATURE_SCHEMA_VERSION
TARGET_NAME = "review_confirmed"
EXTERNAL_SEVERITY_TARGET = "pavement_visual_severity_low_high"
EXTERNAL_SEVERITY_TARGET_VERSION = "attain-nykrzdm74f-v1-low-high"
EXTERNAL_SEVERITY_SOURCE_VERSION = "mendeley-nykrzdm74f-v1"
EXTERNAL_LABEL_MAPPING_STATUS = "SYNTACTIC_ONLY_RUBRIC_UNVERIFIED"
EXTERNAL_SIMILARITY_METHOD = "dhash64-pillow-bilinear-v1"
EXTERNAL_SIMILARITY_MAX_DISTANCE = 12
EXTERNAL_ANNOTATION_PROTOCOL_BY_SUBSET = {
    "ws_v1": "YOLO_TXT_NORMALIZED_BOX_OR_POLYGON",
    "ws_v2": "PASCAL_VOC_XML_PIXEL_BOX",
}
YOLOX_EXPOSURES = {
    "YOLOX_TRAIN_EXPOSED",
    "YOLOX_VALIDATION_EXPOSED",
    "YOLOX_OTHER_EXPOSED",
    "YOLOX_NOT_EXPOSED_VERIFIED",
    "YOLOX_EXPOSURE_UNKNOWN",
}

# Provisional data-sufficiency policy (not a scientific threshold): below this
# the training status is BLOCKED_DATA instead of a number with no support.
MIN_LABELED_EXAMPLES = 200
MIN_EXAMPLES_PER_TARGET_VALUE = 50
MIN_INDEPENDENT_GROUPS = 10


# Structured reason recorded by the review UI at the start of `notes`. Only a
# visual rejection is a negative label; duplicates, missing location or an
# inconclusive image are review outcomes, not evidence that the occurrence is false.
REJECTION_REASONS = {
    "erro_visual": "a imagem não mostra o dano proposto",
    "duplicidade": "ocorrência duplicada (administrativo)",
    "localizacao": "localização ausente ou incorreta",
    "imagem_inconclusiva": "imagem insuficiente para decidir",
}
NEGATIVE_REJECTION_REASON = "erro_visual"


def rejection_reason(notes: str | None) -> str | None:
    """Reason code from `[motivo:<code>]` at the start of the review notes."""
    if not notes or not notes.startswith("[motivo:"):
        return None
    code = notes[len("[motivo:") :].split("]", 1)[0]
    return code if code in REJECTION_REASONS else None


def _get(snapshot: dict[str, Any], path: str) -> Any:
    value: Any = snapshot
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _timestamp(value: Any) -> datetime:
    """Accept the in-memory exporter and its JSON representation identically."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError("timestamp must be datetime or ISO string")


def _bool(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise TabularExportError("boolean feature has invalid type")
    return float(value)


def _not_bool(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise TabularExportError("boolean feature has invalid type")
    return float(not value)


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float} or not math.isfinite(value):
        raise TabularExportError("numeric feature has invalid type")
    return float(value)


def _count(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise TabularExportError("count feature has invalid type")
    return float(value)


def _mean(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise TabularExportError("confidence feature has invalid type")
    return float(sum(values) / len(values))


def _max(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise TabularExportError("confidence feature has invalid type")
    return float(max(values))


_VISUAL_CLASSES = (
    "URMIND_ROAD_D00",
    "URMIND_ROAD_D10",
    "URMIND_ROAD_D20",
    "URMIND_ROAD_D40",
)


def _class_flag(values: Any, expected: str) -> float | None:
    if values is None:
        return None
    if not isinstance(values, list) or any(value not in _VISUAL_CLASSES for value in values):
        raise TabularExportError("visual class outside pinned detector contract")
    return float(expected in values)


def _bbox_area_ratio_max(values: Any) -> float | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise TabularExportError("visual bbox list invalid")
    if not values:
        return None
    areas = []
    for box in values:
        if not isinstance(box, dict) or any(
            type(box.get(name)) not in {int, float} or not math.isfinite(box[name])
            for name in ("x", "y", "width", "height")
        ):
            raise TabularExportError("visual bbox invalid")
        if not (
            0 <= box["x"] < 1
            and 0 <= box["y"] < 1
            and 0 < box["width"] <= 1
            and 0 < box["height"] <= 1
            and box["x"] + box["width"] <= 1.0001
            and box["y"] + box["height"] <= 1.0001
        ):
            raise TabularExportError("visual bbox outside frame")
        areas.append(float(box["width"] * box["height"]))
    return max(areas)


# column -> (snapshot path, transform). Allowlist: nothing else leaves the snapshot.
FEATURE_COLUMNS: dict[str, tuple[str, Callable[[Any], float | None]]] = {
    "detection_count": ("visual.detection_count", _count),
    "confidence_mean": ("visual.detection_confidences", _mean),
    "confidence_max": ("visual.detection_confidences", _max),
    "detected_class_D00": (
        "visual.detection_classes",
        lambda values: _class_flag(values, "URMIND_ROAD_D00"),
    ),
    "detected_class_D10": (
        "visual.detection_classes",
        lambda values: _class_flag(values, "URMIND_ROAD_D10"),
    ),
    "detected_class_D20": (
        "visual.detection_classes",
        lambda values: _class_flag(values, "URMIND_ROAD_D20"),
    ),
    "detected_class_D40": (
        "visual.detection_classes",
        lambda values: _class_flag(values, "URMIND_ROAD_D40"),
    ),
    "bbox_area_ratio_max": ("visual.bounding_boxes", _bbox_area_ratio_max),
    "accuracy_m": ("location.accuracy_m", _numeric),
    "snap_distance_m": ("location.snap_distance_m", _numeric),
    "has_road_segment": ("missingness.road_segment_missing", _not_bool),
    "near_school": ("context.near_school", _bool),
    "near_health_unit": ("context.near_health_unit", _bool),
    "crossing_nearby": ("context.crossing_nearby", _bool),
    "rain_mm_24h": ("context.rain_mm_24h", _numeric),
    "previous_events_same_segment": (
        "history.previous_events_same_segment",
        _count,
    ),
    "recent_events_same_segment_30d": (
        "history.recent_events_same_segment_30d",
        _count,
    ),
}

# Context columns and the provider whose temporal status gates them.
_CONTEXT_PROVIDER = {
    "near_school": "overpass_pois",
    "near_health_unit": "overpass_pois",
    "crossing_nearby": "overpass_pois",
    "rain_mm_24h": "open_meteo_rain",
}

_BINARY_FEATURES = frozenset(
    {
        "detected_class_D00",
        "detected_class_D10",
        "detected_class_D20",
        "detected_class_D40",
        "has_road_segment",
        "near_school",
        "near_health_unit",
        "crossing_nearby",
    }
)
_COUNT_FEATURES = frozenset(
    {"detection_count", "previous_events_same_segment", "recent_events_same_segment_30d"}
)
_UNIT_FEATURES = frozenset({"confidence_mean", "confidence_max", "bbox_area_ratio_max"})
_NONNEGATIVE_FEATURES = frozenset({"accuracy_m", "snap_distance_m", "rain_mm_24h"})


def _validate_feature_values(features: dict[str, float | None]) -> None:
    for name, value in features.items():
        if value is None:
            continue
        if type(value) not in {int, float} or not math.isfinite(value):
            raise TabularExportError("nonfinite or nonnumeric feature")
        if name in _BINARY_FEATURES and value not in {0, 1}:
            raise TabularExportError(f"binary feature {name} outside 0/1")
        if name in _COUNT_FEATURES and (value < 0 or not float(value).is_integer()):
            raise TabularExportError(f"count feature {name} invalid")
        if name in _UNIT_FEATURES and not 0 <= value <= 1:
            raise TabularExportError(f"unit feature {name} outside 0..1")
        if name in _NONNEGATIVE_FEATURES and value < 0:
            raise TabularExportError(f"nonnegative feature {name} invalid")
    if (
        features["confidence_mean"] is not None
        and features["confidence_max"] is not None
        and features["confidence_mean"] > features["confidence_max"]
    ):
        raise TabularExportError("visual confidence mean exceeds maximum")


# Fields that must never become features (documented and asserted in tests).
FORBIDDEN_FEATURE_PATHS = frozenset(
    {
        "event.event_status",
        "event.event_id",
        "event.capture_id",
        "event.occurred_at",
        "visual.detection_ids",
        "visual.model_version_ids",
        "location.road_segment_id",
        "severity",
        "priority",
        "review",
        "adjudication",
    }
)


class TabularExportError(ValueError):
    """Snapshot incompatible with the pinned schema or with the leakage rules."""


@dataclass(frozen=True)
class TabularExample:
    event_id: str
    capture_ids: tuple[str, ...]
    road_segment_id: str | None
    occurred_at: datetime
    snapshot_collected_at: datetime
    label_at: datetime
    target: int
    features: dict[str, float | None]
    review_id: str | None = None
    detector_versions: tuple[str, ...] = ()
    snapshot_sha256: str = ""
    snapshot_id: str = ""
    feature_schema_version: str = PINNED_FEATURE_SCHEMA
    target_name: str = TARGET_NAME
    target_version: str = "urmind-review-v1"
    review_status: str = ""
    knowledge_cutoff: datetime | None = None
    scene_group_id: str | None = None
    duplicate_group_id: str | None = None
    sequence_group_id: str | None = None
    detector_origin: str | None = None
    use_authorized: bool = False
    yolox_exposure: str = "YOLOX_EXPOSURE_UNKNOWN"
    available_at: datetime | None = None
    feature_provenance: dict[str, Any] = field(default_factory=dict)
    missingness: dict[str, Any] = field(default_factory=dict)
    visual_preprocessing_version: str | None = None
    visual_postprocessing_version: str | None = None
    vision_checkpoint_sha256: str | None = None
    vision_class_order: tuple[str, ...] = ()
    vision_feature_version: str | None = None


def _verified_history_timing(history: dict[str, Any], cutoff: datetime, count: Any) -> bool:
    if history.get("order_status") not in {"observed", "serialized_commit_order"} or (
        history.get("coverage_status") != "VERIFIED"
    ):
        return False
    try:
        window_end = datetime.fromisoformat(history["window_end_at"])
        available_at = datetime.fromisoformat(history["available_at"])
        latest_at = (
            datetime.fromisoformat(history["last_event_at"])
            if history.get("last_event_at")
            else None
        )
    except (KeyError, TypeError, ValueError):
        return False
    if any(
        stamp.tzinfo is None or stamp.utcoffset() is None
        for stamp in (window_end, available_at, latest_at)
        if stamp is not None
    ):
        return False
    if (
        window_end > cutoff
        or available_at > cutoff
        or (latest_at is not None and latest_at >= window_end)
    ):
        raise TabularExportError("FEATURE_BLOCKED_FUTURE: history after prediction cutoff")
    return not count or latest_at is not None


def _verified_visual_timing(
    detector_versions: Sequence[str],
    checkpoint_sha256: str | None,
    class_order: Sequence[str],
    feature_version: str | None,
    preprocessing_version: str | None,
    postprocessing_version: str | None,
    provenance: Any,
    cutoff: datetime,
) -> bool:
    """Require frozen serving lineage only when all visual metadata is present."""
    if not all(
        (
            detector_versions,
            checkpoint_sha256,
            class_order,
            feature_version,
            preprocessing_version,
            postprocessing_version,
        )
    ):
        return False
    if not isinstance(provenance, dict):
        raise TabularExportError("visual lineage provenance missing")
    contract = provenance.get("model_contract_sha256")
    version = f"model-contract-sha256:{contract}"
    if (
        provenance.get("source") != "model_versions.metrics.serving@assessment"
        or len(detector_versions) != 1
        or provenance.get("model_version_id") != detector_versions[0]
        or provenance.get("checkpoint_sha256") != checkpoint_sha256
        or tuple(provenance.get("class_order") or ()) != tuple(class_order)
        or provenance.get("feature_version") != feature_version
        or feature_version != VISUAL_LINEAGE_VERSION
        or preprocessing_version != version
        or postprocessing_version != version
        or not isinstance(contract, str)
        or len(contract) != 64
        or any(char not in "0123456789abcdef" for char in contract)
    ):
        raise TabularExportError("visual lineage does not bind serving contract")
    try:
        available = _timestamp(provenance["available_at"])
        detection = _timestamp(provenance["latest_detection_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TabularExportError("visual lineage timestamps missing") from exc
    if any(stamp.tzinfo is None or stamp.utcoffset() is None for stamp in (available, detection)):
        raise TabularExportError("visual lineage timestamps require timezone")
    if detection > available or available > cutoff:
        raise TabularExportError("FEATURE_BLOCKED_FUTURE: visual detection after cutoff")
    return True


def flatten_snapshot(
    snapshot: dict[str, Any], *, knowledge_cutoff: datetime | None = None
) -> dict[str, float | None]:
    if (
        snapshot.get("query_mode") == "RETROSPECTIVE_ANALYTICS"
        or _get(snapshot, "history.query_mode") == "RETROSPECTIVE_ANALYTICS"
    ):
        raise TabularExportError("retrospective analytics cannot supply scientific features")
    if snapshot.get("feature_schema_version") != PINNED_FEATURE_SCHEMA:
        raise TabularExportError(
            f"feature schema {snapshot.get('feature_schema_version')!r} != {PINNED_FEATURE_SCHEMA}"
        )
    context_provenance = _get(snapshot, "provenance.context")
    if context_provenance is not None and not isinstance(context_provenance, dict):
        raise TabularExportError("context provenance invalid")
    temporal = {
        name: info if isinstance(info, dict) else {}
        for name, info in (context_provenance or {}).items()
    }
    occurred_at = datetime.fromisoformat(_get(snapshot, "event.occurred_at"))
    cutoff = knowledge_cutoff or occurred_at
    _verified_visual_timing(
        tuple(_get(snapshot, "visual.model_version_ids") or ()),
        _get(snapshot, "visual.checkpoint_sha256"),
        tuple(_get(snapshot, "visual.class_order") or ()),
        _get(snapshot, "visual.feature_version"),
        _get(snapshot, "visual.preprocessing_version"),
        _get(snapshot, "visual.postprocessing_version"),
        _get(snapshot, "provenance.visual_lineage"),
        cutoff,
    )
    history = snapshot.get("history") or {}
    if not isinstance(history, dict):
        raise TabularExportError("history provenance invalid")
    history_verified = _get(
        snapshot, "missingness.history_unavailable"
    ) is False and _verified_history_timing(
        history, cutoff, history.get("previous_events_same_segment")
    )
    row: dict[str, float | None] = {}
    for column, (path, transform) in FEATURE_COLUMNS.items():
        if column.startswith(("previous_events_", "recent_events_")) and (not history_verified):
            row[column] = None
            continue
        provider = _CONTEXT_PROVIDER.get(column)
        if provider:
            info = temporal.get(provider, {})
            status = info.get("temporal_status")
            try:
                available = all(
                    datetime.fromisoformat(info[key]) <= cutoff
                    for key in ("fetched_at", "ingested_at")
                )
            except (KeyError, TypeError, ValueError):
                available = False
            if status not in {"available_at_event", "historical_source"} or not available:
                row[column] = None
                continue
        row[column] = transform(_get(snapshot, path))
    visual_count = row["detection_count"]
    for path in (
        "visual.detection_classes",
        "visual.detection_confidences",
        "visual.bounding_boxes",
    ):
        values = _get(snapshot, path)
        if isinstance(values, list) and visual_count is not None and len(values) != visual_count:
            raise TabularExportError("visual detection arrays differ from count")
    _validate_feature_values(row)
    return row


def build_example(
    snapshot: dict[str, Any],
    *,
    snapshot_collected_at: datetime,
    label_at: datetime,
    review_confirmed: bool,
    capture_ids: Sequence[str] = (),
    review: Any | None = None,
    snapshot_id: str = "",
    knowledge_cutoff: datetime | None = None,
    review_status: str = "",
    detector_origin: str | None = None,
) -> TabularExample:
    """Build a row; only a matching persisted Review establishes label provenance.

    Bare Boolean labels are retained for offline scaffolding, but cannot pass
    readiness. `correct` reviews have no binary mapping and are refused.
    """
    if type(review_confirmed) is not bool:
        raise TabularExportError("review_confirmed deve ser booleano")
    if snapshot_collected_at >= label_at:
        raise TabularExportError("snapshot coletado depois do rótulo: vazamento do target")
    if knowledge_cutoff is not None and snapshot_collected_at > knowledge_cutoff:
        raise TabularExportError("snapshot posterior ao cutoff de decisão")
    event = snapshot.get("event") or {}
    occurred_at = datetime.fromisoformat(event["occurred_at"])
    if occurred_at > snapshot_collected_at:
        raise TabularExportError("snapshot anterior ao próprio evento")
    review_id = None
    if review is not None:
        if (
            not getattr(review, "id", None)
            or not getattr(review, "reviewer", None)
            or str(getattr(review, "event_id", "")) != str(event["event_id"])
            or getattr(review, "created_at", None) != label_at
            or getattr(review, "decision", None) != ("confirm" if review_confirmed else "reject")
        ):
            raise TabularExportError("Review ausente, divergente ou sem rótulo binário elegível")
        review_id = str(review.id)
    # Deduplicated events carry every contributing Capture (factors.evidence).
    primary = event.get("capture_id")
    captures = {str(c) for c in capture_ids} | ({str(primary)} if primary else set())
    return TabularExample(
        event_id=str(event["event_id"]),
        capture_ids=tuple(sorted(captures)),
        road_segment_id=_get(snapshot, "location.road_segment_id"),
        occurred_at=occurred_at,
        snapshot_collected_at=snapshot_collected_at,
        label_at=label_at,
        target=int(review_confirmed),
        features=flatten_snapshot(
            snapshot, knowledge_cutoff=knowledge_cutoff or snapshot_collected_at
        ),
        review_id=review_id,
        detector_versions=tuple(
            sorted(
                str(value) for value in (_get(snapshot, "visual.model_version_ids") or []) if value
            )
        ),
        snapshot_sha256=hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, default=str).encode()
        ).hexdigest(),
        snapshot_id=snapshot_id,
        knowledge_cutoff=knowledge_cutoff,
        review_status=review_status,
        detector_origin=detector_origin,
        available_at=snapshot_collected_at,
        feature_provenance={
            **(snapshot.get("provenance") or {}),
            "history_timing": {
                key: _get(snapshot, f"history.{key}")
                for key in (
                    "order_status",
                    "coverage_status",
                    "window_end_at",
                    "available_at",
                    "last_event_at",
                    "previous_events_same_segment",
                )
            },
        },
        missingness={
            **(snapshot.get("missingness") or {}),
            "history_coverage_unknown": _get(snapshot, "history.coverage_status") != "VERIFIED",
        },
        visual_preprocessing_version=_get(snapshot, "visual.preprocessing_version"),
        visual_postprocessing_version=_get(snapshot, "visual.postprocessing_version"),
        vision_checkpoint_sha256=_get(snapshot, "visual.checkpoint_sha256"),
        vision_class_order=tuple(_get(snapshot, "visual.class_order") or ()),
        vision_feature_version=_get(snapshot, "visual.feature_version"),
    )


def leakage_groups(examples: Sequence[Any]) -> dict[str, str]:
    """event_id -> group key: connected components over segment, capture and event ids."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        roots = sorted((find(a), find(b)))
        parent[roots[1]] = roots[0]

    for example in examples:
        root = f"event:{example.event_id}"
        find(root)
        if example.road_segment_id:
            union(root, f"segment:{example.road_segment_id}")
        for capture in example.capture_ids:
            union(root, f"capture:{capture}")
        for name in ("scene_group_id", "duplicate_group_id", "sequence_group_id"):
            value = getattr(example, name)
            if value and value != "UNCONFIRMED":
                union(root, f"{name}:{value}")
    return {e.event_id: find(f"event:{e.event_id}") for e in examples}


def group_split(
    examples: Sequence[TabularExample], ratios: SplitRatios | None = None, seed: int = 20260923
) -> DatasetSplit:
    groups = leakage_groups(examples)
    ordered = sorted(examples, key=lambda e: (groups[e.event_id], e.event_id))
    split = split_by_group(ordered, lambda e: groups[e.event_id], ratios, seed)
    if find_leakage(split):
        raise TabularExportError("grupo atravessou splits")
    return split


def temporal_split(
    examples: Sequence[TabularExample],
    validation_from: datetime,
    test_from: datetime,
    *,
    calibration_from: datetime | None = None,
) -> dict[str, Any]:
    """Time-ordered split; later rows sharing a group with earlier splits are dropped, not kept.

    With `calibration_from`, a separate calibration cohort sits between the
    early-stopping validation and the final test, so neither is reused.
    """
    boundaries = [("train", validation_from), ("validation", test_from)]
    if calibration_from is not None:
        boundaries = [
            ("train", validation_from),
            ("validation", calibration_from),
            ("calibration", test_from),
        ]
    cutoffs = [cutoff for _, cutoff in boundaries]
    if any(not earlier < later for earlier, later in pairwise(cutoffs)):
        raise TabularExportError("cortes temporais precisam ser estritamente crescentes")
    names = [name for name, _ in boundaries] + ["test"]
    groups = leakage_groups(examples)
    parts: dict[str, list[TabularExample]] = {name: [] for name in names}
    unavailable_labels = {name: 0 for name, _ in boundaries}
    for example in sorted(examples, key=lambda e: (e.occurred_at, e.event_id)):
        name, cutoff = next(
            ((name, cutoff) for name, cutoff in boundaries if example.occurred_at < cutoff),
            ("test", None),
        )
        # A label is usable only if it was known before the next cohort starts.
        if cutoff is not None and example.label_at >= cutoff:
            unavailable_labels[name] += 1
        else:
            parts[name].append(example)
    dropped: dict[str, int] = {}
    seen = {groups[e.event_id] for e in parts["train"]}
    for name in names[1:]:
        kept = [e for e in parts[name] if groups[e.event_id] not in seen]
        dropped[name] = len(parts[name]) - len(kept)
        parts[name] = kept
        seen |= {groups[e.event_id] for e in kept}
    return {
        **parts,
        "dropped_for_group_leakage": dropped,
        "dropped_for_label_availability": unavailable_labels,
        "validation_from": validation_from.isoformat(),
        **({"calibration_from": calibration_from.isoformat()} if calibration_from else {}),
        "test_from": test_from.isoformat(),
        "method": "temporal_with_group_isolation",
    }


def training_readiness(
    examples: Sequence[TabularExample], authorization: TabularAuthorization | None = None
) -> dict[str, Any]:
    counts = Counter(e.target for e in examples)
    groups = len(set(leakage_groups(examples).values()))
    blockers = []
    if any(not e.review_id for e in examples):
        blockers.append("rótulos sem vínculo com Review persistida")
    if any(not e.capture_ids for e in examples):
        blockers.append("linhagem de Capture ausente")
    if any(not e.detector_versions for e in examples):
        blockers.append("versão do detector operacional ausente")
    if any(len(set(e.detector_versions)) != 1 for e in examples):
        blockers.append("múltiplas versões visuais na mesma linha")
    if any(not e.snapshot_sha256 for e in examples):
        blockers.append("hash do snapshot point-in-time ausente")
    if any(
        not e.snapshot_id or e.feature_schema_version != PINNED_FEATURE_SCHEMA for e in examples
    ):
        blockers.append("identidade ou versão do snapshot ausente")
    if any(e.review_status not in {"consensus", "adjudicated"} for e in examples):
        blockers.append("revisão sem consenso ou adjudicação")
    if any(e.knowledge_cutoff is None or e.available_at is None for e in examples):
        blockers.append("cutoff ou disponibilidade das features ausente")
    if any(
        not value or value == "UNCONFIRMED"
        for e in examples
        for value in (e.scene_group_id, e.duplicate_group_id, e.sequence_group_id)
    ):
        blockers.append("grupos de cena, duplicata ou sequência não verificados")
    if any(e.detector_origin != "persisted_detection" for e in examples):
        blockers.append("origem do detector não verificada")
    if any(
        not e.visual_preprocessing_version or not e.visual_postprocessing_version for e in examples
    ):
        blockers.append("versões de preprocessamento/postprocessamento visual ausentes")
    if any(
        not e.vision_checkpoint_sha256 or not e.vision_class_order or not e.vision_feature_version
        for e in examples
    ):
        blockers.append("checkpoint, ordem de classes ou versão de features visuais ausente")
    if any(
        any(
            e.features[name] is None
            for name in _BINARY_FEATURES
            if name.startswith("detected_class_")
        )
        or (e.features["detection_count"] and e.features["bbox_area_ratio_max"] is None)
        for e in examples
    ):
        blockers.append("classe ou área de bbox visual ausente")
    visual_lineages = {
        (
            e.detector_versions,
            e.vision_checkpoint_sha256,
            e.vision_class_order,
            e.vision_feature_version,
            e.visual_preprocessing_version,
            e.visual_postprocessing_version,
        )
        for e in examples
    }
    if len(visual_lineages) > 1:
        blockers.append("versões visuais misturadas no DatasetVersion")
    if any(e.use_authorized is not True for e in examples):
        blockers.append("autorização de uso por linha ausente")
    if len({e.event_id for e in examples}) != len(examples):
        blockers.append("Event duplicado no dataset")
    if len(examples) < MIN_LABELED_EXAMPLES:
        blockers.append(f"exemplos rotulados {len(examples)} < {MIN_LABELED_EXAMPLES}")
    for value in (0, 1):
        if counts.get(value, 0) < MIN_EXAMPLES_PER_TARGET_VALUE:
            blockers.append(
                f"{TARGET_NAME}={value}: {counts.get(value, 0)} < {MIN_EXAMPLES_PER_TARGET_VALUE}"
            )
    if groups < MIN_INDEPENDENT_GROUPS:
        blockers.append(f"grupos independentes {groups} < {MIN_INDEPENDENT_GROUPS}")
    digest = dataset_digest(examples)
    # Only a human authorization bound to this exact content digest opens training.
    authorized = (
        not blockers
        and authorization is not None
        and authorization.target == TARGET_NAME
        and authorization.dataset_sha256 == digest
    )
    return {
        "status": "READY" if not blockers else "BLOCKED_DATA",
        "labeled_examples": len(examples),
        "target_counts": {str(k): v for k, v in sorted(counts.items())},
        "independent_groups": groups,
        "dataset_sha256": digest,
        "blockers": blockers,
        "policy": "provisional data-sufficiency floor; not a performance threshold",
        "training_authorized": authorized,
        "authorization_note": (
            f"authorized by {authorization.authorized_by} at {authorization.authorized_at}"
            if authorized and authorization
            else "data sufficiency is not dataset-bound training authorization"
        ),
    }


def load_ground_truth_export(path: Path) -> dict[str, Any]:
    """Read the API export: NDJSON (header line + one row per line) or a JSON document."""
    text = path.read_text(encoding="utf8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not lines or lines[0].get("schema") != "urmind-ground-truth-export-v1":
            raise TabularExportError("export NDJSON sem cabeçalho urmind-ground-truth-export-v1") from None
        return {**lines[0], "rows": lines[1:]}
    if not isinstance(document, dict):
        raise TabularExportError("export must be an object")
    if document.get("schema") == "urmind-ground-truth-export-v1" and "rows" not in document:
        return {**document, "rows": []}  # NDJSON with only the header: zero eligible rows
    return document


def parse_ground_truth_export(document: dict[str, Any]) -> list[TabularExample]:
    """Rebuild examples from CoreService's point-in-time export, refusing any broken lineage,
    future-dated context/history, unverified visual lineage or mixed target versions."""
    if document.get("training_authorized") is not False:
        raise TabularExportError("export cannot authorize training")
    raw_rows = document.get("rows")
    if not isinstance(raw_rows, list):
        raise TabularExportError("rows must be a list")
    examples = []
    seen_events: set[str] = set()
    target_versions: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, dict):
            raise TabularExportError("row must be an object")
        if not isinstance(row.get("capture_ids"), (list, tuple)) or not isinstance(
            row.get("detector_versions"), (list, tuple)
        ):
            raise TabularExportError("Capture and detector lineage must be lists")
        if any(
            not isinstance(value, str) or not value.strip()
            for values in (row["capture_ids"], row["detector_versions"])
            for value in values
        ):
            raise TabularExportError("invalid Capture or detector lineage")
        if not isinstance(row.get("event_id"), str) or not row["event_id"].strip():
            raise TabularExportError("Event lineage missing")
        if not isinstance(row.get("features"), dict) or tuple(row["features"]) != tuple(
            FEATURE_COLUMNS
        ):
            raise TabularExportError("feature allowlist mismatch")
        features = row["features"]
        _validate_feature_values(features)
        try:
            example = TabularExample(
                event_id=str(row["event_id"]),
                capture_ids=tuple(row["capture_ids"]),
                road_segment_id=row.get("road_segment_id"),
                occurred_at=_timestamp(row["occurred_at"]),
                snapshot_collected_at=_timestamp(row["snapshot_collected_at"]),
                label_at=_timestamp(row["label_at"]),
                target=row["target"],
                features=features,
                review_id=row.get("review_id"),
                detector_versions=tuple(row.get("detector_versions") or ()),
                snapshot_sha256=row.get("snapshot_sha256") or "",
                snapshot_id=row.get("snapshot_id") or "",
                feature_schema_version=row.get("feature_schema_version") or "",
                target_name=row.get("target_name") or "",
                target_version=row.get("target_version") or "",
                review_status=row.get("review_status") or "",
                knowledge_cutoff=(
                    _timestamp(row["knowledge_cutoff"]) if row.get("knowledge_cutoff") else None
                ),
                scene_group_id=row.get("scene_group_id"),
                duplicate_group_id=row.get("duplicate_group_id"),
                sequence_group_id=row.get("sequence_group_id"),
                detector_origin=row.get("detector_origin"),
                use_authorized=row.get("use_authorized", False),
                yolox_exposure=row.get("yolox_exposure", "YOLOX_EXPOSURE_UNKNOWN"),
                available_at=(_timestamp(row["available_at"]) if row.get("available_at") else None),
                feature_provenance=row.get("feature_provenance") or {},
                missingness=row.get("missingness") or {},
                visual_preprocessing_version=row.get("visual_preprocessing_version"),
                visual_postprocessing_version=row.get("visual_postprocessing_version"),
                vision_checkpoint_sha256=row.get("vision_checkpoint_sha256"),
                vision_class_order=tuple(row.get("vision_class_order") or ()),
                vision_feature_version=row.get("vision_feature_version"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TabularExportError("invalid point-in-time row") from exc
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (
                example.occurred_at,
                example.snapshot_collected_at,
                example.label_at,
                example.knowledge_cutoff,
                example.available_at,
            )
            if value is not None
        ):
            raise TabularExportError("point-in-time timestamps require timezone")
        if type(example.target) is not int or example.target not in {0, 1}:
            raise TabularExportError("review_confirmed target must be binary")
        if not (
            example.occurred_at
            <= example.snapshot_collected_at
            <= (example.knowledge_cutoff or example.snapshot_collected_at)
            < example.label_at
        ):
            raise TabularExportError("snapshot/label temporal leakage")
        if example.available_at is None or example.available_at > (
            example.knowledge_cutoff or example.snapshot_collected_at
        ):
            raise TabularExportError("feature availability after decision cutoff")
        if not isinstance(example.feature_provenance, dict) or not isinstance(
            example.missingness, dict
        ):
            raise TabularExportError("feature provenance or missingness invalid")
        for column, provider in _CONTEXT_PROVIDER.items():
            if example.features[column] is None:
                continue
            context_info = example.feature_provenance.get("context")
            if not isinstance(context_info, dict):
                raise TabularExportError("context provenance invalid")
            info = context_info.get(provider)
            if not isinstance(info, dict):
                raise TabularExportError("context provenance invalid")
            try:
                available_in_time = all(
                    datetime.fromisoformat(info[name])
                    <= (example.knowledge_cutoff or example.snapshot_collected_at)
                    for name in ("fetched_at", "ingested_at")
                )
            except (KeyError, TypeError, ValueError):
                available_in_time = False
            if (
                info.get("temporal_status") not in {"available_at_event", "historical_source"}
                or not available_in_time
            ):
                raise TabularExportError("future context feature in export")
        if example.missingness.get("history_coverage_unknown") is not False and any(
            example.features[name] is not None
            for name in ("previous_events_same_segment", "recent_events_same_segment_30d")
        ):
            raise TabularExportError("history feature without verified coverage")
        if any(
            example.features[name] is not None
            for name in ("previous_events_same_segment", "recent_events_same_segment_30d")
        ):
            timing = example.feature_provenance.get("history_timing")
            if not isinstance(timing, dict) or not _verified_history_timing(
                timing,
                example.knowledge_cutoff or example.snapshot_collected_at,
                example.features["previous_events_same_segment"],
            ):
                raise TabularExportError("history feature without point-in-time proof")
        if example.target_name != TARGET_NAME or not example.target_version:
            raise TabularExportError("target contract mismatch")
        target_versions.add(example.target_version)
        if example.feature_schema_version != PINNED_FEATURE_SCHEMA:
            raise TabularExportError("feature schema mismatch")
        if not example.snapshot_id or example.review_status not in {"consensus", "adjudicated"}:
            raise TabularExportError("snapshot or human review lineage missing")
        if example.detector_origin != "persisted_detection":
            raise TabularExportError("detector provenance missing")
        _verified_visual_timing(
            example.detector_versions,
            example.vision_checkpoint_sha256,
            example.vision_class_order,
            example.vision_feature_version,
            example.visual_preprocessing_version,
            example.visual_postprocessing_version,
            example.feature_provenance.get("visual_lineage"),
            example.knowledge_cutoff or example.snapshot_collected_at,
        )
        checkpoint = example.vision_checkpoint_sha256
        if checkpoint is not None and (
            not isinstance(checkpoint, str)
            or len(checkpoint) != 64
            or any(char not in "0123456789abcdef" for char in checkpoint)
        ):
            raise TabularExportError("vision checkpoint SHA-256 invalid")
        if not all(isinstance(name, str) and name.strip() for name in example.vision_class_order):
            raise TabularExportError("vision class order invalid")
        if type(example.use_authorized) is not bool:
            raise TabularExportError("invalid use authorization")
        if example.event_id in seen_events:
            raise TabularExportError("duplicate Event in export")
        seen_events.add(example.event_id)
        if not example.review_id or not example.capture_ids:
            raise TabularExportError("Review/Capture lineage missing")
        if len(example.snapshot_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in example.snapshot_sha256
        ):
            raise TabularExportError("snapshot SHA-256 lineage missing")
        examples.append(example)
    if len(target_versions) > 1:
        raise TabularExportError("mixed target versions")
    return examples


def validate_ground_truth_export(document: dict[str, Any]) -> dict[str, Any]:
    """Validate CoreService's point-in-time export without fitting a model."""
    examples = parse_ground_truth_export(document)
    target_versions = {example.target_version for example in examples}
    readiness = training_readiness(examples)
    digest = hashlib.sha256()
    for row_hash in sorted(_row_hash(example) for example in examples):
        digest.update(row_hash.encode())
    content_sha256 = digest.hexdigest()
    dataset = document.get("dataset") or {}
    if dataset and (
        dataset.get("target") != TARGET_NAME
        or dataset.get("schema_version") != TABULAR_SCHEMA_VERSION
        or dataset.get("feature_schema_version") != PINNED_FEATURE_SCHEMA
        or dataset.get("feature_schema_sha256") != feature_schema_sha256()
        or dataset.get("label_schema_sha256") != label_schema_sha256()
        or dataset.get("target_version") != next(iter(target_versions), None)
        or tuple(dataset.get("feature_columns") or ()) != tuple(FEATURE_COLUMNS)
    ):
        raise TabularExportError("dataset metadata contract mismatch")
    declared = dataset.get("content_sha256")
    if declared is not None and declared != content_sha256:
        raise TabularExportError("dataset content hash mismatch")
    return {
        "target": TARGET_NAME,
        "target_version": next(iter(target_versions), None),
        "rows": len(examples),
        "content_sha256": content_sha256,
        "dataset_sha256": readiness["dataset_sha256"],
        "independent_groups": readiness["independent_groups"],
        "missingness": {
            name: sum(row.features[name] is None for row in examples) for name in FEATURE_COLUMNS
        },
        "detector_lineage_present": sum(bool(row.detector_versions) for row in examples),
        "history_coverage_unknown": sum(
            row.missingness.get("history_coverage_unknown") is True for row in examples
        ),
        "visual_preprocessing_known": sum(
            bool(row.visual_preprocessing_version) for row in examples
        ),
        "visual_postprocessing_known": sum(
            bool(row.visual_postprocessing_version) for row in examples
        ),
        "vision_checkpoint_known": sum(bool(row.vision_checkpoint_sha256) for row in examples),
        "vision_class_order_known": sum(bool(row.vision_class_order) for row in examples),
        "vision_feature_version_known": sum(bool(row.vision_feature_version) for row in examples),
        "readiness": readiness,
        "training_authorized": False,
    }


def _row_hash(example: TabularExample) -> str:
    payload = json.dumps(
        asdict(example),
        sort_keys=True,
        default=lambda value: value.isoformat(),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def dataset_digest(examples: Sequence[TabularExample]) -> str:
    """Order-independent content hash: what a human authorization is bound to."""
    digest = hashlib.sha256()
    for row_hash in sorted(_row_hash(e) for e in examples):
        digest.update(row_hash.encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class TabularDatasetVersion:
    """Immutable description of a tabular dataset, ready for `dataset_versions`."""

    name: str
    schema_version: str
    feature_schema_version: str
    feature_schema_sha256: str
    label_schema_sha256: str
    feature_columns: tuple[str, ...]
    target: str
    target_version: str | None
    split: dict[str, Any]
    content_sha256: str
    readiness: dict[str, Any]
    seed: int
    notes: list[str] = field(default_factory=list)


def build_tabular_dataset_version(
    name: str, examples: Sequence[TabularExample], seed: int = 20260923
) -> TabularDatasetVersion:
    readiness = training_readiness(examples)
    target_versions = {example.target_version for example in examples}
    if len(target_versions) > 1:
        raise TabularExportError("mixed target versions in DatasetVersion")
    digest = hashlib.sha256()
    for row_hash in sorted(_row_hash(e) for e in examples):
        digest.update(row_hash.encode())
    content_sha256 = digest.hexdigest()
    return TabularDatasetVersion(
        name=f"{name}-{content_sha256[:12]}",
        schema_version=TABULAR_SCHEMA_VERSION,
        feature_schema_version=PINNED_FEATURE_SCHEMA,
        feature_schema_sha256=feature_schema_sha256(),
        label_schema_sha256=label_schema_sha256(),
        feature_columns=tuple(FEATURE_COLUMNS),
        target=TARGET_NAME,
        target_version=next(iter(target_versions), None),
        split={"status": "NOT_GENERATED", "reason": "requires verified temporal groups"},
        content_sha256=content_sha256,
        readiness=readiness,
        seed=seed,
    )


# ---------------------------------------------------------------- evaluation


def binary_metrics(y_true: Sequence[int], p: Sequence[float]) -> dict[str, float | None]:
    """Brier, log loss and accuracy@0.5; None when undefined (no invented values)."""
    if not y_true or len(y_true) != len(p):
        return {"n": float(len(y_true)), "brier": None, "log_loss": None, "accuracy": None}
    eps = 1e-12
    brier = sum((pi - yi) ** 2 for yi, pi in zip(y_true, p, strict=True)) / len(p)
    log_loss = -sum(
        yi * math.log(max(pi, eps)) + (1 - yi) * math.log(max(1 - pi, eps))
        for yi, pi in zip(y_true, p, strict=True)
    ) / len(p)
    accuracy = sum(int(pi >= 0.5) == yi for yi, pi in zip(y_true, p, strict=True)) / len(p)
    return {"n": float(len(p)), "brier": brier, "log_loss": log_loss, "accuracy": accuracy}


def reliability_bins(
    y_true: Sequence[int], p: Sequence[float], bins: int = 10
) -> list[dict[str, float | int | None]]:
    out: list[dict[str, float | int | None]] = []
    for index in range(bins):
        lo, hi = index / bins, (index + 1) / bins
        members = [
            (yi, pi)
            for yi, pi in zip(y_true, p, strict=True)
            if lo <= pi < hi or (index == bins - 1 and pi == 1.0)
        ]
        out.append(
            {
                "lower": lo,
                "upper": hi,
                "count": len(members),
                "mean_predicted": sum(pi for _, pi in members) / len(members) if members else None,
                "observed_rate": sum(yi for yi, _ in members) / len(members) if members else None,
            }
        )
    return out


def prior_baseline(train: Sequence[TabularExample]) -> Callable[[TabularExample], float]:
    """The baseline any learned model must beat: the training prior."""
    if not train:
        raise TabularExportError("baseline exige conjunto de treino não vazio")
    rate = sum(e.target for e in train) / len(train)
    return lambda _example: rate


class XGBoostUnavailableError(RuntimeError):
    """Runtime dependencies are absent from the isolated tabular Python."""


@dataclass(frozen=True)
class VerifiedTrainingBundle:
    """Inputs tied to the artifact registry and an exact training approval."""

    export: dict[str, Any]
    split: dict[str, Any]
    config: dict[str, Any]
    artifact_sha256: dict[str, str]
    approval_source: str
    similarity_audit: dict[str, Any] | None = None


AUTHORIZATION_DECISION = "AUTHORIZED_FOR_TABULAR_TRAINING"
# scikit-learn guidance: isotonic calibration tends to overfit below ~1000 samples.
MIN_ISOTONIC_CALIBRATION_EXAMPLES = 1000
CALIBRATION_METHODS = frozenset({"platt", "isotonic"})
# Fixed by the pipeline or by the contract fields; never overridable through `params`.
_RESERVED_PARAMS = frozenset(
    {
        "objective",
        "eval_metric",
        "missing",
        "random_state",
        "seed",
        "n_estimators",
        "early_stopping_rounds",
        "callbacks",
    }
)
_SPLITS = ("train", "validation", "calibration", "test")


def _aware(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise TabularExportError(f"{name} inválido") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TabularExportError(f"{name} exige timezone")
    return parsed


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class TrainingContract:
    """Pre-registered before fitting: cutoffs, budget, patience and calibration method."""

    validation_from: datetime
    calibration_from: datetime
    test_from: datetime
    seed: int
    calibration_method: str
    n_estimators: int
    early_stopping_rounds: int
    params: dict[str, Any] = field(default_factory=dict)
    bootstrap_replicates: int = 1000

    def document(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "validation_from": self.validation_from.isoformat(),
            "calibration_from": self.calibration_from.isoformat(),
            "test_from": self.test_from.isoformat(),
        }

    def sha256(self) -> str:
        return _canonical_sha256(self.document())


def load_training_contract(document: dict[str, Any]) -> TrainingContract:
    try:
        contract = TrainingContract(
            validation_from=_aware(document["validation_from"], "validation_from"),
            calibration_from=_aware(document["calibration_from"], "calibration_from"),
            test_from=_aware(document["test_from"], "test_from"),
            seed=document["seed"],
            calibration_method=document["calibration_method"],
            n_estimators=document["n_estimators"],
            early_stopping_rounds=document["early_stopping_rounds"],
            params=dict(document.get("params") or {}),
            bootstrap_replicates=document.get("bootstrap_replicates", 1000),
        )
    except (KeyError, TypeError) as exc:
        raise TabularExportError(f"contrato de treino incompleto: {exc}") from exc
    if not contract.validation_from < contract.calibration_from < contract.test_from:
        raise TabularExportError("cortes do contrato precisam ser estritamente crescentes")
    if contract.calibration_method not in CALIBRATION_METHODS:
        raise TabularExportError(f"calibração deve ser uma de {sorted(CALIBRATION_METHODS)}")
    for name in ("seed", "n_estimators", "early_stopping_rounds", "bootstrap_replicates"):
        value = getattr(contract, name)
        if type(value) is not int or value < (0 if name == "seed" else 1):
            raise TabularExportError(f"{name} deve ser inteiro válido")
    if reserved := sorted(_RESERVED_PARAMS & set(contract.params)):
        raise TabularExportError(f"params reservados pelo pipeline: {reserved}")
    if any(not isinstance(v, (int, float, str, bool)) for v in contract.params.values()):
        raise TabularExportError("params do contrato devem ser escalares")
    return contract


@dataclass(frozen=True)
class TabularAuthorization:
    """Human decision bound to one dataset digest and one pre-registered contract."""

    target: str
    dataset_sha256: str
    contract_sha256: str
    authorized_by: str
    authorized_at: str


def load_authorization(document: dict[str, Any]) -> TabularAuthorization:
    if document.get("decision") != AUTHORIZATION_DECISION:
        raise TabularExportError(f"BLOCKED_AUTHORIZATION: decision != {AUTHORIZATION_DECISION}")
    if document.get("target") != TARGET_NAME:
        raise TabularExportError("BLOCKED_AUTHORIZATION: alvo divergente")
    if not _is_sha256(document.get("dataset_sha256")) or not _is_sha256(
        document.get("contract_sha256")
    ):
        raise TabularExportError("BLOCKED_AUTHORIZATION: hashes do dataset/contrato ausentes")
    authorized_by = document.get("authorized_by")
    if not isinstance(authorized_by, str) or not authorized_by.strip():
        raise TabularExportError("BLOCKED_AUTHORIZATION: responsável ausente")
    authorized_at = _aware(document.get("authorized_at"), "authorized_at")
    return TabularAuthorization(
        target=TARGET_NAME,
        dataset_sha256=document["dataset_sha256"],
        contract_sha256=document["contract_sha256"],
        authorized_by=authorized_by.strip(),
        authorized_at=authorized_at.isoformat(),
    )


def _matrix(rows: Sequence[TabularExample]) -> Any:
    import numpy as np

    columns = list(FEATURE_COLUMNS)
    # None stays missing (NaN, handled natively by XGBoost); it never becomes zero.
    return np.array(
        [[np.nan if e.features[c] is None else e.features[c] for c in columns] for e in rows],
        dtype=float,
    ).reshape(len(rows), len(columns))


@dataclass(frozen=True)
class TabularFit:
    model: Any
    split: dict[str, Any]
    best_iteration: int
    validation_log_loss: list[float]
    readiness: dict[str, Any]

    def predict_raw(self, rows: Sequence[TabularExample]) -> list[float]:
        if not rows:
            return []
        proba = self.model.predict_proba(
            _matrix(rows), iteration_range=(0, self.best_iteration + 1)
        )
        return [float(value) for value in proba[:, 1]]


def train_xgboost(
    train: Sequence[TabularExample] | VerifiedTrainingBundle,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    *,
    authorization: TabularAuthorization | None = None,
    contract: TrainingContract | None = None,
    output_root: Path | None = None,
) -> Any:
    """Single offline fit entry point; fails closed on data, binding, contract or library.

    Two dataset bindings exist and are never mixed:
    - a `VerifiedTrainingBundle` (registered DatasetVersion, split plan and approval pinned
      by hash; Event and external targets) fits through `_fit_verified_xgboost`;
    - Event examples with a human `TabularAuthorization` bound to the dataset digest and a
      pre-registered `TrainingContract` fit here; `run_tabular_training` then adds the
      calibration, grouped bootstrap and promotion record that the runtime loader reads.
    Unbound examples are always blocked.
    """
    if isinstance(train, VerifiedTrainingBundle):
        if (
            seed is not None
            or params is not None
            or authorization is not None
            or contract is not None
            or output_root is None
        ):
            raise TabularExportError("approved config and output directory required for fit")
        return _fit_verified_xgboost(train, output_root)
    examples = train
    readiness = training_readiness(examples, authorization)
    if readiness["status"] != "READY":
        raise TabularExportError(f"BLOCKED_DATA: {readiness['blockers']}")
    if authorization is None or not readiness["training_authorized"]:
        raise TabularExportError("BLOCKED_AUTHORIZATION: dataset-bound approval required")
    if contract is None or authorization.contract_sha256 != contract.sha256():
        raise TabularExportError("BLOCKED_AUTHORIZATION: contrato difere do autorizado")
    if seed != contract.seed or (params is not None and params != contract.params):
        raise TabularExportError("seed/hiperparâmetros fora do contrato pré-registrado")
    split = temporal_split(
        examples,
        contract.validation_from,
        contract.test_from,
        calibration_from=contract.calibration_from,
    )
    for name in _SPLITS:
        if {e.target for e in split[name]} != {0, 1}:
            raise TabularExportError(f"BLOCKED_DATA: {name} sem as duas classes do alvo")
    try:
        import numpy as np
        import xgboost  # type: ignore[import-not-found]
    except ImportError as exc:
        raise XGBoostUnavailableError("xgboost não instalado no backend/.venv") from exc
    model = xgboost.XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        missing=np.nan,
        random_state=seed,
        n_estimators=contract.n_estimators,
        early_stopping_rounds=contract.early_stopping_rounds,
        # Conservative, reproducible defaults; the contract may override them.
        # No class weights or resampling: probabilities must reflect the population.
        **{"tree_method": "hist", "n_jobs": 1, "max_depth": 3, **contract.params},
    )
    model.fit(
        _matrix(split["train"]),
        np.array([e.target for e in split["train"]]),
        eval_set=[(_matrix(split["validation"]), np.array([e.target for e in split["validation"]]))],
        verbose=False,
    )
    return TabularFit(
        model=model,
        split=split,
        best_iteration=int(model.best_iteration),
        validation_log_loss=[float(v) for v in model.evals_result()["validation_0"]["logloss"]],
        readiness=readiness,
    )


# ---------------------------------------------------------------- calibration


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def fit_calibrator(y_true: Sequence[int], p: Sequence[float], method: str) -> dict[str, Any]:
    """Fit on the calibration cohort only; the method was fixed by the contract."""
    if len(y_true) != len(p) or set(y_true) != {0, 1}:
        raise TabularExportError("calibração exige as duas classes na coorte de calibração")
    if method == "isotonic" and len(y_true) < MIN_ISOTONIC_CALIBRATION_EXAMPLES:
        # Isotonic overfits small cohorts; the contract must pick Platt instead.
        raise TabularExportError(
            f"isotônica exige >= {MIN_ISOTONIC_CALIBRATION_EXAMPLES} exemplos de calibração; use platt"
        )
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression  # type: ignore[import-untyped]

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y_true)
        return {
            "method": "isotonic",
            "x": [float(v) for v in iso.X_thresholds_],
            "y": [float(v) for v in iso.y_thresholds_],
        }
    if method != "platt":
        raise TabularExportError(f"método de calibração desconhecido: {method}")
    # Platt scaling on the logit: damped Newton (step halving), so the loss never rises.
    z = [_logit(v) for v in p]

    def loss(a: float, b: float) -> float:
        return sum(
            _per_example_log_loss(yi, _sigmoid(a * zi + b)) for yi, zi in zip(y_true, z, strict=True)
        )

    a, b = 1.0, 0.0
    current = loss(a, b)
    for _ in range(100):
        q = [_sigmoid(a * zi + b) for zi in z]
        ga = sum((qi - yi) * zi for qi, yi, zi in zip(q, y_true, z, strict=True))
        gb = sum(qi - yi for qi, yi in zip(q, y_true, strict=True))
        w = [qi * (1 - qi) for qi in q]
        haa = sum(wi * zi * zi for wi, zi in zip(w, z, strict=True)) + 1e-9
        hab = sum(wi * zi for wi, zi in zip(w, z, strict=True))
        hbb = sum(w) + 1e-9
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        step = 1.0
        while step > 1e-8 and loss(a - step * da, b - step * db) > current:
            step /= 2
        if step <= 1e-8:
            break
        a, b = a - step * da, b - step * db
        previous, current = current, loss(a, b)
        if previous - current < 1e-12:
            break
    return {"method": "platt", "a": a, "b": b}


def apply_calibrator(calibrator: dict[str, Any], p: Sequence[float]) -> list[float]:
    if calibrator["method"] == "platt":
        return [_sigmoid(calibrator["a"] * _logit(v) + calibrator["b"]) for v in p]
    if calibrator["method"] == "isotonic":
        import numpy as np

        return [float(v) for v in np.interp(p, calibrator["x"], calibrator["y"])]
    raise TabularExportError(f"calibrador desconhecido: {calibrator['method']}")


# ---------------------------------------------------------------- evaluation run


def _pr_auc(y_true: Sequence[int], p: Sequence[float]) -> float | None:
    if set(y_true) != {0, 1}:
        return None
    from sklearn.metrics import average_precision_score  # type: ignore[import-untyped]

    return float(average_precision_score(y_true, p))


def _per_example_log_loss(y: int, p: float) -> float:
    p = min(max(p, 1e-12), 1 - 1e-12)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def grouped_bootstrap_log_loss_gain(
    examples: Sequence[TabularExample],
    p_reference: Sequence[float],
    p_model: Sequence[float],
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Log-loss gain (reference minus model), resampling whole leakage groups."""
    import numpy as np

    groups = leakage_groups(examples)
    members: dict[str, list[int]] = {}
    for index, example in enumerate(examples):
        members.setdefault(groups[example.event_id], []).append(index)
    keys = sorted(members)
    gain = [
        _per_example_log_loss(e.target, r) - _per_example_log_loss(e.target, m)
        for e, r, m in zip(examples, p_reference, p_model, strict=True)
    ]
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(replicates):
        picked = [i for k in rng.choice(len(keys), size=len(keys)) for i in members[keys[k]]]
        samples.append(sum(gain[i] for i in picked) / len(picked))
    low, high = (float(v) for v in np.percentile(samples, [2.5, 97.5]))
    return {
        "metric": "log_loss(prior) - log_loss(model)",
        "estimate": sum(gain) / len(gain),
        "ci95": [low, high],
        "replicates": replicates,
        "groups": len(keys),
        "method": "grouped_bootstrap_percentile",
    }


def leakage_audit(split: dict[str, Any]) -> list[str]:
    """Automated findings over the fitted split; empty means none were found."""
    findings = []
    everything = [e for name in _SPLITS for e in split[name]]
    groups = leakage_groups(everything)
    owner: dict[str, str] = {}
    for name in _SPLITS:
        for example in split[name]:
            if owner.setdefault(groups[example.event_id], name) != name:
                findings.append(f"grupo {groups[example.event_id]} em {owner[groups[example.event_id]]} e {name}")
            if not example.occurred_at <= example.snapshot_collected_at < example.label_at:
                findings.append(f"vazamento temporal em {example.event_id}")
    cutoffs = {
        "train": split["validation_from"],
        "validation": split["calibration_from"],
        "calibration": split["test_from"],
    }
    for name, cutoff in cutoffs.items():
        if any(e.label_at >= datetime.fromisoformat(cutoff) for e in split[name]):
            findings.append(f"rótulo de {name} indisponível antes do corte seguinte")
    return findings


def run_tabular_training(
    export_document: dict[str, Any],
    contract_document: dict[str, Any],
    authorization_document: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """Authorized end-to-end run: fit, early stop, calibrate, evaluate on held-out test, persist.

    Never promotes: calibration review and human approval stay False in the report.
    """
    examples = parse_ground_truth_export(export_document)
    contract = load_training_contract(contract_document)
    authorization = load_authorization(authorization_document)
    if out_dir.exists():
        raise TabularExportError(f"diretório de saída já existe, não sobrescrevo: {out_dir}")
    fit = train_xgboost(examples, contract.seed, authorization=authorization, contract=contract)
    split = fit.split
    calibration_rows, test_rows = split["calibration"], split["test"]
    calibrator = fit_calibrator(
        [e.target for e in calibration_rows],
        fit.predict_raw(calibration_rows),
        contract.calibration_method,
    )
    y_test = [e.target for e in test_rows]
    raw = fit.predict_raw(test_rows)
    calibrated = apply_calibrator(calibrator, raw)
    prior = prior_baseline(split["train"])
    prior_p = [prior(e) for e in test_rows]
    variants = {"prior_baseline": prior_p, "xgboost_raw": raw, "xgboost_calibrated": calibrated}
    metrics = {
        name: {**binary_metrics(y_test, p), "pr_auc": _pr_auc(y_test, p)}
        for name, p in variants.items()
    }
    gain = grouped_bootstrap_log_loss_gain(
        test_rows, prior_p, calibrated, contract.bootstrap_replicates, contract.seed
    )
    findings = leakage_audit(split)
    import numpy as np
    import xgboost  # type: ignore[import-not-found]

    contributions = fit.model.get_booster().predict(
        xgboost.DMatrix(_matrix(test_rows), missing=np.nan),
        pred_contribs=True,
        iteration_range=(0, fit.best_iteration + 1),
    )
    importance = {
        column: float(np.mean(np.abs(contributions[:, index])))
        for index, column in enumerate(FEATURE_COLUMNS)
    }
    evaluation = {
        "held_out_split": True,
        "beats_prior_baseline": gain["ci95"][0] > 0,
        "calibration_reviewed": False,
        "leakage_audit_passed": not findings,
        "human_approval": False,
    }
    out_dir.mkdir(parents=True)
    model_path, calibration_path = out_dir / "model.json", out_dir / "calibration.json"
    fit.model.get_booster().save_model(str(model_path))
    calibration_path.write_text(
        json.dumps(calibrator, indent=2) + "\n", encoding="utf8", newline="\n"
    )
    report = {
        "schema_version": TABULAR_SCHEMA_VERSION,
        "feature_schema_version": PINNED_FEATURE_SCHEMA,
        "feature_columns": list(FEATURE_COLUMNS),
        "target": TARGET_NAME,
        "dataset_sha256": fit.readiness["dataset_sha256"],
        "contract": contract.document(),
        "contract_sha256": contract.sha256(),
        "authorization": asdict(authorization),
        "xgboost_version": xgboost.__version__,
        "best_iteration": fit.best_iteration,
        "validation_log_loss": fit.validation_log_loss,
        "split_counts": {name: len(split[name]) for name in _SPLITS},
        "dropped_for_group_leakage": split["dropped_for_group_leakage"],
        "dropped_for_label_availability": split["dropped_for_label_availability"],
        "test_metrics": metrics,
        "test_log_loss_gain_vs_prior": gain,
        "test_reliability": {
            "xgboost_raw": reliability_bins(y_test, raw),
            "xgboost_calibrated": reliability_bins(y_test, calibrated),
        },
        "mean_abs_contribution_test": importance,
        "contribution_note": "associação no modelo (SHAP TreeExplainer), não causalidade",
        "leakage_findings": findings,
        "evaluation": evaluation,
        "promotion": promotion_gate(evaluation),
        "artifacts": {
            "model.json": _file_sha256(model_path),
            "calibration.json": _file_sha256(calibration_path),
        },
        "runtime_allowed": False,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf8", newline="\n"
    )
    return report


@dataclass(frozen=True)
class PromotedTabularModel:
    """Loaded only through `load_promoted_model`; predicts calibrated probability."""

    booster: Any
    calibrator: dict[str, Any]
    best_iteration: int
    report_sha256: str

    def predict_snapshot(self, snapshot: dict[str, Any]) -> float:
        import numpy as np
        import xgboost  # type: ignore[import-not-found]

        row = flatten_snapshot(snapshot)
        matrix = np.array(
            [[np.nan if row[c] is None else row[c] for c in FEATURE_COLUMNS]], dtype=float
        )
        raw = self.booster.predict(
            xgboost.DMatrix(matrix, missing=np.nan),
            iteration_range=(0, self.best_iteration + 1),
        )
        return apply_calibrator(self.calibrator, [float(raw[0])])[0]


def load_promoted_model(directory: Path) -> PromotedTabularModel:
    """Fail-closed runtime loader: hashes intact and a human promotion record for this report."""
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text(encoding="utf8"))
    if not isinstance(report, dict):
        raise TabularExportError("report.json malformado")
    if report.get("feature_schema_version") != PINNED_FEATURE_SCHEMA or report.get(
        "feature_columns"
    ) != list(FEATURE_COLUMNS):
        raise TabularExportError("modelo treinado com outro schema de features")
    artifacts = report.get("artifacts")
    # The weights and the calibrator must themselves be hash-pinned, never just present.
    if not isinstance(artifacts, dict) or not {"model.json", "calibration.json"} <= set(artifacts):
        raise TabularExportError("report sem hash de model.json e calibration.json")
    if not isinstance(report.get("evaluation"), dict) or not isinstance(
        report.get("best_iteration"), int
    ):
        raise TabularExportError("report.json malformado")
    for name, expected in artifacts.items():
        if Path(name).name != name or _file_sha256(directory / name) != expected:
            raise TabularExportError(f"artefato alterado: {name}")
    promotion_path = directory / "promotion.json"
    if not promotion_path.is_file():
        raise TabularExportError("BLOCKED_PROMOTION: promotion.json ausente")
    promotion = json.loads(promotion_path.read_text(encoding="utf8"))
    report_sha256 = _file_sha256(report_path)
    if promotion.get("report_sha256") != report_sha256:
        raise TabularExportError("BLOCKED_PROMOTION: aprovação não vinculada a este report")
    if not isinstance(promotion.get("approved_by"), str) or not promotion["approved_by"].strip():
        raise TabularExportError("BLOCKED_PROMOTION: aprovador ausente")
    _aware(promotion.get("approved_at"), "approved_at")
    gate = promotion_gate(
        {
            **report["evaluation"],
            "calibration_reviewed": promotion.get("calibration_reviewed") is True,
            "human_approval": promotion.get("human_approval") is True,
        }
    )
    if not gate["promotable"]:
        raise TabularExportError(f"BLOCKED_PROMOTION: {gate['missing']}")
    try:
        import xgboost  # type: ignore[import-not-found]
    except ImportError as exc:
        raise XGBoostUnavailableError("xgboost não instalado no backend/.venv") from exc
    booster = xgboost.Booster()
    booster.load_model(str(directory / "model.json"))
    return PromotedTabularModel(
        booster=booster,
        calibrator=json.loads((directory / "calibration.json").read_text(encoding="utf8")),
        best_iteration=int(report["best_iteration"]),
        report_sha256=report_sha256,
    )


def tabular_runtime(directory: Path | None) -> dict[str, Any]:
    """Resolve the tabular model once; failures degrade to an explicit status, never a guess."""
    if directory is None:
        return {"status": "DISABLED", "reason": "nenhum modelo tabular configurado", "model": None}
    try:
        model = load_promoted_model(directory)
    except (TabularExportError, XGBoostUnavailableError) as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc), "model": None}
    except Exception as exc:  # noqa: BLE001 — a broken artifact must never block the rules
        reason = f"modelo tabular ilegível: {type(exc).__name__}"
        return {"status": "UNAVAILABLE", "reason": reason, "model": None}
    return {"status": "AVAILABLE", "reason": None, "model": model}


def predict_review_confirmed(runtime: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Advisory estimate for the decision trace. It never changes Review, publication,
    severity, risk, priority or the detector class; rules keep deciding."""
    result: dict[str, Any] = {
        "target": TARGET_NAME,
        "status": runtime["status"],
        "probability": None,
        "reason": runtime["reason"],
        "advisory_only": True,
    }
    if runtime["status"] == "AVAILABLE":
        result["report_sha256"] = runtime["model"].report_sha256
        try:
            result["probability"] = runtime["model"].predict_snapshot(snapshot)
        except Exception as exc:  # noqa: BLE001 — an advisory failure never blocks the rules
            result["status"] = "ERROR"
            result["reason"] = f"previsão indisponível: {type(exc).__name__}"
    return result


@lru_cache(maxsize=4)
def _runtime_for(directory: str | None) -> dict[str, Any]:
    return tabular_runtime(Path(directory) if directory else None)


def configured_tabular_runtime() -> dict[str, Any]:
    """TABULAR_MODEL_DIR resolved once per process; unset stays DISABLED."""
    from app.config import get_settings

    directory = get_settings().tabular_model_dir
    return _runtime_for(str(directory) if directory else None)


def promotion_gate(evaluation: dict[str, Any]) -> dict[str, Any]:
    """A tabular model is never promoted without every piece of evidence."""
    required = {
        "held_out_split": "avaliação em conjunto não usado no treino",
        "beats_prior_baseline": "melhor que o baseline de prior no mesmo conjunto",
        "calibration_reviewed": "curva de confiabilidade revisada",
        "leakage_audit_passed": "auditoria de vazamento sem achados",
        "human_approval": "aprovação humana registrada",
    }
    missing = [reason for key, reason in required.items() if evaluation.get(key) is not True]
    return {"promotable": not missing, "missing": missing, "runtime_allowed": not missing}


def labeling_protocol() -> dict[str, Any]:
    """Unfilled human annotation form. Targets are never inferred from current rules."""
    meanings = {
        "review_confirmed": (
            "Adjudicated confirm/reject under the occurrence review protocol",
            "binary log loss, Brier, PR-AUC",
        ),
        "severity": (
            "Independent inspection of physical damage under a versioned ordinal rubric",
            "ordinal MAE, weighted kappa, per-level recall",
        ),
        "risk": (
            "Observed adverse outcome within a fixed horizon, conditioned on measured exposure",
            "Brier, calibration, PR-AUC; exposure-stratified",
        ),
        "priority": (
            "Independent expert ranking given an explicit service policy and capacity",
            "ranking agreement, NDCG and policy-stratified errors",
        ),
        "recurrence": (
            "New adjudicated occurrence during documented follow-up, accounting for censoring",
            "time-dependent calibration and concordance; censoring-aware",
        ),
    }
    tasks = {
        "review_confirmed": "binary classification",
        "severity": "ordinal classification",
        "risk": "horizon-defined binary outcome; probability only after calibration",
        "priority": "policy-defined ordinal ranking",
        "recurrence": "time-to-event with censoring",
    }
    return {
        "protocol_version": "urmind-tabular-labeling-v2-draft",
        "targets": {
            name: {
                "meaning": meaning,
                "task": tasks[name],
                "unit_of_analysis": "one Event at its immutable assessment snapshot",
                "decision_time": "snapshot collected_at, before first reviewer action",
                "label_source": (
                    "persisted Review consensus/adjudication"
                    if name == TARGET_NAME
                    else "independent human or observed outcome evidence pending"
                ),
                "approval_status": "DRAFT" if name == TARGET_NAME else "TARGET_APPROVAL_PENDING",
                "allowed_values": [0, 1] if name == TARGET_NAME else None,
                "proposed_values": (
                    ["low", "medium", "high", "critical"]
                    if name == "severity"
                    else ["routine", "elevated", "expedited"]
                    if name == "priority"
                    else None
                ),
                "limitations": (
                    "Review agreement is not severity, risk, or priority"
                    if name == TARGET_NAME
                    else "Current rule output is not independent Ground Truth; sample and rubric pending"
                ),
                "metrics": metric,
                "training_authorized": False,
                "forbidden_label_sources": ["rule_score", "detector_confidence"]
                + ([] if name == TARGET_NAME else [TARGET_NAME]),
                "annotation_protocol": "two independent qualified reviewers, blinded to model/rule scores; disagreement requires attributed adjudication",
                "features": list(FEATURE_COLUMNS),
                "observation_time": "persisted immutable snapshot before first review; all features available by declared cutoff",
                "diversity": "report independent scenes/segments, classes, sources, domains, detector versions and missingness",
                "sample_size": "determine by target prevalence, clustered uncertainty and power; current provisional floor is not evidence",
                "validation": "temporal+group isolation; separate early stopping, calibration and final holdout; no protected-set reuse",
                "baseline": "training prior for binary target; simple target-matched model for other tasks",
                "early_stopping": "validation-only metric; patience and budget fixed before fitting",
                "required_extra_evidence": (
                    [
                        "horizon",
                        "follow_up_start",
                        "follow_up_end",
                        "censoring",
                        "outcome_evidence",
                        "exposure",
                    ]
                    if name in {"risk", "recurrence"}
                    else ["rubric_version", "independent_inspection_evidence"]
                ),
            }
            for name, (meaning, metric) in meanings.items()
        },
        "form": {
            name: None
            for name in (
                "target",
                "target_version",
                "approval_status",
                "event_id",
                "capture_ids",
                "scene_group_id",
                "duplicate_group_id",
                "sequence_group_id",
                "road_segment_id",
                "snapshot_id",
                "snapshot_sha256",
                "feature_schema_version",
                "snapshot_collected_at",
                "knowledge_cutoff",
                "detector_version",
                "detector_origin",
                "preprocessing_version",
                "postprocessing_version",
                "feature_provenance",
                "label",
                "label_source",
                "review_id",
                "reviewers",
                "review_status",
                "adjudication_id",
                "label_observed_at",
                "label_at",
                "available_at",
                "review_started_at",
                "reviewed_at",
                "reviewer",
                "rubric_version",
                "evidence_ids",
                "measurement_units",
                "horizon",
                "outcome_evidence",
                "exposure",
                "follow_up_start",
                "follow_up_end",
                "censoring",
                "second_review",
                "adjudication",
                "use_authorized",
            )
        },
        "point_in_time_export": "CoreService.tabular_ground_truth; retrospective or post-review snapshots refused",
        "training_authorized": False,
    }


def validate_label_import(document: dict[str, Any]) -> dict[str, Any]:
    """Audit attributed labels, including pending targets, without authorizing fit."""
    target = document.get("target")
    if not isinstance(target, str) or target not in labeling_protocol()["targets"]:
        raise TabularExportError("unknown tabular target")
    version = document.get("target_version")
    if not isinstance(version, str) or not version.strip():
        raise TabularExportError("target version missing")
    rows = document.get("rows")
    if not isinstance(rows, list):
        raise TabularExportError("label rows must be a list")
    seen: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TabularExportError("label row must be an object")
        for name in ("event_id", "snapshot_id", "snapshot_sha256", "label_source"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise TabularExportError(f"label {name} missing")
        if target == TARGET_NAME and (
            not isinstance(row.get("review_id"), str) or not row["review_id"].strip()
        ):
            raise TabularExportError("label review_id missing")
        if target != TARGET_NAME and row["label_source"] == "persisted_review":
            raise TabularExportError("review confirmation cannot label another target")
        if target != TARGET_NAME and (
            not isinstance(row.get("evidence_ids"), list)
            or not row["evidence_ids"]
            or any(not isinstance(value, str) or not value.strip() for value in row["evidence_ids"])
        ):
            raise TabularExportError("independent label evidence missing")
        if len(row["snapshot_sha256"]) != 64 or any(
            char not in "0123456789abcdef" for char in row["snapshot_sha256"]
        ):
            raise TabularExportError("label snapshot hash invalid")
        if row.get("feature_schema_version") != PINNED_FEATURE_SCHEMA:
            raise TabularExportError("label feature schema mismatch")
        if (
            not isinstance(row.get("capture_ids"), list)
            or not row["capture_ids"]
            or any(not isinstance(value, str) or not value.strip() for value in row["capture_ids"])
        ):
            raise TabularExportError("label Capture lineage missing")
        for name in ("scene_group_id", "duplicate_group_id", "sequence_group_id"):
            value = row.get(name)
            if not isinstance(value, str) or value in {"", "UNCONFIRMED"}:
                raise TabularExportError(f"label {name} unknown")
        if not isinstance(row.get("detector_version"), str) or not row["detector_version"]:
            raise TabularExportError("label detector version missing")
        if row.get("detector_origin") != "persisted_detector":
            raise TabularExportError("label detector provenance unverified")
        source_by_target = {
            TARGET_NAME: "persisted_review",
            "severity": "independent_inspection",
            "priority": "independent_inspection",
            "risk": "observed_outcome",
            "recurrence": "observed_outcome",
        }
        if row["label_source"] != source_by_target[target]:
            raise TabularExportError("label source incompatible with target")
        reviewers = row.get("reviewers")
        if (
            not isinstance(reviewers, list)
            or any(not isinstance(value, str) or not value.strip() for value in reviewers)
            or len(set(reviewers)) < 2
        ):
            raise TabularExportError("two distinct attributable reviewers required")
        if row.get("review_status") not in {"consensus", "adjudicated"}:
            raise TabularExportError("review incomplete or conflicted")
        if row["review_status"] == "adjudicated" and not row.get("adjudication_id"):
            raise TabularExportError("adjudication evidence missing")
        try:
            observed = _timestamp(row["observed_at"])
            available = _timestamp(row["available_at"])
            cutoff = _timestamp(row["knowledge_cutoff"])
            labeled = _timestamp(row["label_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TabularExportError("label timestamps missing or invalid") from exc
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (observed, available, cutoff, labeled)
        ):
            raise TabularExportError("label timestamps require timezone")
        if not observed <= available <= cutoff < labeled:
            raise TabularExportError("label temporal order invalid")
        label = row.get("label")
        if target == TARGET_NAME and (type(label) is not int or label not in {0, 1}):
            raise TabularExportError("review_confirmed label must be 0 or 1")
        if target != TARGET_NAME and (not isinstance(label, str) or not label.strip()):
            raise TabularExportError("label missing")
        if target in {"severity", "priority"} and (
            not isinstance(row.get("rubric_version"), str) or not row["rubric_version"].strip()
        ):
            raise TabularExportError("ordinal label rubric version missing")
        if target in {"risk", "recurrence"} and any(
            row.get(name) in (None, "")
            for name in (
                "horizon",
                "follow_up_start",
                "follow_up_end",
                "outcome_evidence",
                "exposure",
                "censoring",
            )
        ):
            raise TabularExportError("future outcome horizon/evidence missing")
        if target in {"risk", "recurrence"}:
            try:
                follow_up_start = _timestamp(row["follow_up_start"])
                follow_up_end = _timestamp(row["follow_up_end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TabularExportError("future outcome follow-up timestamps invalid") from exc
            if (
                any(
                    stamp.tzinfo is None or stamp.utcoffset() is None
                    for stamp in (follow_up_start, follow_up_end)
                )
                or not observed <= follow_up_start < follow_up_end <= labeled
            ):
                raise TabularExportError("future outcome follow-up order invalid")
            if follow_up_end <= cutoff:
                raise TabularExportError("future outcome must extend beyond prediction cutoff")
        key = row["event_id"]
        if key in seen:
            reason = "conflicting labels" if seen[key] != label else "duplicate label"
            raise TabularExportError(reason)
        seen[key] = label
        if row.get("use_authorized") is not True:
            raise TabularExportError("label use authorization missing")
    return {
        "target": target,
        "target_version": version,
        "rows": len(rows),
        "candidate_counts": dict(Counter(row["label"] for row in rows)),
        "label_contract_valid_rows": len(rows),
        "training_eligible_rows": 0,
        "status": "DRAFT" if target == TARGET_NAME else "TARGET_APPROVAL_PENDING",
        "training_authorized": False,
    }


def validate_visual_output(
    payload: dict[str, Any], *, may_emit: Callable[[str], bool] | None = None
) -> dict[str, Any]:
    """Consume recorded detection metadata or synthetic fixtures; never load YOLOX."""
    for name in (
        "model_version",
        "preprocessing_version",
        "postprocessing_version",
        "feature_version",
    ):
        if not isinstance(payload.get(name), str) or not payload[name].strip():
            raise TabularExportError(f"visual {name} missing")
    checkpoint = payload.get("checkpoint_sha256")
    if (
        not isinstance(checkpoint, str)
        or len(checkpoint) != 64
        or any(char not in "0123456789abcdef" for char in checkpoint)
    ):
        raise TabularExportError("visual checkpoint SHA-256 invalid")
    class_order = payload.get("class_order")
    if (
        not isinstance(class_order, list)
        or not class_order
        or any(not isinstance(name, str) or not name.strip() for name in class_order)
        or len(set(class_order)) != len(class_order)
    ):
        raise TabularExportError("visual class order invalid")
    if payload.get("origin") not in {"persisted_detector", "synthetic_fixture", "human_annotation"}:
        raise TabularExportError("visual origin missing or unsupported")
    width, height = payload.get("image_width"), payload.get("image_height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise TabularExportError("image dimensions invalid")
    detections = payload.get("detections")
    if not isinstance(detections, list):
        raise TabularExportError("detections must be a list")
    if detections and may_emit is None:
        try:
            from app.schemas.issue_taxonomy import model_may_emit
        except ImportError as exc:
            raise TabularExportError(
                "visual taxonomy dependencies unavailable; install in isolated tabular environment later"
            ) from exc
        may_emit = model_may_emit
    for detection in detections:
        if (
            not isinstance(detection, dict)
            or not isinstance(detection.get("class"), str)
            or may_emit is None
            or not may_emit(detection.get("class", ""))
        ):
            raise TabularExportError("unsupported visual class")
        confidence, box = detection.get("confidence"), detection.get("bbox")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise TabularExportError("invalid detection confidence")
        if not isinstance(box, dict) or any(
            type(box.get(key)) not in {int, float} or not math.isfinite(box[key])
            for key in ("x", "y", "width", "height")
        ):
            raise TabularExportError("invalid bbox")
        if not (
            0 <= box["x"] < 1
            and 0 <= box["y"] < 1
            and box["width"] > 0
            and box["height"] > 0
            and box["x"] + box["width"] <= 1
            and box["y"] + box["height"] <= 1
        ):
            raise TabularExportError("bbox outside image")
    return {
        "detections": len(detections),
        "observation": "NO_DETECTION_OBSERVED" if not detections else "DETECTIONS_RECORDED",
        "origin": payload["origin"],
        "model_version": payload["model_version"],
        "checkpoint_sha256": checkpoint,
        "class_order": class_order,
        "feature_version": payload["feature_version"],
        "training_authorized": False,
    }


def _registered_artifact(path: Path, registry_path: Path) -> tuple[Path, str]:
    """Verify bytes against the project's artifact registry without reading image data."""
    if (
        registry_path.name != "artifact_registry.json"
        or registry_path.parent.name != "metadata"
        or registry_path.parent.parent.name != "datasets"
    ):
        raise TabularExportError("official artifact registry required")
    root = registry_path.resolve().parents[2]
    actual = path.resolve()
    if not actual.is_relative_to(root):
        raise TabularExportError("artifact outside registry root")
    registry = json.loads(registry_path.read_text(encoding="utf8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("artifacts"), list):
        raise TabularExportError("artifact registry malformed")
    relative = actual.relative_to(root).as_posix()
    entries = [
        entry
        for entry in registry["artifacts"]
        if isinstance(entry, dict) and entry.get("path") == relative
    ]
    if len(entries) != 1:
        raise TabularExportError(f"artifact not uniquely registered: {relative}")
    entry = entries[0]
    if not actual.is_file() or actual.stat().st_size != entry.get("size_bytes"):
        raise TabularExportError(f"registered artifact absent or size mismatch: {relative}")
    digest = hashlib.sha256()
    with actual.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != entry.get("sha256"):
        raise TabularExportError(f"registered artifact hash mismatch: {relative}")
    return actual, digest.hexdigest()


def _yolox_exposure_index(
    registry_path: Path, manifests: dict[str, Path]
) -> tuple[dict[str, str], dict[str, str]]:
    """Index only explicitly supplied TRAIN/VALIDATION manifests; never open TEST."""
    if set(manifests) != {"TRAIN", "VALIDATION"}:
        raise TabularExportError("YOLOX TRAIN and VALIDATION manifests required")
    fingerprints: dict[str, str] = {}
    manifest_hashes: dict[str, str] = {}
    for role, path in manifests.items():
        actual, digest = _registered_artifact(path, registry_path)
        manifest_hashes[role] = digest
        with actual.open(encoding="utf8") as stream:
            for line in stream:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise TabularExportError("invalid YOLOX manifest row")
                fingerprint = record.get("source_fingerprint")
                if (
                    record.get("split") != role
                    or not isinstance(fingerprint, str)
                    or not fingerprint
                ):
                    raise TabularExportError("invalid YOLOX manifest identity or role")
                status = f"YOLOX_{role}_EXPOSED"
                if fingerprint in fingerprints and fingerprints[fingerprint] != status:
                    raise TabularExportError("YOLOX fingerprint crosses manifests")
                fingerprints[fingerprint] = status
    return fingerprints, manifest_hashes


def validate_split_plan(
    document: dict[str, Any],
    *,
    yolox_registry: Path | None = None,
    yolox_manifests: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Check roles/groups and derive exposure from registered YOLOX manifests."""
    rows = document.get("rows")
    if not isinstance(rows, list):
        raise TabularExportError("split rows must be a list")
    if any(not isinstance(row, dict) for row in rows):
        raise TabularExportError("split row must be an object")
    if any(
        not isinstance(row.get("capture_ids"), list)
        or any(not isinstance(value, str) for value in row["capture_ids"])
        for row in rows
    ):
        raise TabularExportError("split Capture lineage missing")
    if (yolox_registry is None) != (yolox_manifests is None):
        raise TabularExportError("YOLOX registry and manifests must be supplied together")
    fingerprints, manifest_hashes = (
        _yolox_exposure_index(yolox_registry, yolox_manifests)
        if yolox_registry is not None and yolox_manifests is not None
        else ({}, {})
    )
    roles = {"TRAIN", "VALIDATION", "CALIBRATION", "TEST"}
    seen_events: set[str] = set()
    owners: dict[tuple[str, str], str] = {}
    counts: Counter[str] = Counter()
    times: dict[str, list[datetime]] = {role: [] for role in roles}
    label_times: dict[str, list[datetime]] = {role: [] for role in roles}
    exposure_by_event: dict[str, str] = {}
    group_ids = leakage_groups(
        [
            SimpleNamespace(
                **{
                    key: row.get(key)
                    for key in (
                        "event_id",
                        "capture_ids",
                        "road_segment_id",
                        "scene_group_id",
                        "duplicate_group_id",
                        "sequence_group_id",
                    )
                }
            )
            for row in rows
        ]
    )
    exposed_groups: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("role") not in roles:
            raise TabularExportError("invalid split role")
        role = row["role"]
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_events:
            raise TabularExportError("missing or duplicate split Event")
        seen_events.add(event_id)
        if not isinstance(row.get("capture_ids"), list) or not row["capture_ids"]:
            raise TabularExportError("split Capture lineage missing")
        for name in ("scene_group_id", "duplicate_group_id", "sequence_group_id"):
            value = row.get(name)
            if not isinstance(value, str) or value in {"", "UNCONFIRMED"}:
                raise TabularExportError(f"unknown {name}")
        group_values = {
            "event_id": [event_id],
            "capture_ids": row["capture_ids"],
            "road_segment_id": [row["road_segment_id"]] if row.get("road_segment_id") else [],
            **{
                name: [row[name]]
                for name in ("scene_group_id", "duplicate_group_id", "sequence_group_id")
            },
        }
        for name, values in group_values.items():
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise TabularExportError("invalid split group")
                key = (name, value)
                if key in owners and owners[key] != role:
                    raise TabularExportError("group overlaps split roles")
                owners[key] = role
        try:
            observed = datetime.fromisoformat(row["observed_at"])
            labeled = datetime.fromisoformat(row["label_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TabularExportError("split timestamps invalid") from exc
        if observed.tzinfo is None or labeled.tzinfo is None or labeled < observed:
            raise TabularExportError("split temporal lineage invalid")
        claimed = row.get("yolox_exposure", "YOLOX_EXPOSURE_UNKNOWN")
        if claimed not in YOLOX_EXPOSURES:
            raise TabularExportError("invalid YOLOX exposure state")
        source_fingerprints = row.get("visual_source_fingerprints") or []
        if not isinstance(source_fingerprints, list) or any(
            not isinstance(value, str) or not value for value in source_fingerprints
        ):
            raise TabularExportError("invalid visual source fingerprints")
        matches = {fingerprints[value] for value in source_fingerprints if value in fingerprints}
        derived = (
            "YOLOX_TRAIN_EXPOSED"
            if "YOLOX_TRAIN_EXPOSED" in matches
            else "YOLOX_VALIDATION_EXPOSED"
            if matches
            else "YOLOX_EXPOSURE_UNKNOWN"
        )
        if claimed not in {"YOLOX_EXPOSURE_UNKNOWN", derived}:
            raise TabularExportError("YOLOX exposure declaration lacks manifest proof")
        exposure_by_event[event_id] = derived
        group = group_ids[event_id]
        if derived == "YOLOX_TRAIN_EXPOSED" or (
            derived == "YOLOX_VALIDATION_EXPOSED" and group not in exposed_groups
        ):
            exposed_groups[group] = derived
        counts[role] += 1
        times[role].append(observed)
        label_times[role].append(labeled)
    ordered_roles = ("TRAIN", "VALIDATION", "CALIBRATION", "TEST")
    if document.get("temporal") is True and times["TRAIN"]:
        present_roles = [role for role in ordered_roles if times[role]]
        for previous, following in pairwise(present_roles):
            if (
                times[previous]
                and times[following]
                and (
                    max(times[previous]) >= min(times[following])
                    or max(label_times[previous]) >= min(times[following])
                )
            ):
                raise TabularExportError("temporal split order or label availability invalid")
    blockers = [f"{role} empty" for role in ("TRAIN", "VALIDATION") if counts[role] == 0]
    role_groups = {
        role: len({group_ids[row["event_id"]] for row in rows if row["role"] == role})
        for role in ordered_roles
    }
    for role in ordered_roles:
        if counts[role] and role_groups[role] < 2:
            blockers.append(f"{role} has fewer than two independent groups")
    for event_id, group in group_ids.items():
        if group in exposed_groups:
            exposure_by_event[event_id] = exposed_groups[group]
    combined_blockers = [
        f"{row['event_id']}: {exposure_by_event[row['event_id']]}"
        for row in rows
        if row["role"] in {"VALIDATION", "CALIBRATION", "TEST"}
        and exposure_by_event[row["event_id"]] != "YOLOX_NOT_EXPOSED_VERIFIED"
    ]
    return {
        "status": "DRAFT" if not blockers else "BLOCKED_DATA",
        "counts": dict(counts),
        "group_counts": role_groups,
        "blockers": blockers,
        "yolox_exposure_by_event": exposure_by_event,
        "yolox_manifest_sha256": manifest_hashes,
        "combined_model_evaluation": "BLOCKED_EXPOSURE" if combined_blockers else "READY",
        "combined_model_blockers": combined_blockers,
        "training_authorized": False,
    }


def generate_split_plan(
    export: dict[str, Any],
    *,
    seed: int,
    temporal: bool = True,
    include_holdouts: bool = False,
) -> dict[str, Any]:
    """Draft group splits; default to TRAIN/VALIDATION without tiny holdouts."""
    checked = validate_ground_truth_export(export)
    if type(seed) is not int:
        raise TabularExportError("split seed must be an integer")
    rows = export["rows"]
    for row in rows:
        for name in ("scene_group_id", "duplicate_group_id", "sequence_group_id"):
            if not isinstance(row.get(name), str) or row[name] in {"", "UNCONFIRMED"}:
                raise TabularExportError(f"unknown {name}")
    links = [
        SimpleNamespace(
            **{
                key: row.get(key)
                for key in (
                    "event_id",
                    "road_segment_id",
                    "capture_ids",
                    "scene_group_id",
                    "duplicate_group_id",
                    "sequence_group_id",
                )
            }
        )
        for row in rows
    ]
    groups = leakage_groups(links)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(groups[row["event_id"]], []).append(row)
    minimum_groups = 20 if include_holdouts else MIN_INDEPENDENT_GROUPS
    if len(buckets) < minimum_groups:
        return {
            "status": "BLOCKED_DATA",
            "rows": [],
            "dataset_sha256": checked["content_sha256"],
            "blockers": [f"fewer than {minimum_groups} independent groups"],
            "training_authorized": False,
        }
    if temporal:
        ordered = sorted(
            buckets,
            key=lambda group: (
                min(datetime.fromisoformat(row["occurred_at"]) for row in buckets[group]),
                group,
            ),
        )
    else:
        ordered = sorted(
            buckets, key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()
        )
    n = len(ordered)
    train_end = int(n * (0.65 if include_holdouts else 0.7))
    validation_end = max(train_end + 2, int(n * 0.8)) if include_holdouts else n
    calibration_end = n - 2 if include_holdouts else n
    assigned: dict[str, str] = {}
    for index, group in enumerate(ordered):
        role = (
            "TRAIN"
            if index < train_end
            else "VALIDATION"
            if index < validation_end
            else "CALIBRATION"
            if index < calibration_end
            else "TEST"
        )
        assigned[group] = role
    plan_rows = [
        {
            "event_id": row["event_id"],
            "capture_ids": row["capture_ids"],
            "road_segment_id": row.get("road_segment_id"),
            "scene_group_id": row["scene_group_id"],
            "duplicate_group_id": row["duplicate_group_id"],
            "sequence_group_id": row["sequence_group_id"],
            "observed_at": row["occurred_at"],
            "label_at": row["label_at"],
            "yolox_exposure": "YOLOX_EXPOSURE_UNKNOWN",
            "visual_source_fingerprints": row.get("visual_source_fingerprints") or [],
            "snapshot_id": row.get("snapshot_id"),
            "snapshot_sha256": row.get("snapshot_sha256"),
            "role": assigned[groups[row["event_id"]]],
        }
        for row in rows
    ]
    plan = {
        "status": "DRAFT",
        "rows": plan_rows,
        "dataset_sha256": checked["content_sha256"],
        "seed": seed,
        "temporal": temporal,
        "include_holdouts": include_holdouts,
        "blockers": ["YOLOX exposure and independent scene groups require verification"],
        "training_authorized": False,
    }
    checked_plan = validate_split_plan(plan)
    if checked_plan["status"] == "BLOCKED_DATA":
        return {**plan, "status": "BLOCKED_DATA", "rows": [], "blockers": checked_plan["blockers"]}
    return plan


def feature_schema_sha256() -> str:
    """Bind ordered numeric feature paths to the extractor version."""
    payload = [
        PINNED_FEATURE_SCHEMA,
        TABULAR_EXTRACTOR_VERSION,
        VISUAL_LINEAGE_VERSION,
        [(name, path) for name, (path, _) in FEATURE_COLUMNS.items()],
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def label_schema_sha256() -> str:
    """Pin the target definitions and annotation policy used by a run."""
    payload = json.dumps(labeling_protocol(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def external_severity_label_schema_sha256() -> str:
    """Attain H/L is visual distress level, not UrMind RiskAssessment severity."""
    contract = {
        "target": EXTERNAL_SEVERITY_TARGET,
        "version": EXTERNAL_SEVERITY_TARGET_VERSION,
        "source": EXTERNAL_SEVERITY_SOURCE_VERSION,
        "unit": "one annotated pavement damage object",
        "labels": {"LOW": 0, "HIGH": 1},
        "source_types": {"alligator crack": "URMIND_ROAD_D20", "pothole": "URMIND_ROAD_D40"},
        "unsupported": ["linear crack: D00/D10 orientation unknown", "all other Attain classes"],
        "box_origin": "human_annotation_proxy",
        "urmind_risk_assessment_severity_equivalence": False,
    }
    payload = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def external_annotation_content_sha256(document: dict[str, Any]) -> str:
    """Pin feature/label rows and their exact source evidence, excluding the name itself."""
    keys = (
        "source_kind",
        "source_dataset",
        "source_version",
        "source_url",
        "license_reference",
        "target",
        "target_version",
        "feature_schema_version",
        "feature_schema_sha256",
        "label_schema_sha256",
        "feature_order",
        "annotation_manifest_sha256",
        "image_verification_sha256",
        "rows",
    )
    payload = {key: document.get(key) for key in keys}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_external_annotation_export(document: dict[str, Any]) -> dict[str, Any]:
    """Validate an Attain per-object DatasetVersion without invented UrMind Events."""
    if not isinstance(document, dict) or any(
        document.get(key) != value
        for key, value in {
            "source_kind": "EXTERNAL_ANNOTATION",
            "source_dataset": "Attain",
            "source_version": EXTERNAL_SEVERITY_SOURCE_VERSION,
            "source_url": "https://data.mendeley.com/datasets/nykrzdm74f/1",
            "license_reference": "CC BY 4.0; https://data.mendeley.com/datasets/nykrzdm74f/1",
            "target": EXTERNAL_SEVERITY_TARGET,
            "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
            "feature_schema_version": SEVERITY_DETECTION_SCHEMA_VERSION,
            "feature_schema_sha256": severity_detection_schema_sha256(),
            "label_schema_sha256": external_severity_label_schema_sha256(),
        }.items()
    ):
        raise TabularExportError("external visual severity contract mismatch")
    feature_order = document.get("feature_order")
    if not isinstance(feature_order, list) or tuple(feature_order) != SEVERITY_DETECTION_FEATURE_ORDER:
        raise TabularExportError("external severity feature order mismatch")
    for name in ("annotation_manifest_sha256", "image_verification_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(document.get(name, ""))):
            raise TabularExportError(f"external source fingerprint missing: {name}")
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) < MIN_LABELED_EXAMPLES:
        raise TabularExportError("external severity has too few labeled objects")
    seen: set[str] = set()
    image_identity: dict[str, tuple[str, str]] = {}
    counts: Counter[str] = Counter()
    subsets: Counter[str] = Counter()
    group_hashes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or any(
            name in row
            for name in (
                "event_id",
                "capture_id",
                "review_id",
                "road_segment_id",
                "rain_mm_24h",
                "priority",
                "risk",
            )
        ):
            raise TabularExportError("external annotation contains invented Event or target field")
        image_id, row_id = row.get("image_id"), row.get("row_id")
        index = row.get("annotation_index")
        if (
            not isinstance(image_id, str)
            or not image_id.endswith(".jpg")
            or type(index) is not int
            or index < 0
            or row_id != f"{image_id}#{index}"
            or row_id in seen
        ):
            raise TabularExportError("external annotation identity missing or duplicated")
        if row.get("source_sample_id") != image_id or any(
            not isinstance(row.get(name), str) or not row[name]
            for name in ("image_file_id", "annotation_file_id")
        ):
            raise TabularExportError("external source sample/file identity missing")
        seen.add(row_id)
        subset = row.get("source_subset")
        if subset not in {"ws_v1", "ws_v2"}:
            raise TabularExportError("unsupported Attain severity subset")
        if not image_id.startswith("Attain_SMP_WS_v1_" if subset == "ws_v1" else "Attain_SMP_WS_v2_"):
            raise TabularExportError("Attain image/subset mismatch")
        image_hash, annotation_hash = row.get("image_sha256"), row.get("annotation_sha256")
        if not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (image_hash, annotation_hash)
        ):
            raise TabularExportError("external image/annotation fingerprint missing")
        assert isinstance(image_hash, str) and isinstance(annotation_hash, str)
        prior_identity = image_identity.setdefault(image_id, (image_hash, annotation_hash))
        if prior_identity != (image_hash, annotation_hash):
            raise TabularExportError("one Attain image has conflicting file hashes")
        if row.get("group_id") != image_hash or row.get("scene_group_status") != "UNKNOWN":
            raise TabularExportError("external image group or scene uncertainty forged")
        group_hashes.add(image_hash)
        if row.get("box_origin") != "human_annotation_proxy" or row.get("yolox_exposure") != "YOLOX_EXPOSURE_UNKNOWN":
            raise TabularExportError("external visual lineage falsely claims detector independence")
        if row.get("captured_at") is not None or row.get("label_created_at") is not None:
            raise TabularExportError("unknown Attain timestamps must not be invented")
        if (
            row.get("vision_model_version") is not None
            or row.get("vision_checkpoint_hash") is not None
            or row.get("vision_feature_version") != SEVERITY_DETECTION_SCHEMA_VERSION
        ):
            raise TabularExportError("external proxy box cannot claim YOLOX lineage")
        if row.get("missingness") != {
            "capture_time_unknown": True,
            "label_time_unknown": True,
            "context_unavailable": True,
            "detector_confidence_unavailable": True,
        }:
            raise TabularExportError("external missingness contract mismatch")
        try:
            computed = datetime.fromisoformat(row["feature_computed_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TabularExportError("external feature computation time invalid") from exc
        if computed.tzinfo is None or computed.utcoffset() is None:
            raise TabularExportError("external feature computation time needs timezone")
        source_type, _, severity = str(row.get("label_original", "")).rpartition("-")
        source_type, severity = source_type.strip().lower(), severity.strip().upper()
        expected_class = {"alligator crack": "D20", "pothole": "D40"}.get(source_type)
        if (
            expected_class is None
            or severity not in {"LOW", "HIGH"}
            or row.get("target") != (severity == "HIGH")
            or type(row.get("target")) is not int
            or row.get("source_type") != source_type
        ):
            raise TabularExportError("external severity label/value mismatch")
        features = row.get("features")
        if not isinstance(features, dict) or tuple(features) != SEVERITY_DETECTION_FEATURE_ORDER:
            raise TabularExportError("external severity feature order or keys invalid")
        if any(
            type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1
            for value in features.values()
        ):
            raise TabularExportError("external severity feature type/range invalid")
        if (
            features["detected_class_D20"] != float(expected_class == "D20")
            or features["detected_class_D40"] != float(expected_class == "D40")
            or not math.isclose(
                features["bbox_area_ratio"],
                features["bbox_width_ratio"] * features["bbox_height_ratio"],
                abs_tol=1e-8,
            )
        ):
            raise TabularExportError("external severity feature/label class or area mismatch")
        box = row.get("bbox")
        if (
            not isinstance(box, dict)
            or set(box) != {"x", "y", "width", "height"}
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in box.values())
            or not (0 <= box["x"] < 1 and 0 <= box["y"] < 1)
            or not (0 < box["width"] <= 1 and 0 < box["height"] <= 1)
            or box["x"] + box["width"] > 1.000001
            or box["y"] + box["height"] > 1.000001
            or type(row.get("image_width")) is not int
            or type(row.get("image_height")) is not int
            or min(row["image_width"], row["image_height"]) <= 0
            or any(
                not math.isclose(features[name], value, abs_tol=1e-8)
                for name, value in (
                    ("bbox_width_ratio", box["width"]),
                    ("bbox_height_ratio", box["height"]),
                    ("bbox_center_y_ratio", box["y"] + box["height"] / 2),
                )
            )
        ):
            raise TabularExportError("external bbox provenance or derived geometry invalid")
        counts[f"{expected_class}:{severity}"] += 1
        subsets[subset] += 1
    digest = external_annotation_content_sha256(document)
    dataset = document.get("dataset")
    if (
        not isinstance(dataset, dict)
        or dataset.get("name") != f"attain-pavement-visual-severity-{digest[:16]}"
        or dataset.get("content_sha256") != digest
        or dataset.get("status") != "EXPERIMENTAL_DRAFT"
    ):
        raise TabularExportError("external DatasetVersion name/hash/status invalid")
    if any(counts.get(f"{code}:{severity}", 0) < MIN_EXAMPLES_PER_TARGET_VALUE for code in ("D20", "D40") for severity in ("LOW", "HIGH")):
        raise TabularExportError("external severity class support below draft floor")
    return {
        "status": "SOFTWARE_VALIDATED_EXPERIMENTAL",
        "rows": len(rows),
        "source_images": len(image_identity),
        "exact_image_groups": len(group_hashes),
        "class_counts": dict(sorted(counts.items())),
        "subset_counts": dict(sorted(subsets.items())),
        "content_sha256": digest,
        "scientific_validation": False,
        "combined_model_evaluation": "BLOCKED_EXPOSURE",
    }


def external_split_content_sha256(document: dict[str, Any]) -> str:
    payload = {
        "dataset_sha256": document.get("dataset_sha256"),
        "strategy": document.get("strategy"),
        "rows": document.get("rows"),
    }
    if "similarity_audit_sha256" in document:
        payload["similarity_audit_sha256"] = document["similarity_audit_sha256"]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def external_similarity_audit_content_sha256(document: dict[str, Any]) -> str:
    """Bind the near-duplicate evidence to its source bytes and image identities."""
    keys = (
        "dataset_sha256",
        "dataset_version",
        "annotation_manifest_sha256",
        "image_verification_sha256",
        "method",
        "pillow_version",
        "max_distance",
        "images",
        "pairs",
    )
    payload = {key: document.get(key) for key in keys}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_external_similarity_audit(
    document: dict[str, Any], export: dict[str, Any]
) -> dict[str, Any]:
    """Check every eligible image and recompute all cross-subset dHash pairs."""
    checked = validate_external_annotation_export(export)
    if not isinstance(document, dict) or any(
        document.get(key) != value
        for key, value in {
            "dataset_sha256": checked["content_sha256"],
            "dataset_version": export["dataset"]["name"],
            "annotation_manifest_sha256": export["annotation_manifest_sha256"],
            "image_verification_sha256": export["image_verification_sha256"],
            "method": EXTERNAL_SIMILARITY_METHOD,
            "max_distance": EXTERNAL_SIMILARITY_MAX_DISTANCE,
        }.items()
    ):
        raise TabularExportError("external similarity audit source or method invalid")
    if not isinstance(document.get("pillow_version"), str) or not document["pillow_version"]:
        raise TabularExportError("external similarity audit extractor version missing")
    expected_images = {
        row["image_id"]: (row["source_subset"], row["image_sha256"])
        for row in export["rows"]
    }
    images = document.get("images")
    if not isinstance(images, list) or len(images) != len(expected_images):
        raise TabularExportError("external similarity audit image inventory incomplete")
    by_subset: dict[str, list[tuple[str, int]]] = {"ws_v1": [], "ws_v2": []}
    image_ids: list[str] = []
    for item in images:
        if not isinstance(item, dict):
            raise TabularExportError("external similarity audit image malformed")
        image_id = item.get("image_id")
        if not isinstance(image_id, str) or expected_images.get(image_id) != (
            item.get("source_subset"),
            item.get("image_sha256"),
        ):
            raise TabularExportError("external similarity audit image fingerprint mismatch")
        digest = item.get("dhash64")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{16}", digest):
            raise TabularExportError("external similarity audit dHash invalid")
        by_subset[item["source_subset"]].append((image_id, int(digest, 16)))
        image_ids.append(image_id)
    if len(set(image_ids)) != len(expected_images) or image_ids != sorted(image_ids):
        raise TabularExportError("external similarity audit image order or identity invalid")
    expected_pairs = [
        {"ws_v1_image_id": left, "ws_v2_image_id": right, "distance": (a ^ b).bit_count()}
        for left, a in by_subset["ws_v1"]
        for right, b in by_subset["ws_v2"]
        if (a ^ b).bit_count() <= EXTERNAL_SIMILARITY_MAX_DISTANCE
    ]
    if document.get("pairs") != expected_pairs:
        raise TabularExportError("external similarity audit candidate pairs incomplete")
    if document.get("content_sha256") != external_similarity_audit_content_sha256(document):
        raise TabularExportError("external similarity audit content hash mismatch")
    candidates = {
        image_id
        for pair in expected_pairs
        for image_id in (pair["ws_v1_image_id"], pair["ws_v2_image_id"])
    }
    return {
        "status": "CANDIDATE_NEAR_DUPLICATES_QUARANTINED",
        "pair_count": len(expected_pairs),
        "candidate_image_count": len(candidates),
        "candidate_image_ids": candidates,
        "scene_independence_verified": False,
    }


def generate_external_split_plan(
    export: dict[str, Any],
    *,
    similarity_audit: dict[str, Any] | None = None,
    allow_historical_split: bool = False,
) -> dict[str, Any]:
    """Version holdout for internal early stopping; quarantine near matches when audited."""
    if similarity_audit is None and not allow_historical_split:
        raise TabularExportError("external similarity audit required for new split")
    checked = validate_external_annotation_export(export)
    audit_check = (
        validate_external_similarity_audit(similarity_audit, export)
        if similarity_audit is not None
        else None
    )
    excluded = audit_check["candidate_image_ids"] if audit_check else set()
    rows = [
        {
            "row_id": row["row_id"],
            "image_sha256": row["image_sha256"],
            "annotation_sha256": row["annotation_sha256"],
            "source_subset": row["source_subset"],
            "role": (
                "EXCLUDED"
                if row["image_id"] in excluded
                else "TRAIN"
                if row["source_subset"] == "ws_v2"
                else "VALIDATION"
            ),
        }
        for row in export["rows"]
    ]
    split = {
        "dataset_sha256": checked["content_sha256"],
        "strategy": (
            "WS_V2_TRAIN_WS_V1_VALIDATION_DHASH12_QUARANTINE"
            if audit_check
            else "WS_V2_TRAIN_WS_V1_VALIDATION"
        ),
        "rows": rows,
        "scientific_validation": False,
        "temporal_order_verified": False,
        "scene_group_status": "UNKNOWN",
        "combined_model_evaluation": "BLOCKED_EXPOSURE",
    }
    if audit_check and similarity_audit is not None:
        split["similarity_audit_sha256"] = similarity_audit["content_sha256"]
    split["content_sha256"] = external_split_content_sha256(split)
    validate_external_split_plan(split, export, similarity_audit=similarity_audit)
    return split


def validate_external_split_plan(
    split: dict[str, Any],
    export: dict[str, Any],
    *,
    similarity_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove exact image groups do not cross fitting/early-stopping roles."""
    if not isinstance(split, dict):
        raise TabularExportError("external split must be a JSON object")
    checked = validate_external_annotation_export(export)
    audit_check = (
        validate_external_similarity_audit(similarity_audit, export)
        if similarity_audit is not None
        else None
    )
    quarantined = audit_check["candidate_image_ids"] if audit_check else set()
    audited_strategy = split.get("strategy") == "WS_V2_TRAIN_WS_V1_VALIDATION_DHASH12_QUARANTINE"
    if audited_strategy != (audit_check is not None):
        raise TabularExportError("external split requires matching similarity audit")
    if not audited_strategy and "similarity_audit_sha256" in split:
        raise TabularExportError("historical split cannot claim similarity evidence")
    if (
        split.get("dataset_sha256") != checked["content_sha256"]
        or split.get("strategy")
        not in {
            "WS_V2_TRAIN_WS_V1_VALIDATION",
            "WS_V2_TRAIN_WS_V1_VALIDATION_DHASH12_QUARANTINE",
        }
        or (
            split.get("similarity_audit_sha256") if audited_strategy else None
        ) != (similarity_audit["content_sha256"] if similarity_audit is not None else None)
        or split.get("scientific_validation") is not False
        or split.get("temporal_order_verified") is not False
        or split.get("scene_group_status") != "UNKNOWN"
        or split.get("combined_model_evaluation") != "BLOCKED_EXPOSURE"
        or split.get("content_sha256") != external_split_content_sha256(split)
    ):
        raise TabularExportError("external split identity or scientific limits invalid")
    rows = split.get("rows")
    if not isinstance(rows, list) or len(rows) != len(export["rows"]):
        raise TabularExportError("external split membership incomplete")
    by_id = {row["row_id"]: row for row in export["rows"]}
    seen: set[str] = set()
    roles_by_image: dict[str, str] = {}
    counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    type_target_counts: Counter[str] = Counter()
    for member in rows:
        if not isinstance(member, dict) or member.get("row_id") not in by_id:
            raise TabularExportError("external split contains unknown row")
        row = by_id[member["row_id"]]
        role = (
            "EXCLUDED"
            if row["image_id"] in quarantined
            else "TRAIN"
            if row["source_subset"] == "ws_v2"
            else "VALIDATION"
        )
        if (
            member["row_id"] in seen
            or member.get("role") != role
            or member.get("image_sha256") != row["image_sha256"]
            or member.get("annotation_sha256") != row["annotation_sha256"]
            or member.get("source_subset") != row["source_subset"]
        ):
            raise TabularExportError("external split role or row fingerprint forged")
        seen.add(member["row_id"])
        image_hash = row["image_sha256"]
        if image_hash in roles_by_image and roles_by_image[image_hash] != role:
            raise TabularExportError("external image hash crosses TRAIN/VALIDATION")
        roles_by_image[image_hash] = role
        counts[role] += 1
        if role != "EXCLUDED":
            target_counts[f"{role}:{row['target']}"] += 1
            type_target_counts[f"{role}:{row['source_type']}:{row['target']}"] += 1
    if len(seen) != len(by_id) or any(
        target_counts.get(f"{role}:{value}", 0) < 20
        for role in ("TRAIN", "VALIDATION")
        for value in (0, 1)
    ):
        raise TabularExportError("external split lacks rows or target-class support")
    if any(
        type_target_counts.get(f"{role}:{kind}:{value}", 0) < 10
        for role in ("TRAIN", "VALIDATION")
        for kind in ("alligator crack", "pothole")
        for value in (0, 1)
    ):
        raise TabularExportError("external split lacks per-damage class support")
    return {
        "status": "INTERNAL_EARLY_STOPPING_ONLY",
        "counts": dict(sorted(counts.items())),
        "target_counts": dict(sorted(target_counts.items())),
        "type_target_counts": dict(sorted(type_target_counts.items())),
        "exact_image_group_counts": {
            role: sum(owner == role for owner in roles_by_image.values())
            for role in ("TRAIN", "VALIDATION")
        },
        "exact_image_overlap": 0,
        "near_duplicate_cross_role_candidates": 0 if audit_check else "NOT_VERIFIED",
        "similarity_audit_sha256": (
            similarity_audit["content_sha256"] if similarity_audit is not None else None
        ),
        "scene_group_overlap": "NOT_VERIFIED",
        "temporal_order_verified": False,
        "scientific_validation": False,
    }


def _external_domain_shift_summary(
    export: dict[str, Any], split: dict[str, Any]
) -> dict[str, Any]:
    """Describe image and annotation input roles without labels or fitting."""
    roles = {member["row_id"]: member["role"] for member in split["rows"]}
    rows_by_role: dict[str, Counter[str]] = {
        "TRAIN": Counter(),
        "VALIDATION": Counter(),
    }
    protocol_rows_by_role: dict[str, Counter[str]] = {
        "TRAIN": Counter(),
        "VALIDATION": Counter(),
    }
    groups_by_role: dict[str, dict[str, set[str]]] = {
        "TRAIN": {},
        "VALIDATION": {},
    }
    feature_totals: dict[str, dict[str, float]] = {
        role: {name: 0.0 for name in SEVERITY_DETECTION_FEATURE_ORDER}
        for role in rows_by_role
    }
    for row in export["rows"]:
        role = roles[row["row_id"]]
        if role not in rows_by_role:
            continue
        shape = f"{row['image_width']}x{row['image_height']}"
        rows_by_role[role][shape] += 1
        try:
            protocol = EXTERNAL_ANNOTATION_PROTOCOL_BY_SUBSET[row["source_subset"]]
        except KeyError as exc:
            raise TabularExportError("external annotation subset protocol unknown") from exc
        protocol_rows_by_role[role][protocol] += 1
        groups_by_role[role].setdefault(shape, set()).add(row["image_sha256"])
        for name in SEVERITY_DETECTION_FEATURE_ORDER:
            feature_totals[role][name] += float(row["features"][name])
    if not all(sum(counts.values()) for counts in rows_by_role.values()):
        raise TabularExportError("external domain summary requires both split roles")
    proportions = {
        role: {
            shape: count / sum(counts.values())
            for shape, count in counts.items()
        }
        for role, counts in rows_by_role.items()
    }
    shapes = set(proportions["TRAIN"]) | set(proportions["VALIDATION"])
    total_variation = 0.5 * sum(
        abs(proportions["TRAIN"].get(shape, 0.0) - proportions["VALIDATION"].get(shape, 0.0))
        for shape in shapes
    )
    protocol_proportions = {
        role: {
            protocol: count / sum(counts.values())
            for protocol, count in counts.items()
        }
        for role, counts in protocol_rows_by_role.items()
    }
    protocols = set(protocol_proportions["TRAIN"]) | set(protocol_proportions["VALIDATION"])
    protocol_total_variation = 0.5 * sum(
        abs(
            protocol_proportions["TRAIN"].get(protocol, 0.0)
            - protocol_proportions["VALIDATION"].get(protocol, 0.0)
        )
        for protocol in protocols
    )
    feature_means = {
        role: {
            name: total / sum(rows_by_role[role].values())
            for name, total in feature_totals[role].items()
        }
        for role in rows_by_role
    }
    return {
        "status": "DESCRIPTIVE_ONLY",
        "image_dimension_rows": {
            role: dict(sorted(counts.items())) for role, counts in rows_by_role.items()
        },
        "image_dimension_groups": {
            role: {shape: len(groups) for shape, groups in sorted(by_shape.items())}
            for role, by_shape in groups_by_role.items()
        },
        "image_dimension_total_variation": total_variation,
        "source_annotation_protocol_rows": {
            role: dict(sorted(counts.items())) for role, counts in protocol_rows_by_role.items()
        },
        "annotation_protocol_total_variation": protocol_total_variation,
        "feature_means": feature_means,
        "labels_accessed": False,
        "causal_explanation": False,
    }


def _external_similarity_boundary_summary(
    split: dict[str, Any], similarity_audit: dict[str, Any] | None
) -> dict[str, Any]:
    """Show candidates just beyond the dHash quarantine, without calling them scenes."""
    if similarity_audit is None:
        raise TabularExportError("external similarity boundary requires an audit")
    cutoff = similarity_audit.get("max_distance")
    if type(cutoff) is not int or not 0 <= cutoff < 64:
        raise TabularExportError("external similarity cutoff invalid")
    roles_by_image: dict[str, str] = {}
    for member in split["rows"]:
        image_id = member["row_id"].split("#", 1)[0]
        role = member["role"]
        if image_id in roles_by_image and roles_by_image[image_id] != role:
            raise TabularExportError("external image crosses split roles")
        roles_by_image[image_id] = role
    hashes: dict[str, list[int]] = {"TRAIN": [], "VALIDATION": []}
    for item in similarity_audit["images"]:
        role = roles_by_image.get(item["image_id"])
        if role in hashes:
            hashes[role].append(int(item["dhash64"], 16))
    if not hashes["TRAIN"] or not hashes["VALIDATION"]:
        raise TabularExportError("external similarity boundary requires both split roles")
    nearest = 64
    next_distance_pairs = 0
    for train_hash in hashes["TRAIN"]:
        for validation_hash in hashes["VALIDATION"]:
            distance = (train_hash ^ validation_hash).bit_count()
            nearest = min(nearest, distance)
            next_distance_pairs += distance == cutoff + 1
    return {
        "status": "DESCRIPTIVE_ONLY",
        "quarantine_cutoff": cutoff,
        "nearest_cross_role_distance": nearest,
        "pairs_at_cutoff_plus_one": next_distance_pairs,
        "scene_independence_verified": False,
    }


def preflight_xgboost(
    export: dict[str, Any] | None,
    splits: dict[str, Any] | None,
    config: dict[str, Any],
    *,
    yolox_registry: Path | None = None,
    yolox_manifests: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Light, read-only gate. Does not import a learner or inspect TEST rows."""
    target = config.get("target")
    if target not in labeling_protocol()["targets"]:
        raise TabularExportError("unknown tabular target")
    if not isinstance(config.get("target_version"), str) or not config["target_version"]:
        raise TabularExportError("target version required")
    if config.get("feature_schema_sha256") != feature_schema_sha256():
        raise TabularExportError("feature schema hash mismatch")
    if (
        config.get("device") != "cpu"
        or config.get("tree_method") != "hist"
        or config.get("n_jobs") != 1
        or config.get("nthread") != 1
        or config.get("parallel_search") is not False
    ):
        raise TabularExportError("XGBoost must use cpu, hist, one thread and no parallel search")
    for name in ("seed", "n_estimators", "early_stopping_rounds"):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise TabularExportError(f"positive integer {name} required")
    if config["early_stopping_rounds"] >= config["n_estimators"]:
        raise TabularExportError("early stopping must be shorter than tree budget")
    if (
        type(config.get("max_wall_time_seconds")) is not int
        or not 0 < config["max_wall_time_seconds"] <= 36000
    ):
        raise TabularExportError("XGBoost wall time must be at most 10 hours")
    if config.get("eval_metric") != "logloss" or config.get("metric_direction") != "minimize":
        raise TabularExportError("binary validation metric and direction required")
    if (
        type(config.get("max_depth")) is not int
        or not 1 <= config["max_depth"] <= 8
        or type(config.get("max_bin")) is not int
        or not 16 <= config["max_bin"] <= 256
    ):
        raise TabularExportError("bounded max_depth and max_bin required")
    for name in ("min_start_available_mb", "critical_available_mb", "monitor_every_iterations"):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise TabularExportError(f"positive integer {name} required")
    if config["critical_available_mb"] >= config["min_start_available_mb"]:
        raise TabularExportError("critical RAM threshold must be below start threshold")
    blockers: list[str] = []
    if target != TARGET_NAME:
        blockers.append("TARGET_APPROVAL_PENDING: target rubric and outcome contract absent")
    export_check = validate_ground_truth_export(export) if export is not None else None
    split_check = (
        validate_split_plan(splits, yolox_registry=yolox_registry, yolox_manifests=yolox_manifests)
        if splits is not None
        else None
    )
    if export_check is None or export is None:
        blockers.append("tabular export not supplied; LABEL_COUNT=NOT_VERIFIED")
    else:
        if export_check["target_version"] != config["target_version"]:
            raise TabularExportError("target version differs from export")
        if not export.get("dataset"):
            blockers.append("DatasetVersion metadata missing")
        blockers.extend(export_check["readiness"]["blockers"])
        if export_check["history_coverage_unknown"]:
            blockers.append("historical coverage unknown")
        if any(row.get("use_authorized") is not True for row in export["rows"]):
            blockers.append("row-level use authorization missing")
        if any(
            export_check[key] != export_check["rows"]
            for key in (
                "visual_preprocessing_known",
                "visual_postprocessing_known",
                "vision_checkpoint_known",
                "vision_class_order_known",
                "vision_feature_version_known",
            )
        ):
            blockers.append("visual model and feature lineage missing")
    if split_check is None or splits is None:
        blockers.append("split plan not supplied")
    else:
        blockers.extend(split_check["blockers"])
        if splits.get("temporal") is not True:
            blockers.append("temporal split not verified")
    target_counts_by_role: dict[str, dict[str, int]] | None = None
    if export_check is not None and split_check is not None and export is not None and splits is not None:
        if splits.get("dataset_sha256") != export_check["content_sha256"]:
            raise TabularExportError("split plan not bound to dataset hash")
        if {row["event_id"] for row in splits["rows"]} != {
            row["event_id"] for row in export["rows"]
        }:
            raise TabularExportError("split membership differs from export")
        role_by_event = {row["event_id"]: row["role"] for row in splits["rows"]}
        target_counts: dict[str, Counter[int]] = {
            role: Counter() for role in ("TRAIN", "VALIDATION")
        }
        for row in export["rows"]:
            role = role_by_event[row["event_id"]]
            if role in target_counts:
                target_counts[role][row["target"]] += 1
        target_counts_by_role = {
            role: {str(value): count for value, count in sorted(counts.items())}
            for role, counts in target_counts.items()
        }
        for role, counts in target_counts.items():
            if not all(counts[value] > 0 for value in (0, 1)):
                blockers.append(f"{role} lacks a target class")
    xgboost_available = importlib.util.find_spec("xgboost") is not None
    if not xgboost_available:
        blockers.append("EXECUTION_DEFERRED: xgboost is not installed in this Python")
    blockers.append("BLOCKED_AUTHORIZATION: registry-bound dataset approval not verified")
    return {
        "status": "TARGET_APPROVAL_PENDING"
        if target != TARGET_NAME
        else "BLOCKED_DATA"
        if export_check is None or split_check is None or blockers[:-1]
        else "BLOCKED_AUTHORIZATION",
        "target": target,
        "feature_schema_sha256": feature_schema_sha256(),
        "label_count": export_check["rows"] if export_check is not None else "NOT_VERIFIED",
        "split_counts": split_check["counts"] if split_check is not None else None,
        "target_counts_by_role": target_counts_by_role,
        "combined_model_evaluation": (
            split_check["combined_model_evaluation"]
            if split_check is not None
            else "BLOCKED_EXPOSURE"
        ),
        "xgboost_available": xgboost_available,
        "blockers": blockers,
        "fit_called": False,
        "training_authorized": False,
    }


def dry_run_xgboost(
    export: dict[str, Any] | None,
    splits: dict[str, Any] | None,
    config: dict[str, Any],
    *,
    yolox_registry: Path | None = None,
    yolox_manifests: dict[str, Path] | None = None,
) -> dict[str, Any]:
    result = preflight_xgboost(
        export,
        splits,
        config,
        yolox_registry=yolox_registry,
        yolox_manifests=yolox_manifests,
    )
    return {**result, "mode": "DRY_RUN", "artifact_written": False}


def load_verified_training_bundle(
    dataset_path: Path,
    split_path: Path,
    config_path: Path,
    approval_path: Path,
    registry_path: Path,
) -> VerifiedTrainingBundle:
    """Verify exact registered bytes before any model or resource-heavy import."""
    files = {
        "dataset": dataset_path,
        "split": split_path,
        "config": config_path,
        "approval": approval_path,
    }
    artifact_sha256 = {
        name: _registered_artifact(path, registry_path)[1] for name, path in files.items()
    }

    def read(path: Path) -> dict[str, Any]:
        document = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(document, dict):
            raise TabularExportError("training artifact must be a JSON object")
        return document

    export = read(dataset_path)
    split = read(split_path)
    config = read(config_path)
    approval = read(approval_path)
    checked = validate_ground_truth_export(export)
    dataset_version = (export.get("dataset") or {}).get("name")
    if (
        not isinstance(dataset_version, str)
        or not dataset_version
        or export["dataset"].get("content_sha256") != checked["content_sha256"]
    ):
        raise TabularExportError("DatasetVersion fingerprint missing or divergent")
    split_rows = split.get("rows")
    if not isinstance(split_rows, list):
        raise TabularExportError("split rows must be a list")
    if split.get("include_holdouts") is not False or any(
        row.get("role") in {"CALIBRATION", "TEST"} for row in split_rows
    ):
        raise TabularExportError("training bundle must exclude protected holdout rows")
    if type(split.get("seed")) is not int or split.get("temporal") is not True:
        raise TabularExportError("reproducible temporal split required")
    generated = generate_split_plan(export, seed=split["seed"], temporal=True)
    if generated["status"] != "DRAFT" or split.get("rows") != generated["rows"]:
        raise TabularExportError("split differs from reproducible grouped assignment")
    preflight = preflight_xgboost(export, split, config)
    if preflight["blockers"] != [
        "BLOCKED_AUTHORIZATION: registry-bound dataset approval not verified"
    ]:
        raise TabularExportError(f"BLOCKED_DATA: {preflight['blockers']}")
    required = {
        "status": "APPROVED_EXPERIMENTAL_TRAINING",
        "dataset_artifact_sha256": artifact_sha256["dataset"],
        "split_artifact_sha256": artifact_sha256["split"],
        "config_artifact_sha256": artifact_sha256["config"],
        "dataset_sha256": checked["content_sha256"],
        "dataset_version": dataset_version,
        "feature_schema_sha256": feature_schema_sha256(),
        "label_schema_sha256": label_schema_sha256(),
        "target": config["target"],
        "target_version": config["target_version"],
    }
    if any(approval.get(name) != value for name, value in required.items()):
        raise TabularExportError("BLOCKED_AUTHORIZATION: approval does not bind exact inputs")
    if not all(
        isinstance(approval.get(name), str) and approval[name].strip()
        for name in ("approved_by", "authorization_source", "approved_at")
    ):
        raise TabularExportError("BLOCKED_AUTHORIZATION: attributable approval missing")
    try:
        approved_at = datetime.fromisoformat(approval["approved_at"])
    except ValueError as exc:
        raise TabularExportError("BLOCKED_AUTHORIZATION: approval time invalid") from exc
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise TabularExportError("BLOCKED_AUTHORIZATION: approval time requires timezone")
    return VerifiedTrainingBundle(
        export=export,
        split=split,
        config=config,
        artifact_sha256=artifact_sha256,
        approval_source=approval["authorization_source"],
    )


def validate_external_training_config(config: dict[str, Any]) -> dict[str, Any]:
    """Separate visual distress target from UrMind's Event Review target."""
    expected = {
        "target": EXTERNAL_SEVERITY_TARGET,
        "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
        "feature_schema_sha256": severity_detection_schema_sha256(),
        "label_schema_sha256": external_severity_label_schema_sha256(),
        "device": "cpu",
        "tree_method": "hist",
        "n_jobs": 1,
        "nthread": 1,
        "parallel_search": False,
        "include_holdouts": False,
    }
    if not isinstance(config, dict) or any(config.get(k) != v for k, v in expected.items()):
        raise TabularExportError("external severity target, schema or CPU config invalid")
    if (config.get("eval_metric"), config.get("metric_direction")) not in {
        ("logloss", "minimize"),
        ("aucpr", "maximize"),
    }:
        raise TabularExportError("external severity validation metric/direction invalid")
    if config.get("class_weight_mode", "none") not in {"none", "balanced_from_train"}:
        raise TabularExportError("external severity class weighting mode invalid")
    for name in (
        "seed",
        "n_estimators",
        "early_stopping_rounds",
        "max_depth",
        "max_bin",
        "max_wall_time_seconds",
        "min_start_available_mb",
        "critical_available_mb",
        "monitor_every_iterations",
    ):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise TabularExportError(f"external severity config requires positive integer {name}")
    if (
        config["early_stopping_rounds"] >= config["n_estimators"]
        or config["max_depth"] > 8
        or not 16 <= config["max_bin"] <= 256
        or config["max_wall_time_seconds"] > 36000
        or config["critical_available_mb"] >= config["min_start_available_mb"]
    ):
        raise TabularExportError("external severity train budget or memory limits invalid")
    return {"status": "EXPERIMENTAL_CONFIG_VALID", "fit_called": False}


def preflight_external_xgboost(
    export: dict[str, Any],
    split: dict[str, Any],
    config: dict[str, Any],
    *,
    similarity_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if similarity_audit is None:
        raise TabularExportError("external similarity audit required before training")
    checked = validate_external_annotation_export(export)
    split_check = validate_external_split_plan(
        split, export, similarity_audit=similarity_audit
    )
    validate_external_training_config(config)
    available = importlib.util.find_spec("xgboost") is not None
    return {
        "status": "BLOCKED_GROUP_EVIDENCE",
        "target": EXTERNAL_SEVERITY_TARGET,
        "dataset_version": export["dataset"]["name"],
        "real_label_count": checked["rows"],
        "class_counts": checked["class_counts"],
        "label_mapping_status": EXTERNAL_LABEL_MAPPING_STATUS,
        "split_counts": split_check["counts"],
        "domain_shift": _external_domain_shift_summary(export, split),
        "similarity_boundary": _external_similarity_boundary_summary(split, similarity_audit),
        "exact_image_overlap": 0,
        "near_duplicate_cross_role_candidates": split_check[
            "near_duplicate_cross_role_candidates"
        ],
        "scene_group_status": "UNKNOWN",
        "temporal_order_verified": False,
        "combined_model_evaluation": "BLOCKED_EXPOSURE",
        "scientific_validation": False,
        "xgboost_available": available,
        "fit_called": False,
    }


def load_external_training_bundle(
    dataset_path: Path,
    split_path: Path,
    config_path: Path,
    approval_path: Path,
    source_manifest_path: Path,
    image_verification_path: Path,
    similarity_audit_path: Path | None = None,
) -> VerifiedTrainingBundle:
    """Bind external labels, files, split and user authorization before importing XGB."""
    if similarity_audit_path is None:
        raise TabularExportError("external similarity audit required before training/evaluation")
    repo_root = Path(__file__).resolve().parents[3]
    files = {
        "dataset": dataset_path,
        "split": split_path,
        "config": config_path,
        "approval": approval_path,
        "source_manifest": source_manifest_path,
        "image_verification": image_verification_path,
        "similarity_audit": similarity_audit_path,
    }
    if any(not path.resolve().is_relative_to(repo_root) for path in files.values()):
        raise TabularExportError("external training inputs must remain in isolated worktree")
    artifact_sha256 = {name: _file_sha256(path) for name, path in files.items()}
    documents = {
        name: json.loads(path.read_text(encoding="utf8")) for name, path in files.items()
    }
    if any(not isinstance(value, dict) for value in documents.values()):
        raise TabularExportError("external training artifact must be a JSON object")
    export, split, config, approval = (
        documents[name] for name in ("dataset", "split", "config", "approval")
    )
    similarity_audit = documents["similarity_audit"]
    checked = validate_external_annotation_export(export)
    validate_external_split_plan(split, export, similarity_audit=similarity_audit)
    validate_external_training_config(config)
    manifest, image_evidence = (
        documents[name] for name in ("source_manifest", "image_verification")
    )
    if (
        export["annotation_manifest_sha256"] != artifact_sha256["source_manifest"]
        or export["image_verification_sha256"] != artifact_sha256["image_verification"]
        or manifest.get("source_version") != EXTERNAL_SEVERITY_SOURCE_VERSION
        or manifest.get("license") != "CC BY 4.0"
        or image_evidence.get("source_version") != EXTERNAL_SEVERITY_SOURCE_VERSION
        or image_evidence.get("annotation_manifest_sha256")
        != artifact_sha256["source_manifest"]
        or image_evidence.get("verified_images") != 1656
    ):
        raise TabularExportError("external source manifest or image evidence not verified")
    source_rows = manifest.get("rows")
    if not isinstance(source_rows, list) or len(source_rows) != 1656:
        raise TabularExportError("external source manifest lacks published sample inventory")
    by_image = {row.get("image_id"): row for row in source_rows if isinstance(row, dict)}
    if len(by_image) != 1656:
        raise TabularExportError("external source manifest image identities duplicated")
    for row in export["rows"]:
        source = by_image.get(row["image_id"])
        if source is None or any(
            row.get(key) != source.get(source_key)
            for key, source_key in (
                ("source_subset", "subset"),
                ("image_file_id", "image_file_id"),
                ("annotation_file_id", "annotation_file_id"),
                ("image_sha256", "image_sha256_official"),
                ("annotation_sha256", "annotation_sha256"),
            )
        ):
            raise TabularExportError("external row not traceable to official source manifest")
    required = {
        "status": "APPROVED_EXPERIMENTAL_TRAINING",
        "dataset_artifact_sha256": artifact_sha256["dataset"],
        "split_artifact_sha256": artifact_sha256["split"],
        "config_artifact_sha256": artifact_sha256["config"],
        "source_manifest_sha256": artifact_sha256["source_manifest"],
        "image_verification_sha256": artifact_sha256["image_verification"],
        "similarity_audit_sha256": artifact_sha256["similarity_audit"],
        "dataset_sha256": checked["content_sha256"],
        "dataset_version": export["dataset"]["name"],
        "split_sha256": split["content_sha256"],
        "feature_schema_sha256": severity_detection_schema_sha256(),
        "label_schema_sha256": external_severity_label_schema_sha256(),
        "target": EXTERNAL_SEVERITY_TARGET,
        "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
        "scope": "experimental_visual_severity_only",
        "scientific_validation_approved": False,
        "model_promotion_approved": False,
    }
    if any(approval.get(name) != value for name, value in required.items()):
        raise TabularExportError("external training authorization does not bind exact inputs")
    if not all(
        isinstance(approval.get(name), str) and approval[name].strip()
        for name in ("approved_by", "authorization_source", "approved_at")
    ):
        raise TabularExportError("external training authorization source missing")
    try:
        approved_at = datetime.fromisoformat(approval["approved_at"])
    except ValueError as exc:
        raise TabularExportError("external training authorization time invalid") from exc
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise TabularExportError("external training authorization time needs timezone")
    return VerifiedTrainingBundle(
        export=export,
        split=split,
        config=config,
        artifact_sha256=artifact_sha256,
        approval_source=approval["authorization_source"],
        similarity_audit=similarity_audit,
    )


def supervise_xgboost_train(
    dataset_path: Path,
    split_path: Path,
    config_path: Path,
    approval_path: Path,
    registry_path: Path | None,
    output_root: Path,
    *,
    source_manifest_path: Path | None = None,
    image_verification_path: Path | None = None,
    similarity_audit_path: Path | None = None,
) -> dict[str, Any]:
    """Bound the worker process even if one learner iteration stalls."""
    config = json.loads(config_path.read_text(encoding="utf8"))
    wall_limit = config.get("max_wall_time_seconds") if isinstance(config, dict) else None
    if type(wall_limit) is not int or not 0 < wall_limit <= 36000:
        raise TabularExportError("XGBoost wall time must be at most 10 hours")
    command = [
        sys.executable,
        "-B",
        "-m",
        "app.ml.tabular",
        "--train-worker",
        "--dataset",
        str(dataset_path),
        "--split",
        str(split_path),
        "--config",
        str(config_path),
        "--approval",
        str(approval_path),
        "--run-dir",
        str(output_root),
    ]
    if (
        source_manifest_path is not None
        and image_verification_path is not None
        and similarity_audit_path is not None
    ):
        command.extend(
            [
                "--external-source-manifest",
                str(source_manifest_path),
                "--image-verification",
                str(image_verification_path),
                "--similarity-audit",
                str(similarity_audit_path),
            ]
        )
    elif registry_path is not None:
        command.extend(["--artifact-registry", str(registry_path)])
    else:
        raise TabularExportError("training source registry or external source evidence required")
    try:
        worker = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
            timeout=wall_limit,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "TRAINING_TIME_LIMIT",
            "max_wall_time_seconds": wall_limit,
            "checkpoint_status": "CHECK_RUN_DIRECTORY",
            "model_promoted": False,
        }
    if worker.returncode:
        raise TabularExportError(
            "XGBoost worker failed: " + (worker.stderr.strip() or "no stderr")[-1000:]
        )
    try:
        result = json.loads(worker.stdout)
    except json.JSONDecodeError as exc:
        raise TabularExportError("XGBoost worker result malformed") from exc
    if not isinstance(result, dict):
        raise TabularExportError("XGBoost worker result malformed")
    return result


def _fit_verified_xgboost(bundle: VerifiedTrainingBundle, output_root: Path) -> dict[str, Any]:
    """One CPU fit, one target, one validation set; never inspect a holdout."""
    repo_root = Path(__file__).resolve().parents[3]
    if not output_root.resolve().is_relative_to(repo_root):
        raise TabularExportError("training output must remain in the isolated worktree")
    config = bundle.config
    external = bundle.export.get("source_kind") == "EXTERNAL_ANNOTATION"
    external_split_check = None
    if external:
        if bundle.similarity_audit is None:
            raise TabularExportError("external similarity audit required before fit")
        validate_external_training_config(config)
        external_split_check = validate_external_split_plan(
            bundle.split, bundle.export, similarity_audit=bundle.similarity_audit
        )
        if external_split_check["scene_group_overlap"] == "NOT_VERIFIED":
            raise TabularExportError(
                "BLOCKED_GROUP_EVIDENCE; LABEL_RUBRIC_UNVERIFIED: verified scene groups "
                "and Attain label equivalence required before another fit"
            )
        if EXTERNAL_LABEL_MAPPING_STATUS != "VERIFIED":
            raise TabularExportError(
                "LABEL_RUBRIC_UNVERIFIED: source-backed Attain Low/low and WS subset "
                "rubric equivalence required before another fit"
            )
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError as exc:
        raise XGBoostUnavailableError("psutil missing in isolated tabular Python") from exc
    available_before = psutil.virtual_memory().available
    if available_before < config["min_start_available_mb"] * 1024 * 1024:
        raise TabularExportError("BLOCKED_RESOURCE: insufficient RAM before model import")
    try:
        import numpy as np
        import xgboost  # type: ignore[import-not-found]
    except ImportError as exc:
        raise XGBoostUnavailableError("numpy/xgboost missing in isolated tabular Python") from exc
    if psutil.virtual_memory().available < config["min_start_available_mb"] * 1024 * 1024:
        raise TabularExportError("BLOCKED_RESOURCE: insufficient RAM after model import")
    row_key = "row_id" if external else "event_id"
    roles = {row[row_key]: row["role"] for row in bundle.split["rows"]}
    train_rows = [row for row in bundle.export["rows"] if roles[row[row_key]] == "TRAIN"]
    validation_rows = [
        row for row in bundle.export["rows"] if roles[row[row_key]] == "VALIDATION"
    ]
    columns = SEVERITY_DETECTION_FEATURE_ORDER if external else tuple(FEATURE_COLUMNS)
    feature_hash = (
        severity_detection_schema_sha256() if external else feature_schema_sha256()
    )
    label_hash = (
        external_severity_label_schema_sha256() if external else label_schema_sha256()
    )
    if external:
        assert external_split_check is not None
        group_counts = external_split_check["exact_image_group_counts"]
    else:
        group_counts = validate_split_plan(bundle.split)["group_counts"]

    def matrix(rows: list[dict[str, Any]]) -> tuple[Any, Any]:
        values: Any = np.empty((len(rows), len(columns)), dtype=np.float32)
        labels: Any = np.empty(len(rows), dtype=np.int8)
        for index, row in enumerate(rows):
            labels[index] = row["target"]
            for position, column in enumerate(columns):
                value = row["features"][column]
                values[index, position] = np.nan if value is None else value
        return values, labels

    x_train, y_train = matrix(train_rows)
    x_validation, y_validation = matrix(validation_rows)
    prior = float(y_train.mean())
    baseline_metrics = validation_metrics(
        [int(value) for value in y_validation], [prior] * len(y_validation)
    )
    class_prior_baseline = (
        external_severity_class_prior_baseline(train_rows, validation_rows)
        if external
        else None
    )
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dataset_sha = bundle.split["dataset_sha256"]
    run_id = (
        f"xgb-{config['target']}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{dataset_sha[:8]}"
    )
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    log_path = run_dir / "training.jsonl"
    checkpoint_path = run_dir / "interrupted_model.json"
    process = psutil.Process()
    process.cpu_percent(interval=None)
    started = time.monotonic()

    class ResourceLimit(xgboost.callback.TrainingCallback):
        def __init__(self) -> None:
            super().__init__()
            self.stop_reason: str | None = None
            self.peak_rss = process.memory_info().rss

        def after_iteration(self, booster: Any, epoch: int, evals_log: dict) -> bool:
            self.peak_rss = max(self.peak_rss, process.memory_info().rss)
            available = psutil.virtual_memory().available
            elapsed = time.monotonic() - started
            if elapsed >= config["max_wall_time_seconds"]:
                self.stop_reason = "TIME_LIMIT"
            elif available < config["critical_available_mb"] * 1024 * 1024:
                self.stop_reason = "MEMORY_PRESSURE"
            if epoch % config["monitor_every_iterations"] == 0 or self.stop_reason:
                metrics = {
                    role: (values.get(config["eval_metric"]) or [None])[-1]
                    for role, values in evals_log.items()
                }
                record = {
                    "iteration": epoch,
                    "elapsed_seconds": round(elapsed, 2),
                    "metrics": metrics,
                    "available_ram_bytes": available,
                    "process_rss_bytes": self.peak_rss,
                    "process_cpu_percent": process.cpu_percent(interval=None),
                    "stop_reason": self.stop_reason,
                }
                with log_path.open("a", encoding="utf8") as stream:
                    stream.write(json.dumps(record, default=float) + "\n")
            if self.stop_reason or (
                elapsed >= config["max_wall_time_seconds"] * 0.8
                and epoch % config["monitor_every_iterations"] == 0
            ):
                booster.save_model(str(checkpoint_path))
            return bool(self.stop_reason)

    resource_limit = ResourceLimit()
    early_stopping = xgboost.callback.EarlyStopping(
        rounds=config["early_stopping_rounds"],
        metric_name=config["eval_metric"],
        data_name="validation_1",
        maximize=config["metric_direction"] == "maximize",
        save_best=True,
    )
    class_weight_mode = config.get("class_weight_mode", "none") if external else "none"
    if class_weight_mode == "balanced_from_train":
        positives = int((y_train == 1).sum())
        negatives = int((y_train == 0).sum())
        if positives == 0 or negatives == 0:
            raise TabularExportError("balanced class weight requires both TRAIN classes")
        scale_pos_weight = negatives / positives
    else:
        scale_pos_weight = 1.0
    model = xgboost.XGBClassifier(
        objective="binary:logistic",
        device="cpu",
        tree_method="hist",
        n_jobs=1,
        nthread=1,
        random_state=config["seed"],
        n_estimators=config["n_estimators"],
        max_depth=config["max_depth"],
        max_bin=config["max_bin"],
        scale_pos_weight=scale_pos_weight,
        eval_metric=config["eval_metric"],
        callbacks=[early_stopping, resource_limit],
    )
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_train, y_train), (x_validation, y_validation)],
        verbose=False,
    )
    model_path = run_dir / "model.json"
    model.save_model(str(model_path))
    digest = hashlib.sha256()
    with model_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    validation_result = None
    validation_prediction_path = None
    validation_prediction_sha256 = None
    if psutil.virtual_memory().available >= config["critical_available_mb"] * 1024 * 1024:
        probabilities = model.predict_proba(x_validation)[:, 1]
        validation_result = validation_metrics(
            [int(value) for value in y_validation], [float(value) for value in probabilities]
        )
        prediction_artifact = {
            "role": "VALIDATION",
            "target": config["target"],
            "target_version": config["target_version"],
            "dataset_version": bundle.export["dataset"]["name"],
            "dataset_sha256": dataset_sha,
            "split_sha256": bundle.artifact_sha256["split"],
            "model_version": run_id,
            "model_sha256": digest.hexdigest(),
            "feature_schema_sha256": feature_hash,
            "rows": [
                (
                    {
                        "row_id": row["row_id"],
                        "image_sha256": row["image_sha256"],
                        "annotation_sha256": row["annotation_sha256"],
                        "probability": float(probability),
                    }
                    if external
                    else {
                        "event_id": row["event_id"],
                        "snapshot_id": row["snapshot_id"],
                        "snapshot_sha256": row["snapshot_sha256"],
                        "probability": float(probability),
                    }
                )
                for row, probability in zip(validation_rows, probabilities, strict=True)
            ],
        }
        validation_prediction_path = run_dir / "validation_predictions.json"
        validation_prediction_path.write_text(
            json.dumps(prediction_artifact, ensure_ascii=False) + "\n", encoding="utf8"
        )
        validation_prediction_sha256 = hashlib.sha256(
            validation_prediction_path.read_bytes()
        ).hexdigest()
    metadata = {
        "status": (
            "INTERRUPTED_EXPERIMENTAL" if resource_limit.stop_reason else "EXPERIMENTAL_CANDIDATE"
        ),
        "run_id": run_id,
        "model_version": run_id,
        "target": config["target"],
        "target_version": config["target_version"],
        "dataset_version": bundle.export["dataset"]["name"],
        "dataset_sha256": dataset_sha,
        "split_sha256": bundle.artifact_sha256["split"],
        "model_sha256": digest.hexdigest(),
        "feature_schema_sha256": feature_hash,
        "label_schema_sha256": label_hash,
        "feature_order": columns,
        "artifact_sha256": bundle.artifact_sha256,
        "authorization_source": bundle.approval_source,
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "group_counts": group_counts,
        "domain_shift": (
            _external_domain_shift_summary(bundle.export, bundle.split) if external else None
        ),
        "source_kind": bundle.export.get("source_kind", "URMIND_EVENT"),
        "scientific_validation": False if external else None,
        "scene_group_status": "UNKNOWN" if external else None,
        "box_origin": "human_annotation_proxy" if external else None,
        "combined_model_evaluation": "BLOCKED_EXPOSURE" if external else None,
        "seed": config["seed"],
        "parameters": config,
        "class_weight_mode": class_weight_mode,
        "scale_pos_weight_from_train": scale_pos_weight,
        "xgboost_version": xgboost.__version__,
        "git_sha": git_sha,
        "best_iteration": getattr(model, "best_iteration", None),
        "best_score": getattr(model, "best_score", None),
        "eval_metric": config["eval_metric"],
        "metric_direction": config["metric_direction"],
        "baseline_validation_metrics": baseline_metrics,
        "class_prior_baseline": class_prior_baseline,
        "validation_metrics": validation_result,
        "validation_prediction_path": (
            str(validation_prediction_path) if validation_prediction_path is not None else None
        ),
        "validation_prediction_sha256": validation_prediction_sha256,
        "ram_available_before_bytes": available_before,
        "peak_xgboost_process_rss_bytes": resource_limit.peak_rss,
        "stop_reason": resource_limit.stop_reason,
        "model_promoted": False,
    }
    (run_dir / "run.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=float) + "\n",
        encoding="utf8",
    )
    return {**metadata, "model_path": str(model_path), "run_dir": str(run_dir)}


def validation_metrics(y_true: Sequence[int], probabilities: Sequence[float]) -> dict[str, Any]:
    """Validation-only metrics; callers must prove role before passing predictions."""
    if len(y_true) != len(probabilities) or any(
        type(value) is not int or value not in {0, 1} for value in y_true
    ):
        raise TabularExportError("binary validation labels invalid")
    if any(
        type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1
        for value in probabilities
    ):
        raise TabularExportError("validation probabilities invalid")
    confusion = {"tn": 0, "fp": 0, "fn": 0, "tp": 0}
    for actual, probability in zip(y_true, probabilities, strict=True):
        predicted = int(probability >= 0.5)
        confusion[
            "tp"
            if actual == 1 and predicted == 1
            else "tn"
            if actual == 0 and predicted == 0
            else "fp"
            if predicted == 1
            else "fn"
        ] += 1
    support_positive = sum(value == 1 for value in y_true)
    support_negative = len(y_true) - support_positive
    average_precision = None
    if support_positive and support_negative:
        ordered = sorted(zip(probabilities, y_true, strict=True), key=lambda pair: -pair[0])
        found_positive = 0
        total_seen = 0
        precision_sum = 0.0
        index = 0
        while index < len(ordered):
            score = ordered[index][0]
            tied = 0
            positives = 0
            while index < len(ordered) and ordered[index][0] == score:
                tied += 1
                positives += ordered[index][1]
                index += 1
            total_seen += tied
            found_positive += positives
            precision_sum += positives * found_positive / total_seen
        average_precision = precision_sum / support_positive
    per_class = {}
    for value, true_positive, false_positive, false_negative, support in (
        ("0", confusion["tn"], confusion["fn"], confusion["fp"], support_negative),
        ("1", confusion["tp"], confusion["fp"], confusion["fn"], support_positive),
    ):
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else None
        )
        recall = true_positive / support if support else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else 0.0
            if precision is not None and recall is not None
            else None
        )
        per_class[value] = {"support": support, "precision": precision, "recall": recall, "f1": f1}
    return {
        **binary_metrics(y_true, probabilities),
        "confusion_matrix": confusion,
        "per_class": per_class,
        "average_precision": average_precision,
        "calibration_bins": reliability_bins(y_true, probabilities),
        "class_missing": any(value not in y_true for value in (0, 1)),
    }


def external_severity_class_prior_baseline(
    train_rows: Sequence[dict[str, Any]], validation_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """A cheap, stronger baseline: per-damage priors learned from TRAIN only."""
    supported = ("alligator crack", "pothole")
    counts: dict[str, Counter[int]] = {name: Counter() for name in supported}
    for row in train_rows:
        kind, label = row.get("source_type"), row.get("target")
        if kind not in counts or type(label) is not int or label not in {0, 1}:
            raise TabularExportError("external baseline TRAIN class or target invalid")
        counts[kind][label] += 1
    if any(not counts[name][0] or not counts[name][1] for name in supported):
        raise TabularExportError("external baseline needs both labels per damage type")
    # Laplace smoothing prevents 0/1 probabilities with limited support.
    rates = {
        name: (counts[name][1] + 1) / (sum(counts[name].values()) + 2)
        for name in supported
    }
    labels: list[int] = []
    probabilities: list[float] = []
    for row in validation_rows:
        kind, label = row.get("source_type"), row.get("target")
        if kind not in rates or type(label) is not int or label not in {0, 1}:
            raise TabularExportError("external baseline VALIDATION class or target invalid")
        labels.append(label)
        probabilities.append(rates[kind])
    if not labels:
        raise TabularExportError("external baseline VALIDATION empty")
    return {
        "method": "train_only_smoothed_damage_type_prior",
        "train_class_counts": {
            name: {"LOW": counts[name][0], "HIGH": counts[name][1]}
            for name in supported
        },
        "train_class_rates": rates,
        "validation_metrics": validation_metrics(labels, probabilities),
    }


def verify_external_validation_model_predictions(
    model_path: Path,
    validation_rows: Sequence[dict[str, Any]],
    submitted_probabilities: dict[str, float],
) -> dict[str, Any]:
    """Recompute every saved VALIDATION score from the pinned feature matrix.

    This is an explicit runtime check, separate from the lightweight artifact-only
    check. It reads no images or TEST data and never fits a model.
    """
    if not validation_rows or set(submitted_probabilities) != {
        row["row_id"] for row in validation_rows
    }:
        raise TabularExportError("external model reproduction membership invalid")
    try:
        import numpy as np
        import xgboost
    except ImportError as exc:
        raise XGBoostUnavailableError(
            "numpy/xgboost required to reproduce external VALIDATION predictions"
        ) from exc

    matrix = np.asarray(
        [
            [row["features"][name] for name in SEVERITY_DETECTION_FEATURE_ORDER]
            for row in validation_rows
        ],
        dtype=np.float32,
    )
    model = xgboost.XGBClassifier(device="cpu", n_jobs=1)
    model.load_model(str(model_path))
    if model.n_features_in_ != len(SEVERITY_DETECTION_FEATURE_ORDER):
        raise TabularExportError("saved external model feature count mismatch")
    reproduced = model.predict_proba(matrix)
    if reproduced.shape != (len(validation_rows), 2):
        raise TabularExportError("saved external model prediction shape mismatch")
    for row, pair in zip(validation_rows, reproduced, strict=True):
        expected = submitted_probabilities[row["row_id"]]
        actual = float(pair[1])
        if not math.isfinite(actual) or not math.isclose(
            actual, expected, rel_tol=1e-7, abs_tol=1e-7
        ):
            raise TabularExportError(
                f"saved external prediction differs from model for {row['row_id']}"
            )
    return {
        "verified_rows": len(validation_rows),
        "xgboost_runtime_version": xgboost.__version__,
        "device": "cpu",
        "n_jobs": 1,
    }


def evaluate_external_validation_artifacts(
    prediction_path: Path,
    dataset_path: Path,
    split_path: Path,
    config_path: Path,
    approval_path: Path,
    source_manifest_path: Path,
    image_verification_path: Path,
    model_path: Path,
    model_metadata_path: Path,
    *,
    similarity_audit_path: Path | None = None,
    verify_model_predictions: bool = False,
) -> dict[str, Any]:
    """Score only fingerprinted Attain VALIDATION rows; never claim final evaluation."""
    bundle = load_external_training_bundle(
        dataset_path,
        split_path,
        config_path,
        approval_path,
        source_manifest_path,
        image_verification_path,
        similarity_audit_path,
    )
    root = Path(__file__).resolve().parents[3]
    if any(
        not path.resolve().is_relative_to(root)
        for path in (prediction_path, model_path, model_metadata_path)
    ):
        raise TabularExportError("external evaluation artifacts outside isolated worktree")
    metadata = json.loads(model_metadata_path.read_text(encoding="utf8"))
    predictions = json.loads(prediction_path.read_text(encoding="utf8"))
    if not isinstance(metadata, dict) or not isinstance(predictions, dict):
        raise TabularExportError("external evaluation metadata malformed")
    model_hash = _file_sha256(model_path)
    prediction_hash = _file_sha256(prediction_path)
    expected = {
        "model_sha256": model_hash,
        "dataset_sha256": bundle.export["dataset"]["content_sha256"],
        "dataset_version": bundle.export["dataset"]["name"],
        "split_sha256": bundle.artifact_sha256["split"],
        "feature_schema_sha256": severity_detection_schema_sha256(),
        "label_schema_sha256": external_severity_label_schema_sha256(),
        "target": EXTERNAL_SEVERITY_TARGET,
        "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
        "source_kind": "EXTERNAL_ANNOTATION",
        "scientific_validation": False,
        "scene_group_status": "UNKNOWN",
        "combined_model_evaluation": "BLOCKED_EXPOSURE",
        "validation_prediction_sha256": prediction_hash,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise TabularExportError("external model metadata not bound to validated artifacts")
    if (
        metadata.get("status") != "EXPERIMENTAL_CANDIDATE"
        or metadata.get("model_promoted") is not False
        or not isinstance(metadata.get("run_id"), str)
        or not metadata["run_id"]
        or metadata.get("model_version") != metadata["run_id"]
    ):
        raise TabularExportError("external model run/version or candidate status invalid")
    if metadata.get("artifact_sha256") != bundle.artifact_sha256:
        raise TabularExportError("external model training input hashes differ")
    feature_order = metadata.get("feature_order")
    if not isinstance(feature_order, (list, tuple)) or tuple(feature_order) != SEVERITY_DETECTION_FEATURE_ORDER:
        raise TabularExportError("external model feature order changed")
    prediction_expected = {
        "role": "VALIDATION",
        "target": EXTERNAL_SEVERITY_TARGET,
        "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
        "dataset_version": bundle.export["dataset"]["name"],
        "dataset_sha256": bundle.export["dataset"]["content_sha256"],
        "split_sha256": bundle.artifact_sha256["split"],
        "model_version": metadata.get("run_id"),
        "model_sha256": model_hash,
        "feature_schema_sha256": severity_detection_schema_sha256(),
    }
    if any(predictions.get(key) != value for key, value in prediction_expected.items()):
        raise TabularExportError("external validation role or model lineage forged")
    roles = {row["row_id"]: row["role"] for row in bundle.split["rows"]}
    validation = {
        row["row_id"]: row
        for row in bundle.export["rows"]
        if roles[row["row_id"]] == "VALIDATION"
    }
    submitted = predictions.get("rows")
    if not isinstance(submitted, list) or len(submitted) != len(validation):
        raise TabularExportError("external validation prediction membership incomplete")
    by_id: dict[str, float] = {}
    for entry in submitted:
        row_id = entry.get("row_id") if isinstance(entry, dict) else None
        if not isinstance(row_id, str) or row_id not in validation:
            raise TabularExportError("external prediction outside proven VALIDATION")
        source = validation[row_id]
        probability = entry.get("probability")
        if (
            row_id in by_id
            or entry.get("image_sha256") != source["image_sha256"]
            or entry.get("annotation_sha256") != source["annotation_sha256"]
            or not isinstance(probability, (int, float))
            or isinstance(probability, bool)
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise TabularExportError("external prediction fingerprint or probability invalid")
        by_id[row_id] = float(probability)
    if set(by_id) != set(validation):
        raise TabularExportError("external validation rows missing")
    ordered = list(validation)
    reproduction = (
        verify_external_validation_model_predictions(
            model_path, [validation[row_id] for row_id in ordered], by_id
        )
        if verify_model_predictions
        else None
    )
    metrics = validation_metrics(
        [validation[row_id]["target"] for row_id in ordered],
        [by_id[row_id] for row_id in ordered],
    )
    if metadata.get("validation_metrics") != metrics:
        raise TabularExportError("external recorded validation metrics differ from predictions")
    train_rows = [
        row for row in bundle.export["rows"] if roles[row["row_id"]] == "TRAIN"
    ]
    baseline = external_severity_class_prior_baseline(train_rows, list(validation.values()))
    baseline_metrics = baseline["validation_metrics"]
    beats_class_baseline = all(
        (
            metrics[name] < baseline_metrics[name]
            if name in {"log_loss", "brier"}
            else metrics[name] > baseline_metrics[name]
        )
        for name in ("log_loss", "brier", "average_precision")
    )
    by_damage_type = {}
    for kind in ("alligator crack", "pothole"):
        members = [row_id for row_id in ordered if validation[row_id]["source_type"] == kind]
        by_damage_type[kind] = validation_metrics(
            [validation[row_id]["target"] for row_id in members],
            [by_id[row_id] for row_id in members],
        )
    return {
        "status": "INTERNAL_VALIDATION_ONLY",
        "rows": len(validation),
        "model_sha256": model_hash,
        "dataset_version": bundle.export["dataset"]["name"],
        "metrics": metrics,
        "label_mapping_status": EXTERNAL_LABEL_MAPPING_STATUS,
        "class_prior_baseline": baseline,
        "domain_shift": _external_domain_shift_summary(bundle.export, bundle.split),
        "similarity_boundary": _external_similarity_boundary_summary(
            bundle.split, bundle.similarity_audit
        ),
        "selection_gate": (
            "INTERNAL_COMPARISON_PASS" if beats_class_baseline else "FAILS_CLASS_PRIOR_BASELINE"
        ),
        "by_damage_type": by_damage_type,
        "artifact_binding_verified": True,
        "prediction_generation_verified": reproduction is not None,
        "model_reproduction": reproduction,
        "scientific_validation": False,
        "combined_model_evaluation": "BLOCKED_EXPOSURE",
    }


def predict_visual_severity_experimental(
    model_path: Path,
    model_metadata_path: Path,
    image: Any,
    urmind_class: str,
    bbox: dict[str, float],
    *,
    box_origin: str,
    detector_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Offline analysis only; this candidate cannot set RiskAssessment severity."""
    if box_origin not in {"human_annotation_proxy", "persisted_detector"}:
        raise TabularExportError("visual severity box origin unsupported")
    if box_origin == "persisted_detector" and (
        not isinstance(detector_lineage, dict)
        or any(
            not isinstance(detector_lineage.get(name), str)
            or not detector_lineage[name]
            for name in (
                "model_version",
                "checkpoint_sha256",
                "preprocessing_version",
                "postprocessing_version",
            )
        )
        or not re.fullmatch(r"[0-9a-f]{64}", detector_lineage["checkpoint_sha256"])
    ):
        raise TabularExportError("detector box requires attributable visual lineage")
    root = Path(__file__).resolve().parents[3]
    if (
        not model_path.resolve().is_relative_to(root)
        or not model_metadata_path.resolve().is_relative_to(root)
        or model_path.resolve().parent != model_metadata_path.resolve().parent
    ):
        raise TabularExportError("experimental model artifacts must share a worktree run")
    metadata = json.loads(model_metadata_path.read_text(encoding="utf8"))
    if not isinstance(metadata, dict) or any(
        metadata.get(key) != value
        for key, value in {
            "status": "EXPERIMENTAL_CANDIDATE",
            "target": EXTERNAL_SEVERITY_TARGET,
            "target_version": EXTERNAL_SEVERITY_TARGET_VERSION,
            "source_kind": "EXTERNAL_ANNOTATION",
            "feature_schema_sha256": severity_detection_schema_sha256(),
            "label_schema_sha256": external_severity_label_schema_sha256(),
            "model_promoted": False,
            "box_origin": "human_annotation_proxy",
        }.items()
    ):
        raise TabularExportError("visual severity model contract or candidate status invalid")
    feature_order = metadata.get("feature_order")
    if (
        not isinstance(feature_order, (list, tuple))
        or tuple(feature_order) != SEVERITY_DETECTION_FEATURE_ORDER
        or metadata.get("model_sha256") != _file_sha256(model_path)
        or not isinstance(metadata.get("model_version"), str)
        or not metadata["model_version"]
        or metadata.get("model_version") != metadata.get("run_id")
    ):
        raise TabularExportError("visual severity model hash or feature order invalid")
    features = build_severity_detection_features(urmind_class, bbox, image)
    try:
        import numpy as np
        import xgboost
    except ImportError as exc:
        raise XGBoostUnavailableError("numpy/xgboost unavailable for experimental inference") from exc
    values = np.asarray([[features[name] for name in SEVERITY_DETECTION_FEATURE_ORDER]], dtype=np.float32)
    model = xgboost.XGBClassifier()
    model.load_model(str(model_path))
    probabilities = model.predict_proba(values)
    if probabilities.shape != (1, 2):
        raise TabularExportError("visual severity prediction shape invalid")
    score = float(probabilities[0, 1])
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise TabularExportError("visual severity probability invalid")
    return {
        "target": EXTERNAL_SEVERITY_TARGET,
        "label": "HIGH" if score >= 0.5 else "LOW",
        "score_high_uncalibrated": score,
        "feature_schema_sha256": metadata["feature_schema_sha256"],
        "model_version": metadata["model_version"],
        "model_sha256": metadata["model_sha256"],
        "box_origin": box_origin,
        "detector_lineage": detector_lineage,
        "status": "EXPERIMENTAL_ONLY",
        "serving_eligible": False,
        "risk_assessment_written": False,
        "priority_source": "rules",
        "risk_source": "rules",
        "blocking_reasons": [
            "SCENE_INDEPENDENCE_UNVERIFIED",
            "ANNOTATION_TO_DETECTOR_SHIFT_UNVALIDATED",
            "YOLOX_EXPOSURE_UNKNOWN",
            "NO_FINAL_EVALUATION",
        ],
    }


def evaluate_validation_artifacts(
    prediction_path: Path,
    dataset_path: Path,
    split_path: Path,
    model_path: Path,
    model_metadata_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    """Score only rows proven VALIDATION by a registered, reproducible split."""
    artifact_hashes = {
        name: _registered_artifact(path, registry_path)[1]
        for name, path in (
            ("predictions", prediction_path),
            ("dataset", dataset_path),
            ("split", split_path),
            ("model", model_path),
            ("model_metadata", model_metadata_path),
        )
    }

    def read(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(value, dict):
            raise TabularExportError("registered JSON artifact must be an object")
        return value

    dataset = read(dataset_path)
    checked = validate_ground_truth_export(dataset)
    version = (dataset.get("dataset") or {}).get("name")
    if (
        not isinstance(version, str)
        or not version
        or dataset["dataset"].get("content_sha256") != checked["content_sha256"]
    ):
        raise TabularExportError("DatasetVersion name missing")
    split = read(split_path)
    validate_split_plan(split)
    if split.get("dataset_sha256") != checked["content_sha256"]:
        raise TabularExportError("split dataset fingerprint mismatch")
    if type(split.get("seed")) is not int or type(split.get("temporal")) is not bool:
        raise TabularExportError("split generation parameters missing")
    regenerated = generate_split_plan(
        dataset,
        seed=split["seed"],
        temporal=split["temporal"],
        include_holdouts=split.get("include_holdouts", False),
    )
    if regenerated["status"] != "DRAFT" or split["rows"] != regenerated["rows"]:
        raise TabularExportError("split manifest differs from reproducible group assignment")
    metadata = read(model_metadata_path)
    expected = {
        "model_sha256": artifact_hashes["model"],
        "dataset_sha256": checked["content_sha256"],
        "split_sha256": artifact_hashes["split"],
        "feature_schema_sha256": feature_schema_sha256(),
        "target": TARGET_NAME,
        "target_version": checked["target_version"],
        "dataset_version": version,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise TabularExportError("model artifact lineage mismatch")
    if not isinstance(metadata.get("model_version"), str) or not metadata["model_version"]:
        raise TabularExportError("model version missing")
    predictions = read(prediction_path)
    if predictions.get("role") != "VALIDATION" or any(
        predictions.get(key) != value
        for key, value in (expected | {"model_version": metadata["model_version"]}).items()
    ):
        raise TabularExportError("prediction artifact lineage mismatch")
    data_rows = {row["event_id"]: row for row in dataset["rows"]}
    validation_ids = {row["event_id"] for row in split["rows"] if row["role"] == "VALIDATION"}
    prediction_rows = predictions.get("rows")
    if not validation_ids or not isinstance(prediction_rows, list):
        raise TabularExportError("VALIDATION predictions missing")
    seen: set[str] = set()
    labels: list[int] = []
    probabilities: list[float] = []
    for row in prediction_rows:
        if not isinstance(row, dict) or "label" in row or "target" in row:
            raise TabularExportError("prediction row contains untrusted label")
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or event_id not in validation_ids or event_id in seen:
            raise TabularExportError("prediction Event is not unique VALIDATION member")
        original = data_rows[event_id]
        if (row.get("snapshot_id"), row.get("snapshot_sha256")) != (
            original["snapshot_id"],
            original["snapshot_sha256"],
        ):
            raise TabularExportError("prediction snapshot identity mismatch")
        seen.add(event_id)
        labels.append(original["target"])
        probability = row.get("probability")
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            raise TabularExportError("prediction probability invalid")
        probabilities.append(float(probability))
    if seen != validation_ids:
        raise TabularExportError("prediction set differs from VALIDATION membership")
    return {
        "status": "ARTIFACT_BOUND_OFFLINE_METRICS",
        "role": "VALIDATION",
        "dataset_version": version,
        "model_version": metadata["model_version"],
        "artifact_sha256": artifact_hashes,
        "metrics": validation_metrics(labels, probabilities),
        "prediction_generation_verified": False,
        "scientific_validation": False,
        "promotion": "NOT_AUTHORIZED",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Offline tabular contract and XGBoost preparation. Training runs only with a "
            "registered bundle or with an export, a pre-registered contract and a human "
            "authorization bound to both hashes."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--label-form", action="store_true")
    action.add_argument("--validate-export", type=Path)
    action.add_argument("--validate-labels", type=Path)
    action.add_argument("--validate-visual", type=Path)
    action.add_argument("--generate-splits", type=Path)
    action.add_argument("--validate-splits", type=Path)
    action.add_argument("--export-dataset", type=Path)
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--train", action="store_true")
    action.add_argument("--train-worker", action="store_true", help=argparse.SUPPRESS)
    action.add_argument("--evaluate-validation", type=Path)
    action.add_argument("--calibrate", action="store_true")
    action.add_argument("--shap", action="store_true")
    action.add_argument("--report", action="store_true")
    action.add_argument("--contract-sha256", type=Path, metavar="CONTRACT")
    action.add_argument("--train-authorized-export", type=Path, metavar="EXPORT")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--update-draft", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--artifact-registry", type=Path)
    parser.add_argument("--external-source-manifest", type=Path)
    parser.add_argument("--image-verification", type=Path)
    parser.add_argument("--similarity-audit", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--model-metadata", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--yolox-registry", type=Path)
    parser.add_argument("--yolox-train-manifest", type=Path)
    parser.add_argument("--yolox-validation-manifest", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    yolox_manifests = (
        {"TRAIN": args.yolox_train_manifest, "VALIDATION": args.yolox_validation_manifest}
        if args.yolox_train_manifest and args.yolox_validation_manifest
        else None
    )

    def read_json(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        value = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(value, dict):
            raise TabularExportError("document must be a JSON object")
        return value

    def required_json(path: Path | None) -> dict[str, Any]:
        value = read_json(path)
        if value is None:
            raise TabularExportError("required JSON input path missing")
        return value

    try:
        if args.label_form:
            result = labeling_protocol()
            if args.write:
                path = (
                    Path(__file__).resolve().parents[3]
                    / "datasets/annotations/tabular_labeling_protocol.json"
                )
                payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
                if (
                    path.exists()
                    and path.read_text(encoding="utf8") != payload
                    and not args.update_draft
                ):
                    raise TabularExportError(
                        "existing protocol preserved; pass --update-draft explicitly"
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload, encoding="utf8", newline="\n")
                result = {"written": str(path), "training_authorized": False}
        elif args.validate_export:
            # JSON document or the NDJSON stream served by the API export endpoint.
            result = validate_ground_truth_export(load_ground_truth_export(args.validate_export))
        elif args.contract_sha256:
            contract = load_training_contract(required_json(args.contract_sha256))
            result = {"contract_sha256": contract.sha256()}
        elif args.train_authorized_export:
            if not (args.contract and args.authorization and args.out):
                parser.error("--train-authorized-export requires --contract, --authorization and --out")
            report = run_tabular_training(
                load_ground_truth_export(args.train_authorized_export),
                required_json(args.contract),
                required_json(args.authorization),
                args.out,
            )
            result = {
                key: report[key]
                for key in (
                    "dataset_sha256",
                    "best_iteration",
                    "split_counts",
                    "test_metrics",
                    "test_log_loss_gain_vs_prior",
                    "leakage_findings",
                    "promotion",
                )
            }
        elif args.validate_labels:
            result = validate_label_import(required_json(args.validate_labels))
        elif args.validate_visual:
            result = validate_visual_output(required_json(args.validate_visual))
        elif args.generate_splits:
            if args.output is None or args.config is None:
                parser.error("--generate-splits requires --config and --output")
            config = required_json(args.config)
            result = generate_split_plan(
                required_json(args.generate_splits),
                seed=config["seed"],
                include_holdouts=config.get("include_holdouts", False),
            )
            if result["status"] == "DRAFT":
                args.output.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
                )
        elif args.validate_splits:
            result = validate_split_plan(
                required_json(args.validate_splits),
                yolox_registry=args.yolox_registry,
                yolox_manifests=yolox_manifests,
            )
        elif args.export_dataset:
            if args.output is None:
                parser.error("--export-dataset requires --output")
            source = required_json(args.export_dataset)
            result = validate_ground_truth_export(source)
            dataset_meta = source.get("dataset")
            if (
                not isinstance(dataset_meta, dict)
                or not isinstance(dataset_meta.get("name"), str)
                or not dataset_meta["name"]
                or dataset_meta.get("content_sha256") != result["content_sha256"]
            ):
                raise TabularExportError("DatasetVersion name and content hash required")
            if result["readiness"]["status"] == "READY":
                args.output.write_text(
                    json.dumps(source, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf8",
                )
                result["written"] = str(args.output)
        elif args.evaluate_validation:
            if args.external_source_manifest is not None:
                if not all(
                    (
                        args.dataset,
                        args.split,
                        args.config,
                        args.approval,
                        args.image_verification,
                        args.similarity_audit,
                        args.model,
                        args.model_metadata,
                    )
                ):
                    raise TabularExportError("external evaluation requires bound training inputs")
                result = evaluate_external_validation_artifacts(
                    args.evaluate_validation,
                    args.dataset,
                    args.split,
                    args.config,
                    args.approval,
                    args.external_source_manifest,
                    args.image_verification,
                    args.model,
                    args.model_metadata,
                    similarity_audit_path=args.similarity_audit,
                    verify_model_predictions=True,
                )
            else:
                if not all(
                    (args.dataset, args.split, args.model, args.model_metadata, args.artifact_registry)
                ):
                    raise TabularExportError(
                        "evaluation requires registered dataset, split, model and model metadata"
                    )
                result = evaluate_validation_artifacts(
                    args.evaluate_validation,
                    args.dataset,
                    args.split,
                    args.model,
                    args.model_metadata,
                    args.artifact_registry,
                )
        elif args.calibrate:
            result = {
                "status": "EXECUTION_DEFERRED",
                "role": "CALIBRATION",
                "method": "fit calibrator only after model freeze and dataset approval",
                "holdout_accessed": False,
            }
        elif args.shap:
            result = {
                "status": "EXECUTION_DEFERRED",
                "method": "TreeExplainer on frozen trained model",
                "requires": ["model_sha256", "feature_schema_sha256", "non-TEST sample"],
                "causal_claim": False,
            }
        else:
            config = required_json(args.config)
            if args.train or args.train_worker:
                external = config.get("target") == EXTERNAL_SEVERITY_TARGET
                if not all((args.dataset, args.split, args.approval, args.run_dir)):
                    raise TabularExportError(
                        "training requires dataset, split, approval and run directory"
                    )
                if external and not all(
                    (args.external_source_manifest, args.image_verification, args.similarity_audit)
                ):
                    raise TabularExportError("external training requires source, image and similarity evidence")
                if not external and args.artifact_registry is None:
                    raise TabularExportError("Event training requires the official artifact registry")
                if args.train_worker:
                    bundle = (
                        load_external_training_bundle(
                            args.dataset,
                            args.split,
                            args.config,
                            args.approval,
                            args.external_source_manifest,
                            args.image_verification,
                            args.similarity_audit,
                        )
                        if external
                        else load_verified_training_bundle(
                            args.dataset,
                            args.split,
                            args.config,
                            args.approval,
                            args.artifact_registry,
                        )
                    )
                    result = train_xgboost(bundle, output_root=args.run_dir)
                else:
                    result = supervise_xgboost_train(
                        args.dataset,
                        args.split,
                        args.config,
                        args.approval,
                        args.artifact_registry,
                        args.run_dir,
                        source_manifest_path=args.external_source_manifest,
                        image_verification_path=args.image_verification,
                        similarity_audit_path=args.similarity_audit,
                    )
            else:
                export = read_json(args.dataset)
                split = read_json(args.split)
                if config.get("target") == EXTERNAL_SEVERITY_TARGET:
                    if export is None or split is None:
                        raise TabularExportError("external preflight requires dataset and split")
                    result = preflight_external_xgboost(
                        export,
                        split,
                        config,
                        similarity_audit=read_json(args.similarity_audit),
                    )
                elif args.dry_run:
                    result = dry_run_xgboost(
                        export,
                        split,
                        config,
                        yolox_registry=args.yolox_registry,
                        yolox_manifests=yolox_manifests,
                    )
                else:
                    result = preflight_xgboost(
                        export,
                        split,
                        config,
                        yolox_registry=args.yolox_registry,
                        yolox_manifests=yolox_manifests,
                    )
                if args.dry_run:
                    result = {**result, "mode": "DRY_RUN", "artifact_written": False}
                if args.report:
                    result = {
                        "preflight": result,
                        "calibration": "EXECUTION_DEFERRED",
                        "shap": "EXECUTION_DEFERRED",
                        "promotion": "NOT_REQUESTED",
                    }
        if args.output and (args.preflight or args.dry_run or args.report):
            args.output.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (TabularExportError, XGBoostUnavailableError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
