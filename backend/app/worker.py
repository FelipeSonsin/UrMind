"""Worker de inferência: fila → Storage → ONNX → Detection → Event (MASTER_PLAN §7, §9).

    python -m app.worker            # processa continuamente
    python -m app.worker --once     # processa o que houver e sai

Regras do §7.2: idempotência por captura + versão de modelo; job só é arquivado
depois da persistência; falha não vira `inference_completed`; retry limitado e
erro final registrado na captura. Sem modelo promovido: `model_not_available`.
"""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
import time
import uuid
from datetime import UTC, datetime

import structlog

from app.config import get_settings
from app.db.session import Database
from app.logging import configure_logging
from app.ml.serving import PROJECT_ROOT, ModelNotAvailableError, OnnxDetector
from app.repositories.core import (
    CaptureRepository,
    DecisionRepository,
    EventRepository,
    InferenceRepository,
)
from app.schemas.core import BoundingBox, UrmindClass
from app.services.context import gather_context
from app.services.core import CoreService
from app.services.storage import StorageClient, StorageError

log = structlog.get_logger()

VISIBILITY_TIMEOUT_S = 300
MAX_ATTEMPTS = 3
IDLE_SLEEP_S = 5.0


class Worker:
    def __init__(self, database: Database, storage: StorageClient) -> None:
        self.database = database
        self.storage = storage
        self._detector: OnnxDetector | None = None
        self._detector_version: uuid.UUID | None = None

    def _detector_for(self, model) -> OnnxDetector:
        if self._detector is None or self._detector_version != model.id:
            serving = (model.metrics or {}).get("serving", {})
            if not serving.get("onnx_path") or not model.checksum:
                raise ModelNotAvailableError("model_versions sem onnx_path/checksum")
            self._detector = OnnxDetector(PROJECT_ROOT / serving["onnx_path"], model.checksum)
            self._detector_version = model.id
        return self._detector

    async def process_one(self) -> bool:
        """Processa no máximo um job. Devolve False quando a fila está vazia."""
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            job = await queue.read_job(VISIBILITY_TIMEOUT_S)
            await session.commit()
        if job is None:
            return False

        capture_id = uuid.UUID(job["message"]["capture_id"])
        bound = log.bind(job_id=job["msg_id"], capture_id=str(capture_id), attempt=job["read_ct"])
        started = time.perf_counter()
        created: list[uuid.UUID] = []
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            captures = CaptureRepository(session)
            capture = await captures.get(capture_id)
            if capture is None:
                await queue.archive_job(job["msg_id"])
                await session.commit()
                bound.warning("inference_capture_missing")
                return True
            try:
                model = await queue.promoted_vision_model()
                if model is None:
                    raise ModelNotAvailableError("nenhum modelo de visão promovido")
                detector = self._detector_for(model)
                if not await queue.has_detections_from(capture_id, model.id):
                    if not capture.storage_path:
                        raise ValueError("captura sem storage_path")
                    image = await self.storage.download(capture.storage_path)
                    found = await asyncio.to_thread(detector.detect, image)
                    from app.models.core import Detection

                    await captures.add_detections(
                        capture,
                        [
                            Detection(
                                urmind_class=UrmindClass(d.urmind_class).value,
                                confidence=d.confidence,
                                bbox=BoundingBox(**d.bbox).model_dump(),
                                model_version_id=model.id,
                            )
                            for d in found
                        ],
                    )
                service = CoreService(
                    captures, EventRepository(session), DecisionRepository(session)
                )
                consolidated = await service.consolidate_capture(capture_id)
                await queue.set_capture_inference(
                    capture,
                    {
                        "status": "inference_completed",
                        "model_version_id": str(model.id),
                        "model_version": model.version,
                        "onnx_sha256": model.checksum,
                        "execution_provider": detector.provider,
                        "detections": len(capture.detections),
                        "events": len(consolidated["events"]),
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "completed_at": datetime.now(UTC).isoformat(),
                    },
                )
                await queue.archive_job(job["msg_id"])
                await session.commit()
                bound.info(
                    "inference_completed",
                    detection_ids=[str(d.id) for d in capture.detections],
                    event_ids=[str(o["event_id"]) for o in consolidated["events"]],
                )
                created = [o["event_id"] for o in consolidated["events"] if o.get("created")]
            except ModelNotAvailableError as exc:
                await session.rollback()
                await self._final_state(capture_id, job, "model_not_available", str(exc))
                bound.warning("model_not_available", reason=str(exc))
            except (StorageError, ValueError, OSError) as exc:
                await session.rollback()
                if job["read_ct"] >= MAX_ATTEMPTS:
                    await self._final_state(capture_id, job, "inference_failed", type(exc).__name__)
                    bound.error("inference_failed", error=type(exc).__name__)
                else:
                    bound.warning("inference_retry", error=type(exc).__name__)
        for event_id in created:
            await self.enrich_event(event_id, bound)
        return True

    async def enrich_event(self, event_id: uuid.UUID, bound) -> None:
        """Contexto externo depois da inferência persistida (§13): falha nunca desfaz o Event."""
        try:
            async with self.database.sessionmaker() as session:
                service = CoreService(
                    CaptureRepository(session),
                    EventRepository(session),
                    DecisionRepository(session),
                )
                location = await service.event_location(event_id)
            if location is None:
                return
            results = await gather_context(
                location["latitude"], location["longitude"], location["occurred_at"]
            )
            async with self.database.sessionmaker() as session:
                service = CoreService(
                    CaptureRepository(session),
                    EventRepository(session),
                    DecisionRepository(session),
                )
                applied = await service.apply_context(event_id, results)
                await session.commit()
            bound.info("event_context_applied", event_id=str(event_id), context=applied["context"])
        except Exception as exc:  # noqa: BLE001 — contexto é opcional; o evento já existe
            bound.warning("event_context_failed", event_id=str(event_id), error=type(exc).__name__)

    async def _final_state(
        self, capture_id: uuid.UUID, job: dict, status: str, detail: str
    ) -> None:
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            capture = await CaptureRepository(session).get(capture_id)
            if capture is not None:
                await queue.set_capture_inference(
                    capture,
                    {
                        "status": status,
                        "detail": detail,
                        "attempts": job["read_ct"],
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
            await queue.archive_job(job["msg_id"])
            await session.commit()


async def run(once: bool) -> int:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    worker = Worker(database, StorageClient(settings))
    try:
        while True:
            try:
                processed = await worker.process_one()
            except Exception as exc:
                log.error("worker_iteration_failed", error=type(exc).__name__)
                if once:
                    raise
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            if not processed:
                if once:
                    return 0
                await asyncio.sleep(IDLE_SLEEP_S)
    finally:
        await database.close()


async def print_report(kind: str) -> int:
    """Relatórios de observabilidade (§19) sobre a conexão que já existe."""
    import json

    from app.observability import drift_status, model_lineage, slow_queries

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            if kind == "stats":
                report: dict = await InferenceRepository(session).stats()
            elif kind == "slow-queries":
                report = await slow_queries(session)
            elif kind == "lineage":
                report = await model_lineage(session)
            else:
                report = await drift_status(session)
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
    parser.add_argument(
        "--drift", action="store_true", help="há amostra suficiente para falar de drift?"
    )
    parser.add_argument(
        "--lineage", action="store_true", help="cadeia dataset → treino → ONNX → modelo → detecção"
    )
    args = parser.parse_args(argv)
    report = (
        "stats"
        if args.stats
        else "slow-queries"
        if args.slow_queries
        else "lineage"
        if args.lineage
        else "drift"
        if args.drift
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
