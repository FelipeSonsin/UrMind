"""Domínio do núcleo: captura, evento e enriquecimento geoespacial.

Regras determinísticas apenas — nenhuma LLM participa da decisão (regra central
do MASTER_PLAN). O que o serviço não puder afirmar com os dados disponíveis,
ele marca para revisão em vez de inventar.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from app.repositories.core import CaptureRepository, DecisionRepository, EventRepository
from app.schemas.core import (
    CaptureCreate,
    CaptureProcessingStatus,
    CaptureReviewCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EventStatus,
    EvidenceMode,
    NearbyQuery,
    ReviewCreate,
    ReviewDecision,
    UrmindClass,
)
from app.services.context import POI_RADIUS_M, STATUS_OK, ContextResult
from app.services.features import FeatureInput, build_features
from app.services.report import (
    ActionSuggestion,
    ReportInput,
    ResponsibilitySuggestion,
    render_event_report,
)
from app.services.review_export import REVIEW_SCHEMA_VERSION, review_resolution
from app.services.risk import ContextInput, RiskResult, Severity, assess_features, replay_features

# PROVISÓRIO: limiar ainda não medido contra a malha viária real do piloto.
# §31.16 exige que ele nasça de baseline; revisar no passo 5 do §25.
MAX_SNAP_DISTANCE_M = 50.0
# PROVISÓRIO pelo mesmo motivo: accuracy aceitável depende de medição em campo (§11.1).
MAX_TRUSTED_ACCURACY_M = 30.0
# PROVISÓRIOS (§31.16): deduplicação por raio/janela e confiança mínima para virar
# evento. Nenhum foi calibrado com eventos revisados; revisar no piloto.
DEDUP_RADIUS_M = 10.0
DEDUP_WINDOW = timedelta(days=30)
MIN_DETECTION_CONFIDENCE = 0.25
V1_EVENT_CLASSES = frozenset(
    {UrmindClass.ROAD_D00, UrmindClass.ROAD_D10, UrmindClass.ROAD_D20, UrmindClass.ROAD_D40}
)
ASSET_TYPE_PAVEMENT = "pavimento"
DEFAULT_ACTION_CODE = "inspecao_tecnica"
# Ação sugerida por severidade (§14.4). Duas regras, ambas conservadoras:
# defeito grave pede proteção do ponto antes de qualquer obra, e o reparo
# definitivo nunca é sugerido pelo sistema — o próprio catálogo o descreve como
# intervenção "definida pelo órgão após inspeção".
ACTION_BY_SEVERITY = {
    Severity.CRITICAL: "sinalizacao_temporaria",
    Severity.HIGH: "sinalizacao_temporaria",
    Severity.MEDIUM: DEFAULT_ACTION_CODE,
    Severity.LOW: DEFAULT_ACTION_CODE,
}
ACTION_RULE_VERSION = "severity-to-action-v1"

# Stored Worker stages exposed verbatim by the processing endpoint.
_PASSTHROUGH_STAGES = frozenset(
    status.value
    for status in (
        CaptureProcessingStatus.PROCESSING_DETECTION,
        CaptureProcessingStatus.BUILDING_EVENT,
        CaptureProcessingStatus.ENRICHING_CONTEXT,
        CaptureProcessingStatus.BUILDING_FEATURES,
        CaptureProcessingStatus.ASSESSING,
        CaptureProcessingStatus.NEEDS_REVIEW,
        CaptureProcessingStatus.NO_SUPPORTED_DETECTION,
        CaptureProcessingStatus.NO_EVENT,
        CaptureProcessingStatus.MODEL_NOT_AVAILABLE,
    )
)


class DuplicateKeyError(RuntimeError):
    """Chave idempotente já usada; reenvio não deve duplicar (§7.2)."""


class EventNotFoundError(RuntimeError):
    pass


class CoreService:
    """Uma unidade de trabalho por operação; a sessão vem do chamador."""

    def __init__(
        self,
        captures: CaptureRepository,
        events: EventRepository,
        decisions: DecisionRepository | None = None,
    ) -> None:
        self.captures = captures
        self.events = events
        self.decisions = decisions

    async def register_capture(self, payload: CaptureCreate) -> dict[str, Any]:
        existing = await self.captures.get_by_key(payload.capture_key)
        if existing is not None:
            # Reenvio idempotente: devolve o que já existe em vez de duplicar.
            return {
                "id": existing.id,
                "capture_key": existing.capture_key,
                "protocol_code": existing.protocol_code,
                "created": False,
            }
        capture = await self.captures.create(payload)
        return {
            "id": capture.id,
            "capture_key": capture.capture_key,
            "protocol_code": capture.protocol_code,
            "created": True,
        }

    async def capture_markers(
        self,
        actor: str,
        can_review: bool = False,
        *,
        public: bool = False,
        include_unlocated: bool = False,
    ) -> list[dict[str, Any]]:
        rows = await self.captures.report_markers(
            actor, can_review, public=public, include_unlocated=include_unlocated
        )
        markers = []
        for row in rows:
            marker = {
                "id": row["id"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "report_status": "received",
            }
            if public:
                # Opt-in public layer is deliberately generic: no analysis, owner, EXIF or media.
                marker["id"] = row["public_id"]
                marker["latitude"] = round(row["latitude"], 4)
                marker["longitude"] = round(row["longitude"], 4)
                markers.append(marker)
                continue
            status = row.get("processing_status")
            if row["latitude"] is None or row["longitude"] is None:
                marker["report_status"] = "location_required"
            elif row.get("has_review") and row.get("event_status") == "confirmed":
                marker["report_status"] = "human_confirmed"
            elif row.get("event_id") and row.get("model_status") == "EXPERIMENTAL_SHADOW":
                marker["report_status"] = "experimental"
            elif status == "model_not_available":
                marker["report_status"] = "model_not_available"
            elif status in {"no_supported_detection", "no_detection"}:
                marker["report_status"] = "no_supported_detection"
            marker.update(
                {
                    key: row.get(key)
                    for key in (
                        "location_source",
                        "accuracy_m",
                        "user_description",
                        "location_conflict",
                        "event_id",
                        "event_public_id",
                        "created_at",
                        "photo_gate",
                        "protocol_code",
                        "public_id",
                    )
                }
            )
            analyzed = marker["report_status"] in {"experimental", "human_confirmed"}
            marker.update(
                {
                    key: row.get(key) if analyzed else None
                    for key in (
                        "urmind_class",
                        "severity",
                        "priority_score",
                    )
                }
            )
            human = row.get("human_review") or {}
            if marker["report_status"] == "human_confirmed":
                if human.get("class"):
                    marker["urmind_class"] = human["class"]
                correction = human.get("corrected_location")
                if isinstance(correction, dict):
                    marker["original_latitude"] = marker["latitude"]
                    marker["original_longitude"] = marker["longitude"]
                    marker["latitude"] = correction["latitude"]
                    marker["longitude"] = correction["longitude"]
            elif human.get("status") == "rejected":
                marker["report_status"] = "duplicate" if human.get("duplicate_of") else "rejected"
            markers.append(marker)
        return markers

    async def capture_processing(
        self, capture_id: uuid.UUID, actor: str, can_review: bool
    ) -> dict[str, Any]:
        capture = await self.captures.get(capture_id)
        if capture is None:
            raise EventNotFoundError("Captura não encontrada")
        quality = capture.quality or {}
        if quality.get("uploaded_by") != actor:
            raise EventNotFoundError("Captura não encontrada")
        inference = quality.get("inference") or {}
        events = await self.events.for_capture(capture_id)
        known_ids = {event.id for event in events}
        for raw_id in inference.get("event_ids") or []:
            try:
                event_id = uuid.UUID(raw_id)
            except (TypeError, ValueError, AttributeError):
                continue
            if event_id in known_ids:
                continue
            event = await self.events.get(event_id)
            evidence = (event.factors or {}).get("evidence") if event else None
            if (
                event
                and isinstance(evidence, dict)
                and str(capture_id) in evidence.get("capture_ids", [])
            ):
                events.append(event)
                known_ids.add(event_id)
        stored_status = inference.get("status")
        status: CaptureProcessingStatus
        if capture.point is None:
            status = CaptureProcessingStatus.LOCATION_REQUIRED
        elif stored_status == "inference_failed":
            status = CaptureProcessingStatus.FAILED
        elif stored_status == "no_detection":
            status = CaptureProcessingStatus.NO_SUPPORTED_DETECTION
        elif stored_status in _PASSTHROUGH_STAGES:
            status = CaptureProcessingStatus(stored_status)
        elif stored_status in {"inference_completed", "detection_completed"}:
            status = CaptureProcessingStatus.DETECTION_COMPLETED
        elif stored_status == "analysis_completed":
            status = await self._analysis_status(events)
        else:
            status = CaptureProcessingStatus.QUEUED
        return {
            "capture_id": capture.id,
            "protocol_code": getattr(capture, "protocol_code", None),
            "status": status.value,
            "requires_manual_location": capture.point is None,
            "event_ids": [event.id for event in events],
            "event_public_ids": [
                event.public_id for event in events if getattr(event, "public_id", None)
            ],
            "model_version_id": inference.get("model_version_id"),
            "model_status": inference.get("model_status"),
            "updated_at": inference.get("completed_at") or inference.get("at"),
        }

    async def _analysis_status(self, events: list[Any]) -> CaptureProcessingStatus:
        """`completed` only with Event + feature snapshot + RiskAssessment + DecisionTrace."""
        if not events:
            return CaptureProcessingStatus.NEEDS_REVIEW
        if self.decisions is None:
            return CaptureProcessingStatus.ASSESSING
        for event in events:
            assessment = await self.decisions.latest_risk(event.id)
            factors = (assessment.factors or {}) if assessment else {}
            if not factors.get("phase4_snapshot") or not factors.get("decision_trace"):
                return CaptureProcessingStatus.ASSESSING
        if any(
            event.status in {EventStatus.REVIEW.value, EventStatus.TRIAGE_REQUIRED.value}
            for event in events
        ):
            return CaptureProcessingStatus.NEEDS_REVIEW
        return CaptureProcessingStatus.COMPLETED

    async def register_event(self, payload: EventCreate) -> dict[str, Any]:
        if await self.events.get_by_key(payload.event_key) is not None:
            raise DuplicateKeyError(f"event_key já registrado: {payload.event_key}")

        event = await self.events.create(payload)
        snap: dict[str, Any] | None = None
        if payload.coordinate is not None:
            snap = await self._enrich_location(event, payload.coordinate)
        await self.events.set_status(event, decide_status(payload, snap).value)
        return {
            "id": event.id,
            "event_key": event.event_key,
            "status": event.status,
            "road_segment_id": event.road_segment_id,
            "distance_to_road_m": event.distance_to_road_m,
        }

    async def _enrich_location(self, event, coordinate: Coordinate) -> dict[str, Any] | None:
        snap = await self.events.snap_to_road(coordinate, MAX_SNAP_DISTANCE_M)
        if snap is None:
            return None
        await self.events.attach_road(
            event,
            road_segment_id=snap["road_segment_id"],
            distance_m=snap["distance_m"],
            latitude=snap["latitude"],
            longitude=snap["longitude"],
        )
        return snap

    async def consolidate_capture(self, capture_id: uuid.UUID) -> dict[str, Any]:
        """Detections de uma captura -> Events deduplicados + risco (§25 passos 9 e 11).

        Idempotente: repetir para a mesma captura não cria evento nem evidência nova.
        """
        capture = await self.captures.get(capture_id)
        if capture is None:
            raise EventNotFoundError("Captura não encontrada")
        location = await self.captures.location(capture_id)
        if location is None:
            return {"capture_id": capture_id, "events": [], "reason": "requires_manual_location"}
        coordinate = Coordinate(
            latitude=location["latitude"],
            longitude=location["longitude"],
            # The browser's reported GPS accuracy is retained on Capture for
            # provenance, but is not independently verified for Event/risk.
            accuracy_m=(
                None
                if (capture.quality or {}).get("location_attestation") == "unverified_client_claim"
                else location["accuracy_m"]
            ),
        )
        evidence_mode = (
            EvidenceMode.EXIF_PHOTO
            if capture.source == CaptureSource.EXIF_UPLOAD.value
            else EvidenceMode.PHOTO
        )
        by_class: dict[UrmindClass, list[Any]] = {}
        for detection in capture.detections:
            urmind_class = UrmindClass(detection.urmind_class)
            if (
                urmind_class in V1_EVENT_CLASSES
                and detection.confidence >= MIN_DETECTION_CONFIDENCE
            ):
                by_class.setdefault(urmind_class, []).append(detection)

        outcomes: list[dict[str, Any]] = []
        for urmind_class, detections in sorted(by_class.items()):
            best = max(detections, key=lambda d: d.confidence)
            detection_ids = sorted(str(d.id) for d in detections)
            event_key = f"cap-{capture_id}-{urmind_class.value}"
            existing = await self.events.get_by_key(event_key)
            if existing is not None:
                outcomes.append({"event_id": existing.id, "created": False, "deduplicated": False})
                continue
            # A client-claimed point cannot establish that two separate photos
            # show the same place. Keep their Event/Review lineage independent.
            duplicate = (
                None
                if (capture.quality or {}).get("location_attestation") == "unverified_client_claim"
                else await self.events.find_open_near(
                    urmind_class=urmind_class.value,
                    coordinate=coordinate,
                    radius_m=DEDUP_RADIUS_M,
                    since=capture.captured_at - DEDUP_WINDOW,
                )
            )
            if duplicate is not None:
                previous = (duplicate.factors or {}).get("evidence", {})
                if str(capture_id) not in previous.get("capture_ids", []):
                    await self.events.merge_factors(
                        duplicate,
                        {
                            "evidence": {
                                "capture_ids": [*previous.get("capture_ids", []), str(capture_id)],
                                "detection_ids": [
                                    *previous.get("detection_ids", []),
                                    *detection_ids,
                                ],
                            }
                        },
                    )
                outcomes.append({"event_id": duplicate.id, "created": False, "deduplicated": True})
                continue
            created = await self.register_event(
                EventCreate(
                    event_key=event_key,
                    capture_id=capture_id,
                    urmind_class=urmind_class,
                    evidence_mode=evidence_mode,
                    occurred_at=capture.captured_at,
                    coordinate=coordinate,
                    visual_confidence=best.confidence,
                    model_version_id=best.model_version_id,
                    factors={
                        "location_attestation": (capture.quality or {}).get("location_attestation"),
                        "evidence": {
                            "capture_ids": [str(capture_id)],
                            "detection_ids": detection_ids,
                        },
                    },
                )
            )
            risk = await self.assess_event(created["id"])
            outcomes.append(
                {"event_id": created["id"], "created": True, "deduplicated": False, "risk": risk}
            )
        return {"capture_id": capture_id, "events": outcomes}

    async def assess_event(self, event_id: uuid.UUID) -> dict[str, Any]:
        """Severidade/prioridade por regras versionadas e competência por tabela (§14)."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        features, context_records = await self._feature_snapshot(event_id)
        previous_assessment = await self.decisions.latest_risk(event_id)
        if previous_assessment is not None:
            features["provenance"]["history_snapshot_assessment_id"] = str(previous_assessment.id)
            previous_features = (
                (previous_assessment.factors or {}).get("phase4_snapshot", {}).get("features")
            )
            if previous_features is None:
                features["history"] = {
                    "previous_events_same_segment": None,
                    "recent_events_same_segment_30d": None,
                    "has_previous_event_same_segment": None,
                    "order_status": "legacy_snapshot_unavailable",
                }
                features["missingness"]["history_unavailable"] = True
            else:
                features["history"] = deepcopy(previous_features["history"])
                features["missingness"]["history_unavailable"] = previous_features["missingness"][
                    "history_unavailable"
                ]
        result = assess_features(features)
        assessment_id = uuid.uuid4()
        dataset_version_id = (
            await self.decisions.dataset_version_for_model(event.model_version_id)
            if event.model_version_id
            else None
        )
        severity = Severity(result["severity"])
        segment = (
            await self.events.segment(event.road_segment_id) if event.road_segment_id else None
        )
        rule = await self.decisions.responsibility(
            jurisdiction=segment.jurisdiction if segment else None,
            asset_type=ASSET_TYPE_PAVEMENT,
            urmind_class=event.urmind_class,
        )
        action_code = ACTION_BY_SEVERITY.get(severity)
        action = await self.decisions.action(action_code) if action_code else None
        # Legacy score columns remain nullable. An ordinal attention lane is
        # neither a calibrated numeric score nor a probability.
        persisted: dict[str, Any] = {
            "severity": severity.value,
            "priority_score": None,
            "uncertainty": None,
            "factors": {
                "phase5": result,
                "phase4_snapshot": {
                    "assessment_id": str(assessment_id),
                    "event_id": str(event_id),
                    "feature_schema_version": features["feature_schema_version"],
                    "ruleset_version": result["ruleset_version"],
                    "collected_at": datetime.now(UTC).isoformat(),
                    "context_records": context_records,
                    "features": features,
                },
            },
        }
        persisted["factors"]["responsibility"] = (
            {"rule_id": str(rule.id), "responsible": rule.responsible, "source": rule.source}
            if rule
            else "requires_triage"
        )
        persisted["factors"]["action"] = (
            {
                "code": action.code,
                "reason": f"severidade {severity.value}",
                "rule": ACTION_RULE_VERSION,
            }
            if action
            else {"reason": "sem severidade afirmável, nenhuma ação é sugerida"}
        )
        # The Phase 4 snapshot and Phase 5 rule trace live in this same immutable
        # assessment row. Keep references to those exact values, not to the
        # mutable EventContext or to the current model registry entry.
        persisted["factors"]["decision_trace"] = {
            "trace_schema_version": "urmind-decision-trace-v1",
            "assessment_id": str(assessment_id),
            "event_id": str(event_id),
            "feature_schema_version": features["feature_schema_version"],
            "ruleset_version": result["ruleset_version"],
            "feature_snapshot_key": "phase4_snapshot.features",
            "context_snapshot_key": "phase4_snapshot.context_records",
            "factors_used": result["factors_used"],
            "factors_missing": result["factors_missing"],
            "evaluated_rules": result["decision_trace"]["evaluated_rules"],
            "provisional_parameters": result["decision_trace"]["provisional_parameters"],
            "impact": result["impact"],
            "severity": result["severity"],
            "risk": result["risk"],
            "priority": result["priority"],
            "responsibility": persisted["factors"]["responsibility"],
            "action": persisted["factors"]["action"],
            "model_version_id": str(event.model_version_id) if event.model_version_id else None,
            "dataset_version_id": str(dataset_version_id) if dataset_version_id else None,
            "assessed_at": persisted["factors"]["phase4_snapshot"]["collected_at"],
            "provenance": features["provenance"],
        }
        assessment = await self.decisions.add_risk(
            event.id,
            persisted,
            id=assessment_id,
            responsibility_rule_id=rule.id if rule else None,
            action_id=action.id if action else None,
            model_version_id=event.model_version_id,
        )
        if severity is Severity.UNKNOWN and event.status == EventStatus.DETECTED.value:
            await self.events.set_status(event, EventStatus.TRIAGE_REQUIRED.value)
        return {
            "assessment_id": assessment.id,
            "severity": assessment.severity,
            "priority_score": assessment.priority_score,
            "uncertainty": assessment.uncertainty,
            "risk": result["risk"],
            "priority": result["priority"],
            "ruleset_version": result["ruleset_version"],
            "responsible": rule.responsible if rule else None,
            "action": action.code if action else None,
        }

    async def event_dossier(self, event_id: uuid.UUID) -> dict[str, Any]:
        """Detalhe completo do evento: captura, detecções, risco, competência, ação e relatório."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        base = await self.detail(event_id)
        event = await self.events.get(event_id)
        assert event is not None
        coords = await self.events.coordinates(event_id)
        capture = await self.captures.get(event.capture_id) if event.capture_id else None
        evidence_ids = set((event.factors or {}).get("evidence", {}).get("detection_ids", []))
        detections = [
            {
                "id": d.id,
                "urmind_class": d.urmind_class,
                "confidence": d.confidence,
                "bbox": d.bbox,
                "model_version_id": d.model_version_id,
            }
            for d in (capture.detections if capture else [])
            if not evidence_ids or str(d.id) in evidence_ids
        ]
        row = await self.decisions.latest_risk(event_id)
        rule = await self.decisions.get_rule(row.responsibility_rule_id) if row else None
        action = await self.decisions.get_action(row.action_id) if row else None
        segment = (
            await self.events.segment(event.road_segment_id) if event.road_segment_id else None
        )
        report = None
        if row is not None:
            report = render_event_report(
                ReportInput(
                    event_key=event.event_key,
                    urmind_class=UrmindClass(event.urmind_class),
                    risk=await _risk_from_row(row),
                    visual_confidence=event.visual_confidence,
                    model_version=str(event.model_version_id) if event.model_version_id else None,
                    latitude=coords["latitude"],
                    longitude=coords["longitude"],
                    location_accuracy_m=event.location_accuracy_m,
                    location_source=capture.source_location if capture else None,
                    road_segment_name=segment.name if segment else None,
                    distance_to_road_m=event.distance_to_road_m,
                    evidence=[f"captura {capture.capture_key}"] if capture else [],
                    responsibility=(
                        ResponsibilitySuggestion(rule.responsible, rule.source, rule.version)
                        if rule
                        else None
                    ),
                    action=ActionSuggestion(action.code, action.label, action.version)
                    if action
                    else None,
                )
            )
        inference = (getattr(capture, "quality", None) or {}).get("inference") or {}
        return {
            **base,
            **coords,
            "model_status": (
                inference.get("model_status")
                if capture and str(event.model_version_id) == inference.get("model_version_id")
                else None
            ),
            "capture": (
                {
                    "id": capture.id,
                    "capture_key": capture.capture_key,
                    "source": capture.source,
                    "source_location": capture.source_location,
                    "captured_at": capture.captured_at,
                    "storage_path": capture.storage_path,
                }
                if capture
                else None
            ),
            "detections": detections,
            "risk": (
                {
                    "severity": row.severity,
                    "priority_score": row.priority_score,
                    "uncertainty": row.uncertainty,
                    "factors": row.factors,
                    "created_at": row.created_at,
                }
                if row
                else None
            ),
            "decision_trace": (row.factors or {}).get("decision_trace") if row else None,
            "responsibility": (
                {"responsible": rule.responsible, "source": rule.source, "version": rule.version}
                if rule
                else ("requires_triage" if row else None)
            ),
            "action": {"code": action.code, "label": action.label, "version": action.version}
            if action
            else None,
            "context": [
                {"source": row.source, **row.payload}
                for row in await self.events.contexts(event_id)
            ],
            "report": report,
            "reviews": [
                {
                    "decision": r.decision,
                    "corrected_class": r.corrected_class,
                    "notes": r.notes,
                    "reviewer": r.reviewer,
                    "created_at": r.created_at,
                }
                for r in await self.decisions.reviews(event_id)
            ],
        }

    async def review_capture(
        self,
        capture_id: uuid.UUID,
        payload: CaptureReviewCreate,
        *,
        reviewer: str,
        reviewer_role: str | None,
    ) -> dict[str, Any]:
        if reviewer_role not in {"reviewer", "admin"}:
            raise PermissionError("Revisão exige papel de revisor")
        if payload.adjudicate and reviewer_role != "admin":
            raise PermissionError("Adjudicação exige admin")
        if self.decisions is None:
            raise RuntimeError("Revisão indisponível")
        capture = await self.captures.get_for_review(capture_id)
        if capture is None:
            raise EventNotFoundError("Relato não encontrado")
        duplicate = None
        if payload.duplicate_of_protocol:
            duplicate = await self.captures.protocol_target(payload.duplicate_of_protocol)
            if duplicate is None or duplicate.id == capture.id:
                raise ValueError("Protocolo alvo inválido")
        events = await self.events.for_capture(capture_id)
        event = events[0] if events else None
        if event is None and payload.decision is not ReviewDecision.REJECT:
            if payload.corrected_class is None:
                raise ValueError("Relato sem análise exige classe humana explícita")
            event = await self.events.human_event(capture, payload.corrected_class)
            await self.decisions.attach_report_event(capture_id, event.id)
        if event:
            result = await self.review_event(
                event.id,
                ReviewCreate.model_validate(payload.model_dump(exclude={"duplicate_of_protocol"})),
                reviewer=reviewer,
                reviewer_role=reviewer_role,
            )
            await self.decisions.attach_capture_review(result["review_id"], capture_id)
        else:
            prior = await self.decisions.review_votes(None, capture_id=capture_id)
            review = await self.decisions.add_review(
                event_id=None,
                capture_id=capture_id,
                reviewer=reviewer,
                decision="reject",
                corrected_class=None,
                notes=payload.notes,
            )
            resolution = review_resolution(
                [
                    *prior,
                    {
                        "decision": "reject",
                        "reviewer": reviewer,
                        "reviewer_role": reviewer_role,
                        "order_source": review.order_source,
                        "adjudicated": payload.adjudicate,
                    },
                ]
            )
            result = {
                "review_id": review.id,
                "event_id": None,
                "status": "rejected",
                "ground_truth_status": resolution["status"],
            }
            await self.decisions.add_audit(
                operation="review",
                entity_type="capture",
                entity_id=capture_id,
                actor=reviewer,
                before={},
                after={
                    "review_id": str(review.id),
                    "decision": "reject",
                    "reviewer_role": reviewer_role,
                    "review_schema_version": REVIEW_SCHEMA_VERSION,
                    "adjudicated": payload.adjudicate,
                    "ground_truth_status": result["ground_truth_status"],
                },
                event_hash=f"capture-review-{review.id}",
            )
        before = deepcopy((capture.quality or {}).get("human_review") or {})
        human_review = {
            "review_id": str(result["review_id"]),
            "status": result["status"],
            "ground_truth_status": result["ground_truth_status"],
            "class": payload.corrected_class or before.get("class"),
            "duplicate_of": str(duplicate.id) if duplicate else None,
            "corrected_location": payload.corrected_location.model_dump()
            if payload.corrected_location
            else before.get("corrected_location"),
        }
        capture.quality = {**(capture.quality or {}), "human_review": human_review}
        await self.decisions.add_audit(
            operation="capture_review_state",
            entity_type="capture",
            entity_id=capture_id,
            actor=reviewer,
            before=before,
            after=human_review,
            event_hash=f"capture-state-{uuid.uuid4()}",
        )
        return {**result, "capture_id": capture.id, "protocol_code": capture.protocol_code}

    async def review_event(
        self,
        event_id: uuid.UUID,
        payload: ReviewCreate,
        *,
        reviewer: str,
        reviewer_role: str | None = None,
    ) -> dict[str, Any]:
        """Confirma, corrige ou rejeita. Original preservado; mudança vira Review + AuditLog."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        if payload.adjudicate and reviewer_role != "admin":
            raise PermissionError("Adjudicação exige papel de admin")
        event = await self.events.get_for_review(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        original = await self.events.coordinates(event_id)
        before = {
            "status": event.status,
            "urmind_class": event.urmind_class,
            "latitude": original["latitude"],
            "longitude": original["longitude"],
        }
        prior = await self.decisions.review_votes(event_id)
        review = await self.decisions.add_review(
            event_id=event_id,
            reviewer=reviewer,
            decision=payload.decision.value,
            corrected_class=payload.corrected_class,
            corrected_location=payload.corrected_location,
            notes=payload.notes,
        )
        votes = list(prior)
        votes.append(
            {
                "decision": review.decision,
                "corrected_class": review.corrected_class,
                "corrected_latitude": payload.corrected_location.latitude
                if payload.corrected_location
                else None,
                "corrected_longitude": payload.corrected_location.longitude
                if payload.corrected_location
                else None,
                "reviewer": reviewer,
                "reviewer_role": reviewer_role,
                "order_source": review.order_source,
                "adjudicated": payload.adjudicate,
            }
        )
        resolution = review_resolution(votes)
        await self.events.set_status(
            event,
            REVIEW_STATUS[payload.decision].value
            if resolution["status"] in {"consensus", "adjudicated"}
            else EventStatus.REVIEW.value,
        )
        after = {
            "status": event.status,
            "urmind_class": event.urmind_class,
            "review_id": str(review.id),
            "decision": review.decision,
            "reviewer_role": reviewer_role,
            "review_schema_version": REVIEW_SCHEMA_VERSION,
            "adjudicated": payload.adjudicate,
            "ground_truth_status": resolution["status"],
            "corrected_class": review.corrected_class,
            "corrected_location": (
                payload.corrected_location.model_dump() if payload.corrected_location else None
            ),
        }
        digest = hashlib.sha256(
            json.dumps(
                {"before": before, "after": after, "event_id": str(event_id)}, sort_keys=True
            ).encode()
        ).hexdigest()
        await self.decisions.add_audit(
            operation="review",
            entity_type="event",
            entity_id=event_id,
            actor=reviewer,
            before=before,
            after=after,
            event_hash=digest,
        )
        return {
            "review_id": review.id,
            "event_id": event_id,
            "status": event.status,
            "ground_truth_status": resolution["status"],
        }

    async def event_location(self, event_id: uuid.UUID) -> dict[str, Any] | None:
        """Coordenada original e instante: o que os providers de contexto precisam."""
        event = await self.events.get(event_id)
        if event is None or event.point is None:
            return None
        coords = await self.events.coordinates(event_id)
        return {
            "latitude": coords["latitude"],
            "longitude": coords["longitude"],
            "occurred_at": event.occurred_at,
        }

    async def _feature_snapshot(
        self, event_id: uuid.UUID
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Load each context record once for both features and the assessment archive."""
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        capture = await self.captures.get(event.capture_id) if event.capture_id else None
        segment = (
            await self.events.segment(event.road_segment_id) if event.road_segment_id else None
        )
        coords = await self.events.coordinates(event_id)
        contexts = await self.events.contexts(event_id)
        history, history_status = await self.events.previous_event_times(event)
        features = build_features(
            FeatureInput(
                event=event,
                capture=capture,
                detections=await self.events.evidence_detections(event),
                road_segment=segment,
                contexts=contexts,
                previous_event_times=history,
                history_status=history_status,
                has_original_location=coords["latitude"] is not None,
                has_snapped_point=coords["snapped_latitude"] is not None,
            )
        )
        records = [
            {
                "id": str(row.id),
                "source": row.source,
                "fetched_at": row.fetched_at.isoformat(),
                "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
                "temporal_status": features["provenance"]["context"][row.source]["temporal_status"],
                "payload": deepcopy(row.payload),
            }
            for row in contexts
        ]
        return features, records

    async def event_features(self, event_id: uuid.UUID) -> dict[str, Any]:
        """Build current Phase 4 features; prior assessments use their own snapshot."""
        features, _ = await self._feature_snapshot(event_id)
        return features

    async def reproduce_assessment(self, assessment_id: uuid.UUID) -> dict[str, Any]:
        """Replay only the immutable saved input, never the mutable EventContext row."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        row = await self.decisions.get_risk(assessment_id)
        if row is None:
            raise EventNotFoundError("Avaliação não encontrada")
        snapshot = (row.factors or {}).get("phase4_snapshot")
        original = (row.factors or {}).get("phase5")
        if not snapshot or not original:
            raise ValueError("avaliação legada sem snapshot reproduzível")
        if snapshot.get("assessment_id") != str(row.id):
            raise ValueError("lineage da avaliação inconsistente")
        if snapshot.get("ruleset_version") != original.get("ruleset_version"):
            raise ValueError("versão das regras inconsistente")
        replayed = replay_features(snapshot["features"], original)
        if replayed["ruleset_version"] != snapshot["ruleset_version"]:
            raise ValueError("versão das regras indisponível para reprodução")
        return {
            "assessment_id": row.id,
            "snapshot": deepcopy(snapshot),
            "result": deepcopy(original),
            "replay_matches": replayed == original,
        }

    async def apply_context(
        self, event_id: uuid.UUID, results: list[ContextResult]
    ) -> dict[str, Any]:
        """Persiste o contexto (inclusive `context_unavailable`) e reavalia o risco com ele."""
        for result in results:
            await self.events.save_context(event_id, result.source, result.as_payload())
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        risk = await self.assess_event(event_id)
        return {
            "event_id": event_id,
            "context": {result.source: result.status for result in results},
            "risk": risk,
        }

    async def list_events(
        self, *, urmind_class: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self.events.rows(urmind_class=urmind_class, status=status, limit=limit)

    async def events_nearby(self, query: NearbyQuery) -> list[dict[str, Any]]:
        return await self.events.nearby(query)

    async def detail(self, event_id: uuid.UUID) -> dict[str, Any]:
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        return {
            "id": event.id,
            "event_key": event.event_key,
            "urmind_class": event.urmind_class,
            "evidence_mode": event.evidence_mode,
            "status": event.status,
            "occurred_at": event.occurred_at,
            "visual_confidence": event.visual_confidence,
            "fused_confidence": event.fused_confidence,
            "road_segment_id": event.road_segment_id,
            "distance_to_road_m": event.distance_to_road_m,
            "location_accuracy_m": event.location_accuracy_m,
            "factors": event.factors,
        }


async def _risk_from_row(row) -> RiskResult:
    factors = dict(row.factors or {})
    phase5 = factors.get("phase5")
    if isinstance(phase5, dict):
        return RiskResult(
            severity=Severity(row.severity),
            priority_score=None,
            uncertainty=None,
            coverage=None,
            limitations=list(phase5.get("factors_missing", [])),
            ruleset_version=str(phase5["ruleset_version"]),
            factors=factors,
        )
    return RiskResult(
        severity=Severity(row.severity),
        priority_score=row.priority_score,
        uncertainty=row.uncertainty if row.uncertainty is not None else 1.0,
        coverage=float(factors.pop("coverage", 0.0)),
        limitations=list(factors.pop("limitations", [])),
        ruleset_version=str(factors.pop("ruleset_version", "desconhecida")),
        factors=factors,
    )


def context_input(rows, *, recurrence_on_segment: int | None) -> ContextInput:
    """EventContext persistido → entrada do motor de risco. Indisponível continua None."""
    payloads = {row.source: row.payload for row in rows if row.payload.get("status") == STATUS_OK}

    def nearest(category: str) -> float | None:
        pois = payloads.get("overpass_pois")
        if pois is None:
            return None
        found = (pois["data"].get("nearest") or {}).get(category)
        # Nada no raio consultado: a distância é *pelo menos* o raio, não desconhecida.
        return (
            float(found["distance_m"])
            if found
            else float(pois["data"].get("radius_m", POI_RADIUS_M))
        )

    rain = payloads.get("open_meteo_rain")
    return ContextInput(
        distance_to_school_m=nearest("school"),
        distance_to_health_m=nearest("health"),
        distance_to_crossing_m=nearest("crossing"),
        recurrence_on_segment=recurrence_on_segment,
        rain_mm_24h=float(rain["data"]["rain_mm_24h"]) if rain else None,
    )


REVIEW_STATUS = {
    ReviewDecision.CONFIRM: EventStatus.CONFIRMED,
    ReviewDecision.CORRECT: EventStatus.CONFIRMED,
    ReviewDecision.REJECT: EventStatus.REJECTED,
}


def decide_status(payload: EventCreate, snap: dict[str, Any] | None) -> EventStatus:
    """Status inicial do evento. Função pura de decisão, sem LLM (§15, §27).

    Incerteza alta manda para revisão humana; ela nunca vira urgência inventada.
    """
    if payload.evidence_mode is EvidenceMode.IMAGE_ONLY:
        # Sem localização não existe ocorrência geográfica (§11.1).
        return EventStatus.TRIAGE_REQUIRED
    if payload.evidence_mode is EvidenceMode.SENSOR_ONLY:
        # Sensor sem câmera gera candidato, nunca classe visual afirmada.
        return EventStatus.REVIEW
    if payload.urmind_class.value == "URMIND_UNKNOWN":
        return EventStatus.REVIEW
    if payload.factors.get("location_attestation") == "unverified_client_claim":
        return EventStatus.REVIEW

    accuracy = payload.coordinate.accuracy_m if payload.coordinate else None
    if accuracy is not None and accuracy > MAX_TRUSTED_ACCURACY_M:
        return EventStatus.REVIEW
    if snap is None:
        # Ponto longe de qualquer via compatível exige confirmação humana (§11.2).
        return EventStatus.REVIEW

    confidence = payload.fused_confidence or payload.visual_confidence
    if confidence is None or confidence < 0.5:
        return EventStatus.REVIEW
    return EventStatus.DETECTED
