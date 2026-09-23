"""Revisões confirmadas → candidatos à próxima DatasetVersion (MASTER_PLAN §16.2).

Revisão confirmada alimenta a próxima versão do dataset, mas **não** causa
retreinamento automático e **não** altera o dataset atual. Este comando só exporta
um lote rastreável; ele ainda precisa das auditorias humanas do pipeline de datasets
antes de qualquer autorização de treino.

    python -m app.services.review_export            # grava em datasets/review_candidates/
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import selectors
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.services.storage import StorageError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "datasets" / "review_candidates"
TRAINING_STATUS = "candidate_not_training_authorized"
REVIEW_SCHEMA_VERSION = "urmind-review-v1"


def review_resolution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Two independent matching votes or an explicit admin adjudication establish GT."""
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
        selected = rows[-1]
        return {"status": "adjudicated", "selected": selected}
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


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"não serializável: {type(value).__name__}")


def candidate_record(row: dict[str, Any]) -> dict[str, Any]:
    """Uma revisão vira um candidato. Label final = corrigida quando houver, senão a inferida."""
    return {
        "review": {
            "id": row["review_id"],
            "decision": row["decision"],
            "reviewer": row["reviewer"],
            "reviewed_at": row["reviewed_at"],
            "notes": row["notes"],
            "schema_version": row.get("review_schema_version"),
            "ground_truth_status": row.get("ground_truth_status", "unverified"),
            "reviewer_role": row.get("reviewer_role"),
        },
        "label": {
            "urmind_class": row["corrected_class"] or row["inferred_class"],
            "source": "human_correction" if row["decision"] == "correct" else "human_confirmation",
            "inferred_class": row["inferred_class"],
        },
        "location": {
            "original": {"latitude": row["latitude"], "longitude": row["longitude"]},
            "corrected": (
                {"latitude": row["corrected_latitude"], "longitude": row["corrected_longitude"]}
                if row["corrected_latitude"] is not None
                else None
            ),
        },
        "capture": {
            "id": row["capture_id"],
            "capture_key": row["capture_key"],
            "storage_bucket": "captures",
            "storage_path": row["storage_path"],
            "image_sha256": row["image_sha256"],
            "source": row["source"],
            "source_location": row["source_location"],
            "captured_at": row["captured_at"],
        },
        "event": {
            "id": row["event_id"],
            "event_key": row["event_key"],
            "visual_confidence": row["visual_confidence"],
        },
        "original_detections": row["original_detections"],
        "inference_lineage": {
            "model_version_id": row.get("model_version_id"),
            "model_name": row["model_name"],
            "model_version": row["model_version"],
            "model_checksum": row["model_checksum"],
            "dataset_version_id": row.get("dataset_version_id"),
            "dataset_name": row["dataset_name"],
            "dataset_version": row["dataset_version"],
        },
        "training_status": TRAINING_STATUS,
        "dataset_eligibility": row.get("ground_truth_status") in {"consensus", "adjudicated"}
        and row["decision"] in {"confirm", "correct"},
    }


def write_batch(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    data_path = output_dir / f"review_candidates_{stamp}.jsonl"
    body = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n"
        for record in records
    )
    # Bytes exatos: write_text no Windows converteria as quebras de linha e quebraria o hash.
    data_path.write_bytes(body.encode("utf-8"))
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "records": len(records),
        "file": data_path.name,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "purpose": "candidatos à próxima DatasetVersion; não altera o dataset vigente",
        "training_status": TRAINING_STATUS,
        "retraining_triggered": False,
        "next_step": "auditoria humana e split por grupo no pipeline de datasets",
    }
    data_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def eligible_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only conflict-free independent consensus or admin adjudication is exportable."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["event_id"]), []).append(row)
    eligible = []
    for group in grouped.values():
        resolution = review_resolution(group)
        selected = resolution["selected"]
        if (
            selected is not None
            and selected["decision"] in {"confirm", "correct"}
            and selected.get("capture_id") is not None
            and selected.get("storage_path")
            and selected.get("image_sha256")
            and selected.get("original_detections")
            and len((selected.get("evidence") or {}).get("detection_ids") or ())
            == len(selected["original_detections"])
            and {
                str(value) for value in (selected.get("evidence") or {}).get("detection_ids") or ()
            }
            == {detection.get("id") for detection in selected["original_detections"]}
            and selected.get("model_version_id") is not None
            and selected.get("dataset_version_id") is not None
            and selected.get("review_schema_version") == REVIEW_SCHEMA_VERSION
            and set((selected.get("evidence") or {}).get("capture_ids") or ())
            == {str(selected["capture_id"])}
            and all(
                detection.get("capture_id") == str(selected["capture_id"])
                and detection.get("model_version_id") == str(selected["model_version_id"])
                for detection in selected["original_detections"]
            )
            and selected["inferred_class"]
            in {detection.get("urmind_class") for detection in selected["original_detections"]}
        ):
            candidate = candidate_record({**selected, "ground_truth_status": resolution["status"]})
            candidate["review_provenance"] = [
                {
                    "review_id": vote["review_id"],
                    "reviewer": vote["reviewer"],
                    "reviewer_role": vote.get("reviewer_role"),
                    "decision": vote["decision"],
                    "corrected_class": vote.get("corrected_class"),
                    "corrected_latitude": vote.get("corrected_latitude"),
                    "corrected_longitude": vote.get("corrected_longitude"),
                    "reviewed_at": vote["reviewed_at"],
                    "adjudicated": vote.get("adjudicated", False),
                }
                for vote in group
            ]
            eligible.append(candidate)
    return eligible


async def verify_candidate_objects(records: list[dict[str, Any]], storage: Any) -> None:
    """Fail closed before export if any evidence object is absent or changed."""
    for record in records:
        capture = record["capture"]
        try:
            payload = await storage.download(capture["storage_path"])
        except StorageError as exc:
            raise ValueError(f"capture {capture['id']} has unavailable Storage evidence") from exc
        if hashlib.sha256(payload).hexdigest() != capture["image_sha256"]:
            raise ValueError(f"capture {capture['id']} evidence checksum mismatch")


async def export(output_dir: Path) -> dict[str, Any]:
    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import DecisionRepository
    from app.services.storage import StorageClient

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            rows = await DecisionRepository(session).dataset_candidates()
    finally:
        await database.close()
    candidates = eligible_candidates(rows)
    await verify_candidate_objects(candidates, StorageClient(get_settings()))
    return write_batch(candidates, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    coroutine = export(args.out)
    if sys.platform == "win32":
        manifest = asyncio.run(
            coroutine, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    else:
        manifest = asyncio.run(coroutine)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
