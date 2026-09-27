"""Worker de fila para capturas e enriquecimento de endereço.

    python -m app.worker            # processa continuamente
    python -m app.worker --once     # processa o que houver e sai

Sem detector configurado, cada captura segue para revisão humana. O job só é
arquivado após persistir esse estado.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import selectors
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.config import get_settings
from app.db.session import Database
from app.logging import configure_logging
from app.repositories.core import (
    CaptureRepository,
    EventRepository,
    InferenceRepository,
)
from app.schemas.core import Coordinate
from app.services.context import NominatimReverse, OpenMeteoRain, gather_context
from app.services.external_sources.http import ExternalHttpClient
from app.services.external_sources.sidra import municipal_population_query

log = structlog.get_logger()

VISIBILITY_TIMEOUT_S = 300
IDLE_SLEEP_S = 5.0


class Worker:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def process_pending_address(self, capture_id: uuid.UUID | None = None) -> bool:
        """Retry explicit mobile address requests."""
        async with self.database.sessionmaker() as session:
            job = await CaptureRepository(session).reserve_pending_address(capture_id)
            await session.commit()
        if job is None:
            return False
        if job["address_status"] == "address_pending" and job["request_id"]:
            async with ExternalHttpClient(
                user_agent=get_settings().external_http_user_agent,
                timeout_seconds=3,
                max_attempts=1,
            ) as client:
                result = await NominatimReverse(client, sessions=self.database.sessionmaker).fetch(
                    job["latitude"], job["longitude"], job["captured_at"]
                )
            if result.status == "ok":
                address = {
                    "status": result.status,
                    "source": result.source,
                    "fetched_at": result.fetched_at,
                    "provenance": result.provenance,
                    **{
                        name: result.data.get(name)
                        for name in ("road", "suburb", "city", "attribution")
                    },
                }
                async with self.database.sessionmaker() as session:
                    await CaptureRepository(session).save_address(
                        job["id"], job["request_id"], address
                    )
                    await session.commit()
        if job["context_status"] == "pending" and job["context_request_id"]:
            settings = get_settings()
            sidra_query = (
                municipal_population_query(settings.ibge_sidra_municipality_code)
                if settings.ibge_sidra_municipality_code
                else None
            )
            providers: dict[str, dict] = {}
            try:
                results = await gather_context(
                    job["latitude"],
                    job["longitude"],
                    job["captured_at"],
                    providers=(OpenMeteoRain,),
                    include_sidra=sidra_query is not None,
                    sidra_query=sidra_query,
                )
                providers = {result.source: result.as_payload() for result in results}
            except Exception as exc:  # noqa: BLE001 - external context must not fail a report
                providers["open_meteo_rain"] = {
                    "status": "context_unavailable",
                    "error": type(exc).__name__,
                }
                if sidra_query is not None:
                    providers["ibge_sidra"] = {
                        "status": "context_unavailable",
                        "error": type(exc).__name__,
                    }
            if sidra_query is None:
                providers["ibge_sidra"] = {"status": "not_applicable"}
            try:
                async with self.database.sessionmaker() as session:
                    snapped = await EventRepository(session).snap_to_road(
                        Coordinate(latitude=job["latitude"], longitude=job["longitude"])
                    )
                road = (
                    {
                        "status": "ok",
                        "source": "postgis_road_segments",
                        "road_segment_id": str(snapped["road_segment_id"]),
                        "distance_m": snapped["distance_m"],
                        "latitude": snapped["latitude"],
                        "longitude": snapped["longitude"],
                    }
                    if snapped is not None
                    else {
                        "status": "context_unavailable",
                        "source": "postgis_road_segments",
                        "reason": "no_segment_within_radius",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - missing road data is degradable
                road = {
                    "status": "context_unavailable",
                    "source": "postgis_road_segments",
                    "reason": type(exc).__name__,
                }
            async with self.database.sessionmaker() as session:
                await CaptureRepository(session).save_report_context(
                    job["id"],
                    job["context_request_id"],
                    {
                        "status": "processed",
                        "request_id": job["context_request_id"],
                        "at": datetime.now(UTC).isoformat(),
                        "providers": providers,
                        "road": road,
                    },
                )
                await session.commit()
        return True

    async def process_one(self) -> bool:
        """Route one queued capture to manual review and archive its job."""
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            job = await queue.read_job(VISIBILITY_TIMEOUT_S)
            await session.commit()
        if job is None:
            return False

        capture_id = uuid.UUID(job["message"]["capture_id"])
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            capture = await CaptureRepository(session).get(capture_id)
            if capture is not None:
                saved = (capture.quality or {}).get("inference") or {}
                if saved.get("status") not in {"needs_review", "analysis_completed"}:
                    status = (
                        "location_required"
                        if capture.source in {"pwa_photo", "exif_upload"} and capture.point is None
                        else "needs_review"
                    )
                    await queue.set_capture_inference(
                        capture,
                        {
                            "status": status,
                            "reason": "manual_review_required",
                            "at": datetime.now(UTC).isoformat(),
                        },
                    )
            await queue.archive_job(job["msg_id"])
            await session.commit()
        log.info("capture_routed_to_review", capture_id=str(capture_id))
        return True


class Supervision:
    """Optional heartbeat and graceful stop for a supervised local Worker.

    Enabled by URMIND_WORKER_STATE_DIR (scripts/deploy/worker.ps1 sets it). A
    `stop.request` file ends the loop between jobs: a hidden Windows console
    process has no reliable signal for a clean shutdown.
    """

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self.started_at = datetime.now(UTC).isoformat()
        self.jobs = 0
        self.errors = 0
        self.last_job_at: str | None = None
        self.last_error: str | None = None

    def stop_requested(self) -> bool:
        return self.directory is not None and (self.directory / "stop.request").is_file()

    def beat(self, worker: Worker, *, processed: bool, error: str | None = None) -> None:
        if processed:
            self.jobs += 1
            self.last_job_at = datetime.now(UTC).isoformat()
        if error is not None:
            self.errors += 1
            self.last_error = error
        if self.directory is None:
            return
        state = {
            "pid": os.getpid(),
            "started_at": self.started_at,
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "queue": "inference_jobs",
            "jobs_processed": self.jobs,
            "iteration_errors": self.errors,
            "last_job_at": self.last_job_at,
            "last_error": self.last_error,
        }
        target = self.directory / "heartbeat.json"
        partial = target.with_suffix(".json.tmp")
        try:
            partial.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(partial, target)
        except OSError as exc:
            # A reader or antivirus holding the file must never stop the Worker;
            # the next iteration writes a fresh heartbeat.
            log.warning("worker_heartbeat_write_failed", error=type(exc).__name__)


async def run(once: bool) -> int:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    worker = Worker(database)
    state_dir = os.environ.get("URMIND_WORKER_STATE_DIR")
    supervision = Supervision(Path(state_dir) if state_dir else None)
    # First heartbeat lets supervision see the process before any job.
    supervision.beat(worker, processed=False)
    try:
        while not supervision.stop_requested():
            try:
                await worker.process_pending_address()
                processed = await worker.process_one()
            except Exception as exc:
                log.error("worker_iteration_failed", error=type(exc).__name__)
                supervision.beat(worker, processed=False, error=type(exc).__name__)
                if once:
                    raise
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            supervision.beat(worker, processed=processed)
            if not processed:
                if once:
                    return 0
                await asyncio.sleep(IDLE_SLEEP_S)
        log.info("worker_stop_requested", jobs_processed=supervision.jobs)
        return 0
    finally:
        await database.close()


async def print_report(kind: str) -> int:
    """Relatórios de observabilidade (§19) sobre a conexão que já existe."""
    import json

    from app.observability import slow_queries

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            if kind == "stats":
                report: dict = await InferenceRepository(session).stats()
            elif kind == "slow-queries":
                report = await slow_queries(session)
            else:
                raise ValueError(f"unknown report: {kind}")
    finally:
        await database.close()
    print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stats", action="store_true", help="métricas da fila e das inferências")
    parser.add_argument(
        "--slow-queries", action="store_true", help="SQL mais caro (pg_stat_statements)"
    )
    args = parser.parse_args(argv)
    report = (
        "stats"
        if args.stats
        else "slow-queries"
        if args.slow_queries
        else None
    )
    if report:
        coroutine = print_report(report)
        if sys.platform == "win32":
            return asyncio.run(
                coroutine,
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        return asyncio.run(coroutine)
    if sys.platform == "win32":
        return asyncio.run(
            run(args.once),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    return asyncio.run(run(args.once))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Worker", "main"]
