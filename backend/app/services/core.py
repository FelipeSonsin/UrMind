"""Domínio do núcleo: captura, evento e enriquecimento geoespacial.

Regras determinísticas apenas — nenhuma LLM participa da decisão (regra central
do MASTER_PLAN). O que o serviço não puder afirmar com os dados disponíveis,
ele marca para revisão em vez de inventar.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from app.repositories.core import CaptureRepository, DecisionRepository, EventRepository
from app.schemas.core import (
    CaptureCreate,
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
from app.services.report import (
    ActionSuggestion,
    ReportInput,
    ResponsibilitySuggestion,
    render_event_report,
)
from app.services.risk import ContextInput, RiskInput, RiskResult, Severity, assess

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
            return {"id": existing.id, "capture_key": existing.capture_key, "created": False}
        capture = await self.captures.create(payload)
        return {"id": capture.id, "capture_key": capture.capture_key, "created": True}

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
            accuracy_m=location["accuracy_m"],
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
            duplicate = await self.events.find_open_near(
                urmind_class=urmind_class.value,
                coordinate=coordinate,
                radius_m=DEDUP_RADIUS_M,
                since=capture.captured_at - DEDUP_WINDOW,
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
                        "evidence": {
                            "capture_ids": [str(capture_id)],
                            "detection_ids": detection_ids,
                        }
                    },
                )
            )
            risk = await self.assess_event(
                created["id"],
                apparent_extent=max(d.bbox["width"] * d.bbox["height"] for d in detections),
                detection_count=len(detections),
            )
            outcomes.append(
                {"event_id": created["id"], "created": True, "deduplicated": False, "risk": risk}
            )
        return {"capture_id": capture_id, "events": outcomes}

    async def assess_event(
        self, event_id: uuid.UUID, *, apparent_extent: float | None, detection_count: int
    ) -> dict[str, Any]:
        """Severidade/prioridade por regras versionadas e competência por tabela (§14)."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        result = assess(
            RiskInput(
                urmind_class=UrmindClass(event.urmind_class),
                evidence_mode=EvidenceMode(event.evidence_mode),
                visual_confidence=event.visual_confidence,
                apparent_extent=apparent_extent,
                detection_count=detection_count,
                location_accuracy_m=event.location_accuracy_m,
                context=context_input(
                    await self.events.contexts(event.id),
                    recurrence_on_segment=await self.events.recurrence_on_segment(event),
                ),
            )
        )
        segment = (
            await self.events.segment(event.road_segment_id) if event.road_segment_id else None
        )
        rule = await self.decisions.responsibility(
            jurisdiction=segment.jurisdiction if segment else None,
            asset_type=ASSET_TYPE_PAVEMENT,
            urmind_class=event.urmind_class,
        )
        action_code = ACTION_BY_SEVERITY.get(result.severity)
        action = await self.decisions.action(action_code) if action_code else None
        persisted = result.as_persisted()
        persisted["factors"]["responsibility"] = (
            {"rule_id": str(rule.id), "responsible": rule.responsible, "source": rule.source}
            if rule
            else "requires_triage"
        )
        persisted["factors"]["action"] = (
            {
                "code": action.code,
                "reason": f"severidade {result.severity.value}",
                "rule": ACTION_RULE_VERSION,
            }
            if action
            else {"reason": "sem severidade afirmável, nenhuma ação é sugerida"}
        )
        assessment = await self.decisions.add_risk(
            event.id,
            persisted,
            responsibility_rule_id=rule.id if rule else None,
            action_id=action.id if action else None,
            model_version_id=event.model_version_id,
        )
        if result.severity is Severity.UNKNOWN and event.status == EventStatus.DETECTED.value:
            await self.events.set_status(event, EventStatus.TRIAGE_REQUIRED.value)
        return {
            "assessment_id": assessment.id,
            "severity": assessment.severity,
            "priority_score": assessment.priority_score,
            "uncertainty": assessment.uncertainty,
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
        return {
            **base,
            **coords,
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

    async def review_event(
        self, event_id: uuid.UUID, payload: ReviewCreate, *, reviewer: str
    ) -> dict[str, Any]:
        """Confirma, corrige ou rejeita. Original preservado; mudança vira Review + AuditLog."""
        if self.decisions is None:
            raise RuntimeError("CoreService sem DecisionRepository")
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        original = await self.events.coordinates(event_id)
        before = {
            "status": event.status,
            "urmind_class": event.urmind_class,
            "latitude": original["latitude"],
            "longitude": original["longitude"],
        }
        review = await self.decisions.add_review(
            event_id=event_id,
            reviewer=reviewer,
            decision=payload.decision.value,
            corrected_class=payload.corrected_class.value if payload.corrected_class else None,
            corrected_location=payload.corrected_location,
            notes=payload.notes,
        )
        await self.events.set_status(event, REVIEW_STATUS[payload.decision].value)
        after = {
            "status": event.status,
            "urmind_class": event.urmind_class,
            "review_id": str(review.id),
            "decision": review.decision,
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
        return {"review_id": review.id, "event_id": event_id, "status": event.status}

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

    async def apply_context(
        self, event_id: uuid.UUID, results: list[ContextResult]
    ) -> dict[str, Any]:
        """Persiste o contexto (inclusive `context_unavailable`) e reavalia o risco com ele."""
        for result in results:
            await self.events.save_context(event_id, result.source, result.as_payload())
        event = await self.events.get(event_id)
        if event is None:
            raise EventNotFoundError("Evento não encontrado")
        evidence = (event.factors or {}).get("evidence", {})
        detection_ids = set(evidence.get("detection_ids", []))
        capture = await self.captures.get(event.capture_id) if event.capture_id else None
        detections = [
            d for d in (capture.detections if capture else []) if str(d.id) in detection_ids
        ]
        risk = await self.assess_event(
            event_id,
            apparent_extent=(
                max(d.bbox["width"] * d.bbox["height"] for d in detections) if detections else None
            ),
            detection_count=len(detections),
        )
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
