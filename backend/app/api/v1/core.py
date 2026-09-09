"""Rotas do núcleo geoespacial: capturas e eventos."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.repositories.core import CaptureRepository, EventRepository
from app.schemas.core import CaptureCreate, EventCreate, EventStatus, NearbyQuery, UrmindClass
from app.services.core import CoreService, DuplicateKeyError, EventNotFoundError

router = APIRouter(prefix="/api/v1")


async def get_core_service(request: Request) -> AsyncIterator[CoreService]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Banco direto não configurado (DATABASE_URL)")
    async with database.session() as session:
        yield CoreService(CaptureRepository(session), EventRepository(session))


Core = Annotated[CoreService, Depends(get_core_service)]


@router.post("/captures", status_code=201)
async def create_capture(payload: CaptureCreate, service: Core) -> dict[str, Any]:
    return await service.register_capture(payload)


@router.post("/events", status_code=201)
async def create_event(payload: EventCreate, service: Core) -> dict[str, Any]:
    try:
        return await service.register_event(payload)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/events")
async def list_events(
    service: Core,
    urmind_class: UrmindClass | None = None,
    status: EventStatus | None = None,
    limit: int = Query(default=100, gt=0, le=500),
) -> list[dict[str, Any]]:
    return await service.list_events(
        urmind_class=urmind_class.value if urmind_class else None,
        status=status.value if status else None,
        limit=limit,
    )


@router.get("/events/nearby")
async def events_nearby(
    service: Core,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_m: float = Query(default=500, gt=0, le=20000),
    limit: int = Query(default=100, gt=0, le=500),
) -> list[dict[str, Any]]:
    query = NearbyQuery(
        latitude=latitude, longitude=longitude, radius_m=radius_m, limit=limit
    )
    return await service.events_nearby(query)


@router.get("/events/{event_id}")
async def event_detail(event_id: uuid.UUID, service: Core) -> dict[str, Any]:
    try:
        return await service.detail(event_id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
