"""Inspect a real photo report through the current human review and publication flow.

This command reads the latest capture or one selected by ID. It does not upload,
infer, train, or create any data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.core import PublicRepository

OK = "ok"
PENDING = "pendente"
MISSING = "falta"


def _step(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"etapa": name, "status": status, "detalhe": detail, **extra}


async def inspect(session: AsyncSession, capture_id: uuid.UUID | None) -> dict[str, Any]:
    capture = (
        (
            await session.execute(
                text(
                    "select id, source, source_location, captured_at, storage_path, quality, "
                    "point is not null as tem_ponto from public.captures "
                    + ("where id = :id " if capture_id else "")
                    + "order by created_at desc limit 1"
                ),
                {"id": capture_id} if capture_id else {},
            )
        )
        .mappings()
        .first()
    )
    if capture is None:
        return {
            "pronto": False,
            "motivo": "nenhuma captura registrada ainda — envie uma foto real pelo app e rode de novo",
            "etapas": [],
        }

    quality = capture["quality"] or {}
    human_review = quality.get("human_review") or {}
    has_location = bool(capture["tem_ponto"] or human_review.get("corrected_location"))
    review_status = human_review.get("status")
    steps = [
        _step(
            "captura",
            OK,
            f"{capture['source']} em {capture['captured_at']}",
            capture_id=str(capture["id"]),
        ),
        _step(
            "localização",
            OK if has_location else MISSING,
            f"origem {capture['source_location']}" if has_location else "localização pendente",
        ),
        _step(
            "storage",
            OK if capture["storage_path"] else MISSING,
            "objeto no bucket privado" if capture["storage_path"] else "captura sem arquivo",
        ),
        _step(
            "revisão humana",
            OK if review_status == "confirmed" else PENDING,
            review_status or "aguardando decisão de revisor",
        ),
    ]

    event = (
        (
            await session.execute(
                text(
                    "select e.id, e.urmind_class, e.status from public.events e "
                    "where e.capture_id = :id "
                    "or e.factors->'evidence'->'capture_ids' ? :capture_id "
                    "order by e.created_at desc limit 1"
                ),
                {"id": capture["id"], "capture_id": str(capture["id"])},
            )
        )
        .mappings()
        .first()
    )
    if event is None:
        steps.append(_step("evento", PENDING, "nenhum evento confirmado para esta captura"))
        return {
            "pronto": False,
            "capture_id": str(capture["id"]),
            "pendentes": [step["etapa"] for step in steps if step["status"] != OK],
            "etapas": steps,
        }

    steps.append(
        _step(
            "evento",
            OK if event["status"] == "confirmed" else PENDING,
            f"{event['urmind_class']} ({event['status']})",
            event_id=str(event["id"]),
        )
    )
    published = await session.scalar(
        text(
            "select count(*) from public.events e where e.id = :id and "
            + PublicRepository._PUBLISHED
        ),
        {"id": event["id"]},
    )
    steps.append(
        _step(
            "painel público",
            OK if published else PENDING,
            "marcador autorizado após revisão" if published else "publicação pendente",
        )
    )
    pending = [step["etapa"] for step in steps if step["status"] != OK]
    return {
        "pronto": not pending,
        "capture_id": str(capture["id"]),
        "event_id": str(event["id"]),
        "pendentes": pending,
        "etapas": steps,
    }


async def _main(capture_id: uuid.UUID | None) -> int:
    from app.config import get_settings
    from app.db.session import Database

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            report = await inspect(session, capture_id)
    finally:
        await database.close()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("pronto") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=uuid.UUID, default=None)
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        return asyncio.run(
            _main(args.capture),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    return asyncio.run(_main(args.capture))


if __name__ == "__main__":
    raise SystemExit(main())
