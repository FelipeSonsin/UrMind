"""Fase 9 — histórico descritivo e contrato de hotspot (§24).

Separação obrigatória:

- DESCRIPTIVE_ANALYTICS: fatos já observados ("3 eventos neste trecho nos
  últimos 30 dias"). Implementado aqui.
- PREDICTIVE_ANALYTICS: probabilidade futura ("80% de chance"). Não existe:
  exige histórico mínimo, Ground Truth e validação temporal (§24). Qualquer
  consumidor recebe `prediction_status=BLOCKED_HISTORY` até lá.

Entrada: linhas de `HistoryRepository.observations` (eventos não rejeitados).
Sem relógio implícito: a janela é sempre explícita (`as_of`).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import selectors
import sys
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

HISTORY_SCHEMA_VERSION = "urmind-history-v1"
ANALYTICS_KIND = "DESCRIPTIVE_ANALYTICS"
DEFAULT_WINDOWS_DAYS = (7, 30, 90)
DEFAULT_GRID_DEG = 0.005  # ~500 m in latitude; a display grid, not a model feature

# Future predictive requirements (§24). Declared policy, not measured thresholds.
PREDICTION_REQUIREMENTS: dict[str, Any] = {
    "MIN_HISTORY_WINDOW_DAYS": 180,
    "MIN_GROUND_TRUTH_REVIEWED_EVENTS": 200,
    "MIN_EVENTS": 500,
    "MIN_SEGMENT_COVERAGE": 50,
    "TEMPORAL_SPLIT_REQUIRED": True,
}


def _validate_windows(windows: Sequence[int]) -> None:
    if any(type(days) is not int or days <= 0 for days in windows):
        raise ValueError("history window must be a positive integer number of days")


def _window_counts(
    times: Sequence[datetime], as_of: datetime, windows: Sequence[int]
) -> dict[str, int]:
    return {
        f"events_last_{days}d": sum(as_of - timedelta(days=days) <= t < as_of for t in times)
        for days in windows
    }


def segment_history(
    rows: Sequence[dict[str, Any]],
    as_of: datetime,
    windows: Sequence[int] = DEFAULT_WINDOWS_DAYS,
    *,
    coverage_start: datetime | None = None,
) -> list[dict[str, Any]]:
    """Observed rows are not evidence of complete coverage of a time window.

    ``coverage_start`` is the producer's explicit query-coverage contract, not
    inferred from the oldest row. Without it only observed counts are exposed.
    """
    _validate_windows(windows)
    if coverage_start is not None and coverage_start > as_of:
        raise ValueError("coverage start must not be after as_of")
    by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("road_segment_id") is not None and row["occurred_at"] < as_of:
            by_segment[str(row["road_segment_id"])].append(row)
    out = []
    for segment_id, items in sorted(by_segment.items()):
        times = [item["occurred_at"] for item in items]
        confirmed_times = [i["occurred_at"] for i in items if i.get("status") == "confirmed"]
        classes = Counter(item["urmind_class"] for item in items)
        last = max(times)
        observed = {
            **_window_counts(times, as_of, windows),
            **{
                f"confirmed_{key}": value
                for key, value in _window_counts(confirmed_times, as_of, windows).items()
            },
        }
        out.append(
            {
                "road_segment_id": segment_id,
                "total_events": len(items),
                "counts_scope": "observed_rows",
                "observed_window_counts": observed,
                "window_availability": {str(days): "available" for days in windows},
                **observed,
                "unreviewed_events": sum(1 for i in items if i.get("status") != "confirmed"),
                "class_counts": dict(sorted(classes.items())),
                "recurrent_classes": sorted(c for c, n in classes.items() if n > 1),
                "last_event_at": last.isoformat(),
                "days_since_last_event": round((as_of - last).total_seconds() / 86400, 2),
            }
        )
        for days in windows:
            if coverage_start is None or coverage_start > as_of - timedelta(days=days):
                out[-1]["window_availability"][str(days)] = (
                    "unknown" if coverage_start is None else "insufficient_history"
                )
                out[-1][f"events_last_{days}d"] = None
                out[-1][f"confirmed_events_last_{days}d"] = None
    return out


def class_frequency(rows: Sequence[dict[str, Any]], as_of: datetime) -> dict[str, int]:
    return dict(
        sorted(Counter(r["urmind_class"] for r in rows if r["occurred_at"] < as_of).items())
    )


def grid_aggregation(
    rows: Sequence[dict[str, Any]], as_of: datetime, cell_deg: float = DEFAULT_GRID_DEG
) -> list[dict[str, Any]]:
    """Count observed events per lat/lon grid cell (for maps; no smoothing or inference)."""
    cells: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if (
            row["occurred_at"] >= as_of
            or row.get("latitude") is None
            or row.get("longitude") is None
        ):
            continue
        key = (math.floor(row["latitude"] / cell_deg), math.floor(row["longitude"] / cell_deg))
        cells[key].append(row)
    return [
        {
            "cell": f"{i}:{j}",
            "south": i * cell_deg,
            "west": j * cell_deg,
            "north": (i + 1) * cell_deg,
            "east": (j + 1) * cell_deg,
            "event_count": len(items),
            "class_counts": dict(sorted(Counter(r["urmind_class"] for r in items).items())),
        }
        for (i, j), items in sorted(cells.items())
    ]


def prediction_readiness(
    rows: Sequence[dict[str, Any]], reviewed_events: int | None
) -> dict[str, Any]:
    """Whether a predictive model could even be attempted. Never produces a probability."""
    blockers = []
    if rows:
        span = (max(r["occurred_at"] for r in rows) - min(r["occurred_at"] for r in rows)).days
    else:
        span = 0
    segments = len({r["road_segment_id"] for r in rows if r.get("road_segment_id")})
    req = PREDICTION_REQUIREMENTS
    if span < req["MIN_HISTORY_WINDOW_DAYS"]:
        blockers.append(f"janela histórica {span}d < {req['MIN_HISTORY_WINDOW_DAYS']}d")
    if len(rows) < req["MIN_EVENTS"]:
        blockers.append(f"eventos {len(rows)} < {req['MIN_EVENTS']}")
    if segments < req["MIN_SEGMENT_COVERAGE"]:
        blockers.append(f"trechos cobertos {segments} < {req['MIN_SEGMENT_COVERAGE']}")
    if reviewed_events is None or reviewed_events < req["MIN_GROUND_TRUTH_REVIEWED_EVENTS"]:
        blockers.append(
            f"eventos revisados {reviewed_events} < {req['MIN_GROUND_TRUTH_REVIEWED_EVENTS']}"
        )
    return {
        "prediction_status": "BLOCKED_HISTORY" if blockers else "READY_FOR_TEMPORAL_VALIDATION",
        "blockers": blockers,
        "requirements": req,
        "requirements_policy": "provisional; not measured thresholds",
        "observed": {"history_span_days": span, "events": len(rows), "segments": segments},
    }


def hotspot_report(
    rows: Sequence[dict[str, Any]],
    as_of: datetime,
    *,
    reviewed_events: int | None = None,
    query_mode: str = "RETROSPECTIVE_ANALYTICS",
    coverage_start: datetime | None = None,
) -> dict[str, Any]:
    """Hotspot contract v1: descriptive counts only; ranking is by observed count."""
    if query_mode != "RETROSPECTIVE_ANALYTICS":
        raise ValueError("point-in-time history requires immutable knowledge snapshots")
    segments = segment_history(rows, as_of, coverage_start=coverage_start)
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "analytics_kind": ANALYTICS_KIND,
        "query_mode": query_mode,
        "knowledge_cutoff": None,
        "coverage_start": coverage_start.isoformat() if coverage_start else None,
        "scientific_feature_eligible": False,
        "temporal_provenance": "occurred_at window with current persisted review status",
        "as_of": as_of.isoformat(),
        "segments": sorted(segments, key=lambda s: (-s["total_events"], s["road_segment_id"])),
        "class_frequency": class_frequency(rows, as_of),
        "grid": grid_aggregation(rows, as_of),
        "prediction": prediction_readiness(
            [r for r in rows if r["occurred_at"] < as_of], reviewed_events
        ),
        "wording_rule": "only observed counts; no probability, risk forecast or causal claim",
    }


def describe_segment(summary: dict[str, Any], days: int = 30) -> str:
    """The only sentence form allowed for public history text."""
    _validate_windows((days,))
    count = summary.get(f"confirmed_events_last_{days}d")
    if count is None or (summary.get("window_availability") or {}).get(str(days)) != "available":
        return f"Histórico indisponível para a janela de {days} dias neste trecho"
    if type(count) is not int or count < 0:
        raise ValueError("history count must be a non-negative integer or missing")
    noun = "evento confirmado" if count == 1 else "eventos confirmados"
    return f"{count} {noun} por revisão neste trecho nos últimos {days} dias"


def snapshot_hash(payload: dict[str, Any]) -> str:
    """Integrity only: a digest does not certify the producer or scientific eligibility."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def snapshot_report(payload: dict[str, Any], expected_hash: str) -> dict[str, Any]:
    """Replay archived observations, never consult today's event/review state.

    The caller must obtain the digest from its trusted archive. This function
    does not authorize a dataset or prove when an arbitrary supplied JSON existed.
    """
    if not hmac.compare_digest(snapshot_hash(payload), expected_hash):
        raise ValueError("history snapshot hash mismatch")
    if payload.get("schema_version") != "urmind-history-snapshot-v1":
        raise ValueError("unsupported history snapshot schema")

    def timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise TypeError("snapshot timestamp must be an ISO string")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("snapshot timestamp must include timezone")
        return parsed

    cutoff = timestamp(payload.get("knowledge_cutoff"))
    coverage = payload.get("coverage_start")
    start = timestamp(coverage) if coverage is not None else None
    if start is not None and start > cutoff:
        raise ValueError("invalid snapshot coverage window")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise TypeError("snapshot observations must be a list")
    rows = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise TypeError("invalid snapshot observation")
        row = dict(observation)
        row["occurred_at"] = timestamp(row.get("occurred_at"))
        if row["occurred_at"] >= cutoff or (start is not None and row["occurred_at"] < start):
            raise ValueError("observation outside snapshot window")
        rows.append(row)
    report = hotspot_report(rows, cutoff, coverage_start=start)
    report.update(
        query_mode="POINT_IN_TIME_ANALYTICS",
        knowledge_cutoff=cutoff.isoformat(),
        temporal_provenance="archived observations; source capture time requires trusted archive",
        snapshot_sha256=expected_hash,
    )
    # Deliberately retain scientific_feature_eligible=False: integrity != authorization.
    return report


async def _main(days: int, freeze: bool = False, snapshot_id: str | None = None) -> None:
    from datetime import UTC

    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import HistoryRepository

    _validate_windows((days,))
    as_of = datetime.now(UTC)
    start = as_of - timedelta(days=days)
    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            repository = HistoryRepository(session)
            if snapshot_id is not None:
                payload, digest = await repository.load_snapshot(uuid.UUID(snapshot_id))
                report = snapshot_report(payload, digest)
                report["snapshot_id"] = snapshot_id
            elif freeze:
                payload = await repository.snapshot_observations(days)
                digest = snapshot_hash(payload)
                report = snapshot_report(payload, digest)
                report["snapshot_id"] = str(await repository.save_snapshot(payload, digest))
                await session.commit()
            else:
                rows = await repository.observations(start, as_of)
                report = hotspot_report(rows, as_of, coverage_start=start)
    finally:
        await database.close()
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Relatório descritivo de histórico (Fase 9)")
    parser.add_argument("--days", type=int, default=365)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--freeze", action="store_true", help="Persist a new server-time snapshot")
    mode.add_argument("--snapshot", help="Replay an archived snapshot UUID without current rows")
    # psycopg async cannot run on Windows' default ProactorEventLoop (same as app.worker).
    loop_factory = (
        (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None
    )
    args = parser.parse_args()
    asyncio.run(_main(args.days, args.freeze, args.snapshot), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
