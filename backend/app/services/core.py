"""Domínio do núcleo: captura, evento e enriquecimento geoespacial.

Regras determinísticas apenas — nenhuma LLM participa da decisão (regra central
do MASTER_PLAN). O que o serviço não puder afirmar com os dados disponíveis,
ele marca para revisão em vez de inventar.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.repositories.core import CaptureRepository, EventRepository
from app.schemas.core import (
    CaptureCreate,
    Coordinate,
    EventCreate,
    EventStatus,
    EvidenceMode,
    NearbyQuery,
)

# PROVISÓRIO: limiar ainda não medido contra a malha viária real do piloto.
# §31.16 exige que ele nasça de baseline; revisar no passo 5 do §25.
MAX_SNAP_DISTANCE_M = 50.0
# PROVISÓRIO pelo mesmo motivo: accuracy aceitável depende de medição em campo (§11.1).
MAX_TRUSTED_ACCURACY_M = 30.0


class DuplicateKeyError(RuntimeError):
    """Chave idempotente já usada; reenvio não deve duplicar (§7.2)."""


class EventNotFoundError(RuntimeError):
    pass


class CoreService:
    """Uma unidade de trabalho por operação; a sessão vem do chamador."""

    def __init__(self, captures: CaptureRepository, events: EventRepository) -> None:
        self.captures = captures
        self.events = events

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
