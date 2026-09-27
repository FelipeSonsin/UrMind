"""Resolve independent human reviews for an occurrence."""

from __future__ import annotations

from typing import Any

REVIEW_SCHEMA_VERSION = "urmind-review-v1"


def review_resolution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "unreviewed", "selected": None}

    def label(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["decision"],
            row.get("corrected_class"),
            row.get("corrected_latitude"),
            row.get("corrected_longitude"),
        )

    if rows[-1].get("adjudicated") is True and rows[-1].get("reviewer_role") == "admin":
        return {"status": "adjudicated", "selected": rows[-1]}
    if any(row.get("order_source") == "legacy_backfill" for row in rows):
        return {"status": "legacy_order_uncertain", "selected": None}

    by_reviewer: dict[str, dict[str, Any]] = {}
    conflict_observed = False
    for row in rows:
        by_reviewer[str(row["reviewer"])] = row
        if len({label(vote) for vote in by_reviewer.values()}) > 1:
            conflict_observed = True

    votes = list(by_reviewer.values())
    if conflict_observed:
        return {"status": "conflicted", "selected": None}
    if any(row.get("reviewer_role") not in {"reviewer", "admin"} for row in votes):
        return {"status": "role_unverified", "selected": None}
    if len(votes) < 2:
        return {"status": "requires_second_review", "selected": None}
    return {"status": "consensus", "selected": votes[-1]}
