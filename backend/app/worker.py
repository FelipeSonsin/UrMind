"""Worker de inferência: fila → Storage → ONNX → Detection → Event (MASTER_PLAN §7, §9).

    python -m app.worker            # processa continuamente
    python -m app.worker --once     # processa o que houver e sai

Regras do §7.2: idempotência por captura + versão de modelo; job só é arquivado
depois da persistência; falha não vira `inference_completed`; retry limitado e
erro final registrado na captura. Sem modelo shadow autorizado: `model_not_available`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import selectors
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from app.schemas.core import BoundingBox, CaptureProcessingStatus, Coordinate, UrmindClass
from app.schemas.issue_taxonomy import model_may_emit
from app.services.context import NominatimReverse, OpenMeteoRain, gather_context
from app.services.core import CoreService
from app.services.external_sources.http import ExternalHttpClient
from app.services.external_sources.sidra import municipal_population_query
from app.services.photo_reference import urban_auxiliary_safe
from app.services.storage import StorageClient, StorageError

log = structlog.get_logger()

VISIBILITY_TIMEOUT_S = 300
MAX_ATTEMPTS = 3
IDLE_SLEEP_S = 5.0


# Stored stages after detections and events are committed; a retry resumes the
# analysis from the saved event_ids instead of re-running detection.
_RESUMABLE_ANALYSIS_STAGES = frozenset(
    {
        CaptureProcessingStatus.DETECTION_COMPLETED.value,
        CaptureProcessingStatus.ENRICHING_CONTEXT.value,
        CaptureProcessingStatus.BUILDING_FEATURES.value,
        CaptureProcessingStatus.ASSESSING.value,
    }
)


class Worker:
    def __init__(self, database: Database, storage: StorageClient) -> None:
        self.database = database
        self.storage = storage
        self._detector: OnnxDetector | None = None
        self._detector_version: uuid.UUID | None = None
        self._score_threshold: float | None = None
        self._max_detections: int | None = None
        self._inference_profile_sha256: str | None = None

    async def process_pending_address(self, capture_id: uuid.UUID | None = None) -> bool:
        """Retry explicit mobile address requests, independently of detector availability."""
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

    def _detector_for(self, model) -> OnnxDetector:
        if self._detector is None or self._detector_version != model.id:
            serving = (model.metrics or {}).get("serving", {})
            required = (
                "onnx_path",
                "score_threshold",
                "nms_threshold",
                "input_size",
                "class_names",
                "model_contract_path",
                "model_contract_sha256",
            )
            if (
                not model.checksum
                or not isinstance(serving.get("onnx_path"), str)
                or not isinstance(serving.get("model_contract_path"), str)
                or not isinstance(serving.get("model_contract_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", serving["model_contract_sha256"]) is None
                or any(serving.get(field) is None for field in required)
                or isinstance(serving["score_threshold"], bool)
                or not isinstance(serving["score_threshold"], (int, float))
                or not 0.0 < float(serving["score_threshold"]) <= 1.0
                or isinstance(serving["nms_threshold"], bool)
                or not isinstance(serving["nms_threshold"], (int, float))
                or not isinstance(serving["input_size"], list)
                or len(serving["input_size"]) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in serving["input_size"]
                )
                or not isinstance(serving["class_names"], list)
            ):
                raise ModelNotAvailableError("ModelVersion sem contrato de serving completo")
            try:
                input_size = (
                    int(serving["input_size"][0]),
                    int(serving["input_size"][1]),
                )
                class_names = tuple(str(value) for value in serving["class_names"])
                nms_threshold = float(serving["nms_threshold"])
            except (TypeError, ValueError) as exc:
                raise ModelNotAvailableError(
                    "ModelVersion com contrato de serving inválido"
                ) from exc
            onnx_path = (PROJECT_ROOT / serving["onnx_path"]).resolve()
            if not onnx_path.is_relative_to((PROJECT_ROOT / "models" / "serving").resolve()):
                raise ModelNotAvailableError("ONNX fora do diretório de serving")
            # Perfil por classe opcional, no mesmo formato do manifesto do navegador;
            # sem ele o comportamento é o agnóstico de sempre.
            nms_mode = serving.get("nms", "class_agnostic")
            class_score_thresholds = serving.get("class_score_thresholds")
            if class_score_thresholds is not None and (
                not isinstance(class_score_thresholds, list)
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in class_score_thresholds
                )
            ):
                raise ModelNotAvailableError("ModelVersion com limiares por classe inválidos")
            # A versioned inference profile must state its letterbox: the profile hash
            # covers it, so a missing key would silently run another profile. Only
            # legacy registrations without a profile keep the original YOLOX preproc.
            if "inference_profile_sha256" in serving and "letterbox_upscale" not in serving:
                raise ModelNotAvailableError(
                    "perfil de inferência sem letterbox_upscale explícito; re-registre o modelo"
                )
            letterbox_upscale = serving.get("letterbox_upscale", True)
            if not isinstance(letterbox_upscale, bool):
                raise ModelNotAvailableError("ModelVersion com letterbox_upscale inválido")
            self._detector = OnnxDetector(
                onnx_path,
                model.checksum,
                nms_threshold=nms_threshold,
                expected_input_size=input_size,
                expected_class_names=class_names,
                contract_path=PROJECT_ROOT / serving["model_contract_path"],
                expected_contract_sha256=serving["model_contract_sha256"],
                nms_mode=nms_mode,
                class_score_thresholds=class_score_thresholds,
                letterbox_upscale=letterbox_upscale,
            )
            max_detections = serving.get("max_detections")
            if max_detections is not None and (
                isinstance(max_detections, bool)
                or not isinstance(max_detections, int)
                or not 0 < max_detections <= 1000
            ):
                raise ModelNotAvailableError("ModelVersion com max_detections inválido")
            self._detector_version = model.id
            self._score_threshold = float(serving["score_threshold"])
            self._max_detections = max_detections
            # Same weights with another threshold/NMS are another configuration.
            self._inference_profile_sha256 = serving.get("inference_profile_sha256")
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
        event_ids: list[uuid.UUID] = []
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
                saved = (capture.quality or {}).get("inference") or {}
                if capture.source in {"pwa_photo", "exif_upload"} and capture.point is None:
                    await queue.set_capture_inference(capture, {"status": "location_required"})
                    await queue.archive_job(job["msg_id"])
                    await session.commit()
                    return True
                if saved.get("status") in {
                    "analysis_completed",
                    "no_detection",
                    "no_supported_detection",
                    "needs_review",
                }:
                    await queue.archive_job(job["msg_id"])
                    await session.commit()
                    return True
                if saved.get("status") in _RESUMABLE_ANALYSIS_STAGES:
                    event_ids = [uuid.UUID(value) for value in saved.get("event_ids") or []]
                else:
                    await queue.set_capture_inference(
                        capture,
                        {"status": "processing_detection", "at": datetime.now(UTC).isoformat()},
                    )
                    await session.commit()
                    settings = get_settings()
                    model = await queue.configured_vision_model(
                        settings.vision_execution_mode, settings.shadow_model_version_id
                    )
                    if model is None:
                        raise ModelNotAvailableError("nenhum modelo autorizado neste modo")
                    try:
                        detector = self._detector_for(model)
                    except ImportError as exc:
                        raise ModelNotAvailableError("runtime ONNX indisponível") from exc
                    auxiliary: dict[str, Any] | None = None
                    if not await queue.has_detections_from(capture_id, model.id):
                        if not capture.storage_path:
                            raise ValueError("captura sem storage_path")
                        image = await self.storage.download(capture.storage_path)
                        if self._score_threshold is None:
                            raise ModelNotAvailableError(
                                "ModelVersion sem confidence threshold operacional"
                            )
                        try:
                            found = await asyncio.to_thread(
                                detector.detect, image, score_threshold=self._score_threshold
                            )
                        except ImportError as exc:
                            raise ModelNotAvailableError("runtime YOLOX indisponível") from exc
                        # Categorias urbanas fora do YOLOX: só sugestão interna para a
                        # revisão (nunca Detection, Event ou publicação); falha não para nada.
                        auxiliary = await asyncio.to_thread(urban_auxiliary_safe, image)
                        if self._max_detections is not None:
                            # Same cap as the browser profile, highest scores first.
                            found = sorted(found, key=lambda d: -d.confidence)[
                                : self._max_detections
                            ]
                        from app.models.core import Detection

                        unsupported = sorted(
                            {d.urmind_class for d in found if not model_may_emit(d.urmind_class)}
                        )
                        if unsupported:
                            # DATA_REQUIRED/REVIEW_ONLY classes never become a
                            # model Detection, whatever the ONNX outputs.
                            raise ModelNotAvailableError(
                                f"modelo emitiu classe sem suporte na taxonomia: {unsupported}"
                            )
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
                    # Detections persist before consolidation. A crash here resumes
                    # through the normal path: detection is skipped for this model
                    # and event consolidation is idempotent by event_key.
                    await queue.set_capture_inference(
                        capture,
                        {
                            "status": CaptureProcessingStatus.BUILDING_EVENT.value,
                            "at": datetime.now(UTC).isoformat(),
                        },
                    )
                    await session.commit()
                    service = CoreService(
                        captures, EventRepository(session), DecisionRepository(session)
                    )
                    consolidated = await service.consolidate_capture(capture_id)
                    event_ids = [item["event_id"] for item in consolidated["events"]]
                    await queue.set_capture_inference(
                        capture,
                        {
                            "status": "detection_completed",
                            "model_version_id": str(model.id),
                            "model_version": model.version,
                            "model_status": model.operational_status,
                            "onnx_sha256": model.checksum,
                            "inference_profile_sha256": self._inference_profile_sha256,
                            "execution_provider": detector.provider,
                            "detections": len(capture.detections),
                            "events": len(event_ids),
                            "event_ids": [str(item) for item in event_ids],
                            "context_done_event_ids": [],
                            "latency_ms": round((time.perf_counter() - started) * 1000),
                            **({"urban_auxiliary": auxiliary} if auxiliary else {}),
                            "detection_completed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    await session.commit()
                    bound.info(
                        "detection_completed",
                        detection_ids=[str(d.id) for d in capture.detections],
                        event_ids=[str(item) for item in event_ids],
                    )
            except ModelNotAvailableError as exc:
                await session.rollback()
                await self._final_state(capture_id, job, "model_not_available", str(exc))
                bound.warning("model_not_available", reason=str(exc))
                return True
            except (StorageError, ValueError, OSError) as exc:
                await session.rollback()
                if job["read_ct"] >= MAX_ATTEMPTS:
                    await self._final_state(capture_id, job, "inference_failed", type(exc).__name__)
                    bound.error("inference_failed", error=type(exc).__name__)
                else:
                    bound.warning("inference_retry", error=type(exc).__name__)
                return True
        if not event_ids:
            async with self.database.sessionmaker() as session:
                capture = await CaptureRepository(session).get(capture_id)
                inference = ((capture.quality or {}).get("inference") or {}) if capture else {}
                detection_count = inference.get(
                    "detections", len(getattr(capture, "detections", [])) if capture else 0
                )
            await self._set_capture_stage(
                capture_id, "no_event" if detection_count else "no_supported_detection"
            )
        else:
            try:
                await self._finish_analysis(capture_id, event_ids, bound)
            except Exception as exc:  # noqa: BLE001 — qualquer falha persistente deve encerrar o job
                if job["read_ct"] >= MAX_ATTEMPTS:
                    await self._final_state(capture_id, job, "inference_failed", type(exc).__name__)
                    bound.error("analysis_failed", error=type(exc).__name__)
                else:
                    bound.warning("analysis_retry", error=type(exc).__name__)
                return True
        async with self.database.sessionmaker() as session:
            await InferenceRepository(session).archive_job(job["msg_id"])
            await session.commit()
        return True

    async def _finish_analysis(
        self, capture_id: uuid.UUID, event_ids: list[uuid.UUID], bound
    ) -> None:
        await self._set_capture_stage(capture_id, "enriching_context")
        async with self.database.sessionmaker() as session:
            capture = await CaptureRepository(session).get(capture_id)
            completed_context = (
                set(
                    ((capture.quality or {}).get("inference") or {}).get("context_done_event_ids")
                    or []
                )
                if capture
                else set()
            )
        for event_id in event_ids:
            if str(event_id) not in completed_context:
                await self.enrich_event(event_id, bound, capture_id)
        # FeatureBuilder snapshot and RiskAssessment/DecisionTrace are written in
        # one transaction per event; `assessing` is reported by the API while the
        # stored stage is complete but the snapshot/trace is still missing.
        await self._set_capture_stage(capture_id, CaptureProcessingStatus.BUILDING_FEATURES.value)
        async with self.database.sessionmaker() as session:
            service = CoreService(
                CaptureRepository(session),
                EventRepository(session),
                DecisionRepository(session),
            )
            complete = True
            for event_id in event_ids:
                event = await service.events.get(event_id)
                if event is None:
                    complete = False
                    break
                assessment = await DecisionRepository(session).latest_risk(event_id)
                factors = (assessment.factors or {}) if assessment else {}
                if not factors.get("phase4_snapshot") or not factors.get("decision_trace"):
                    await service.assess_event(event_id)
                    assessment = await DecisionRepository(session).latest_risk(event_id)
                    factors = (assessment.factors or {}) if assessment else {}
                if not factors.get("phase4_snapshot") or not factors.get("decision_trace"):
                    complete = False
                    break
            await session.commit()
        if not complete:
            raise RuntimeError("análise incompleta; job permanece na fila para retomada")
        await self._set_capture_stage(capture_id, "analysis_completed")

    async def _set_capture_stage(self, capture_id: uuid.UUID, status: str, **details) -> None:
        async with self.database.sessionmaker() as session:
            capture = await CaptureRepository(session).get(capture_id)
            if capture is None:
                return
            quality = capture.quality or {}
            inference = quality.get("inference") or {}
            await InferenceRepository(session).set_capture_inference(
                capture,
                {**inference, "status": status, "at": datetime.now(UTC).isoformat(), **details},
            )
            await session.commit()

    async def enrich_event(self, event_id: uuid.UUID, bound, capture_id: uuid.UUID) -> bool:
        """Contexto externo depois da inferência persistida (§13): falha nunca desfaz o Event."""
        async with self.database.sessionmaker() as session:
            service = CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            )
            location = await service.event_location(event_id)
        results = None
        if location is not None:
            settings = get_settings()
            sidra_query = (
                municipal_population_query(settings.ibge_sidra_municipality_code)
                if settings.ibge_sidra_municipality_code
                else None
            )
            try:
                with structlog.contextvars.bound_contextvars(event_id=str(event_id)):
                    results = await gather_context(
                        location["latitude"],
                        location["longitude"],
                        location["occurred_at"],
                        include_sidra=True,
                        sidra_query=sidra_query,
                        include_administrative=True,
                        sessions=self.database.sessionmaker,
                    )
            except Exception as exc:  # noqa: BLE001 — falha externa não apaga o Event
                bound.warning(
                    "event_context_failed", event_id=str(event_id), error=type(exc).__name__
                )
        async with self.database.sessionmaker() as session:
            if results is not None:
                service = CoreService(
                    CaptureRepository(session),
                    EventRepository(session),
                    DecisionRepository(session),
                )
                applied = await service.apply_context(event_id, results)
                bound.info(
                    "event_context_applied", event_id=str(event_id), context=applied["context"]
                )
            capture = await CaptureRepository(session).get(capture_id)
            if capture is None:
                raise RuntimeError("Capture ausente durante conclusão de contexto")
            inference = (capture.quality or {}).get("inference") or {}
            done = set(inference.get("context_done_event_ids") or [])
            done.add(str(event_id))
            await InferenceRepository(session).set_capture_inference(
                capture,
                {
                    **inference,
                    "context_done_event_ids": sorted(done),
                    "context_unavailable": results is None
                    or inference.get("context_unavailable", False),
                },
            )
            await session.commit()
        return results is not None

    async def _final_state(
        self, capture_id: uuid.UUID, job: dict, status: str, detail: str
    ) -> None:
        async with self.database.sessionmaker() as session:
            queue = InferenceRepository(session)
            capture = await CaptureRepository(session).get(capture_id)
            if capture is not None:
                inference = (capture.quality or {}).get("inference") or {}
                await queue.set_capture_inference(
                    capture,
                    {
                        **inference,
                        "status": status,
                        "detail": detail,
                        "attempts": job["read_ct"],
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
            await queue.archive_job(job["msg_id"])
            await session.commit()


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
            "model_version_id": str(worker._detector_version) if worker._detector_version else None,
            "inference_profile_sha256": worker._inference_profile_sha256,
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
    worker = Worker(database, StorageClient(settings))
    state_dir = os.environ.get("URMIND_WORKER_STATE_DIR")
    supervision = Supervision(Path(state_dir) if state_dir else None)
    # First heartbeat before any model load or job, so supervision sees the process.
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
