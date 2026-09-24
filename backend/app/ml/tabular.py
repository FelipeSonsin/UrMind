"""Fase 8 — scaffolding tabular (snapshot imutável → DatasetVersion → baseline → XGBoost).

Nada aqui entra no runtime. O Worker continua decidindo por regras
(`assess_features`); este módulo só prepara o caminho para um modelo tabular
futuro, que exige Ground Truth, split válido, avaliação, calibração e aprovação.

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
import json
import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.ml.splits import DatasetSplit, SplitRatios, find_leakage, split_by_group
from app.services.features import SCHEMA_VERSION as FEATURE_SCHEMA_VERSION

TABULAR_SCHEMA_VERSION = "urmind-tabular-v1"
# The export is pinned: a snapshot built by another FeatureBuilder version is refused.
PINNED_FEATURE_SCHEMA = FEATURE_SCHEMA_VERSION
TARGET_NAME = "review_confirmed"

# Provisional data-sufficiency policy (not a scientific threshold): below this
# the training status is BLOCKED_DATA instead of a number with no support.
MIN_LABELED_EXAMPLES = 200
MIN_EXAMPLES_PER_TARGET_VALUE = 50
MIN_INDEPENDENT_GROUPS = 10


def _get(snapshot: dict[str, Any], path: str) -> Any:
    value: Any = snapshot
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _bool(value: Any) -> float | None:
    return None if value is None else float(bool(value))


def _mean(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    return float(sum(values) / len(values))


def _max(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    return float(max(values))


# column -> (snapshot path, transform). Allowlist: nothing else leaves the snapshot.
FEATURE_COLUMNS: dict[str, tuple[str, Callable[[Any], float | None]]] = {
    "detection_count": ("visual.detection_count", lambda v: None if v is None else float(v)),
    "confidence_mean": ("visual.detection_confidences", _mean),
    "confidence_max": ("visual.detection_confidences", _max),
    "accuracy_m": ("location.accuracy_m", lambda v: None if v is None else float(v)),
    "snap_distance_m": ("location.snap_distance_m", lambda v: None if v is None else float(v)),
    "has_road_segment": (
        "missingness.road_segment_missing",
        lambda v: None if v is None else float(not v),
    ),
    "near_school": ("context.near_school", _bool),
    "near_health_unit": ("context.near_health_unit", _bool),
    "crossing_nearby": ("context.crossing_nearby", _bool),
    "rain_mm_24h": ("context.rain_mm_24h", lambda v: None if v is None else float(v)),
    "previous_events_same_segment": (
        "history.previous_events_same_segment",
        lambda v: None if v is None else float(v),
    ),
    "recent_events_same_segment_30d": (
        "history.recent_events_same_segment_30d",
        lambda v: None if v is None else float(v),
    ),
}

# Context columns and the provider whose temporal status gates them.
_CONTEXT_PROVIDER = {
    "near_school": "overpass_pois",
    "near_health_unit": "overpass_pois",
    "crossing_nearby": "overpass_pois",
    "rain_mm_24h": "open_meteo_rain",
}

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


def flatten_snapshot(snapshot: dict[str, Any]) -> dict[str, float | None]:
    if (
        snapshot.get("query_mode") == "RETROSPECTIVE_ANALYTICS"
        or _get(snapshot, "history.query_mode") == "RETROSPECTIVE_ANALYTICS"
    ):
        raise TabularExportError("retrospective analytics cannot supply scientific features")
    if snapshot.get("feature_schema_version") != PINNED_FEATURE_SCHEMA:
        raise TabularExportError(
            f"feature schema {snapshot.get('feature_schema_version')!r} != {PINNED_FEATURE_SCHEMA}"
        )
    temporal = {
        name: (info or {}).get("temporal_status")
        for name, info in (_get(snapshot, "provenance.context") or {}).items()
    }
    row: dict[str, float | None] = {}
    for column, (path, transform) in FEATURE_COLUMNS.items():
        provider = _CONTEXT_PROVIDER.get(column)
        if provider and temporal.get(provider) not in {"available_at_event", "historical_source"}:
            row[column] = None  # information not available at event time
            continue
        row[column] = transform(_get(snapshot, path))
    return row


def build_example(
    snapshot: dict[str, Any],
    *,
    snapshot_collected_at: datetime,
    label_at: datetime,
    review_confirmed: bool,
    capture_ids: Sequence[str] = (),
    review: Any | None = None,
) -> TabularExample:
    """Build a row; only a matching persisted Review establishes label provenance.

    Bare Boolean labels are retained for offline scaffolding, but cannot pass
    readiness. `correct` reviews have no binary mapping and are refused.
    """
    if type(review_confirmed) is not bool:
        raise TabularExportError("review_confirmed deve ser booleano")
    if snapshot_collected_at >= label_at:
        raise TabularExportError("snapshot coletado depois do rótulo: vazamento do target")
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
        features=flatten_snapshot(snapshot),
        review_id=review_id,
    )


def leakage_groups(examples: Sequence[TabularExample]) -> dict[str, str]:
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
    examples: Sequence[TabularExample], validation_from: datetime, test_from: datetime
) -> dict[str, Any]:
    """Time-ordered split; test rows sharing a group with earlier splits are dropped, not kept."""
    if not validation_from < test_from:
        raise TabularExportError("validation_from precisa ser anterior a test_from")
    groups = leakage_groups(examples)
    parts: dict[str, list[TabularExample]] = {"train": [], "validation": [], "test": []}
    unavailable_labels = {"train": 0, "validation": 0}
    for example in sorted(examples, key=lambda e: (e.occurred_at, e.event_id)):
        name = (
            "train"
            if example.occurred_at < validation_from
            else "validation"
            if example.occurred_at < test_from
            else "test"
        )
        cutoff = validation_from if name == "train" else test_from if name == "validation" else None
        if cutoff is not None and example.label_at >= cutoff:
            unavailable_labels[name] += 1
        else:
            parts[name].append(example)
    dropped: dict[str, int] = {}
    seen = {groups[e.event_id] for e in parts["train"]}
    for name in ("validation", "test"):
        kept = [e for e in parts[name] if groups[e.event_id] not in seen]
        dropped[name] = len(parts[name]) - len(kept)
        parts[name] = kept
        seen |= {groups[e.event_id] for e in kept}
    return {
        **parts,
        "dropped_for_group_leakage": dropped,
        "dropped_for_label_availability": unavailable_labels,
        "validation_from": validation_from.isoformat(),
        "test_from": test_from.isoformat(),
        "method": "temporal_with_group_isolation",
    }


def training_readiness(examples: Sequence[TabularExample]) -> dict[str, Any]:
    counts = Counter(e.target for e in examples)
    groups = len(set(leakage_groups(examples).values()))
    blockers = []
    if any(not e.review_id for e in examples):
        blockers.append("rótulos sem vínculo com Review persistida")
    if any(not e.capture_ids for e in examples):
        blockers.append("linhagem de Capture ausente")
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
    return {
        "status": "READY" if not blockers else "BLOCKED_DATA",
        "labeled_examples": len(examples),
        "target_counts": {str(k): v for k, v in sorted(counts.items())},
        "independent_groups": groups,
        "blockers": blockers,
        "policy": "provisional data-sufficiency floor; not a performance threshold",
        "training_authorized": False,
        "authorization_note": "data sufficiency is not dataset-bound training authorization",
    }


def _row_hash(example: TabularExample) -> str:
    payload = json.dumps(
        asdict(example),
        sort_keys=True,
        default=lambda value: value.isoformat(),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class TabularDatasetVersion:
    """Immutable description of a tabular dataset, ready for `dataset_versions`."""

    name: str
    schema_version: str
    feature_schema_version: str
    feature_columns: tuple[str, ...]
    target: str
    split: dict[str, Any]
    content_sha256: str
    readiness: dict[str, Any]
    seed: int
    notes: list[str] = field(default_factory=list)


def build_tabular_dataset_version(
    name: str, examples: Sequence[TabularExample], seed: int = 20260923
) -> TabularDatasetVersion:
    readiness = training_readiness(examples)
    split = group_split(examples, seed=seed) if examples else None
    digest = hashlib.sha256()
    for row_hash in sorted(_row_hash(e) for e in examples):
        digest.update(row_hash.encode())
    return TabularDatasetVersion(
        name=name,
        schema_version=TABULAR_SCHEMA_VERSION,
        feature_schema_version=PINNED_FEATURE_SCHEMA,
        feature_columns=tuple(FEATURE_COLUMNS),
        target=TARGET_NAME,
        split=split.summary() if split else {"counts": {}, "warnings": ["sem exemplos"]},
        content_sha256=digest.hexdigest(),
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
    """xgboost/shap are not installed in backend/.venv; training is not attempted."""


def train_xgboost(
    train: Sequence[TabularExample], seed: int, params: dict[str, Any] | None = None
) -> Any:
    """Offline harness only. Fails closed when data or the library is missing."""
    readiness = training_readiness(train)
    if readiness["status"] != "READY":
        raise TabularExportError(f"BLOCKED_DATA: {readiness['blockers']}")
    if not readiness["training_authorized"]:
        raise TabularExportError("BLOCKED_AUTHORIZATION: autorização vinculada ao dataset ausente")
    try:
        import numpy as np
        import xgboost  # type: ignore[import-not-found]
    except ImportError as exc:
        raise XGBoostUnavailableError("xgboost não instalado no backend/.venv") from exc
    columns = list(FEATURE_COLUMNS)
    matrix = np.array(
        [[np.nan if e.features[c] is None else e.features[c] for c in columns] for e in train]
    )
    model = xgboost.XGBClassifier(random_state=seed, **(params or {}))
    model.fit(matrix, np.array([e.target for e in train]))
    return model


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
