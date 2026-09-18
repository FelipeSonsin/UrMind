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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "datasets" / "review_candidates"
TRAINING_STATUS = "candidate_not_training_authorized"


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
        },
        "label": {
            "urmind_class": row["corrected_class"] or row["inferred_class"],
            "source": "human_correction" if row["corrected_class"] else "human_confirmation",
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
            "model_name": row["model_name"],
            "model_version": row["model_version"],
            "model_checksum": row["model_checksum"],
            "dataset_name": row["dataset_name"],
            "dataset_version": row["dataset_version"],
        },
        "training_status": TRAINING_STATUS,
    }


def write_batch(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
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


async def export(output_dir: Path) -> dict[str, Any]:
    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import DecisionRepository

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            rows = await DecisionRepository(session).dataset_candidates()
    finally:
        await database.close()
    return write_batch([candidate_record(row) for row in rows], output_dir)


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
