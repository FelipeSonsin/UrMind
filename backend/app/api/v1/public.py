"""Painel público, somente leitura (§16.1, §17).

Sem autenticação e sem escrita. Cada resposta é montada por `app.services.public_view`
a partir dos schemas de `app.schemas.public`: o que não está no contrato não sai.

A câmera do Scout nunca é exposta direto ao público: o backend fica no meio, aplica o
portão de privacidade e limita conexões simultâneas.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.repositories.core import (
    CaptureRepository,
    DecisionRepository,
    EventRepository,
    InferenceRepository,
    PublicRepository,
)
from app.schemas.public import (
    ActionPublic,
    DetectionPublic,
    EventDetailPublic,
    EventSummaryPublic,
    RoadPublic,
    ScoutCameraPublic,
    ScoutPublic,
    StatusPublic,
    TransparencyPublic,
)
from app.services import public_view
from app.services.core import CoreService, EventNotFoundError

router = APIRouter(prefix="/api/v1/public", tags=["public"])

CACHE_SHORT = "public, max-age=5"
CACHE_MEDIUM = "public, max-age=30"
NO_CAMERA = "nenhuma fonte de câmera configurada para o Scout"
STREAM_BUSY = "limite de espectadores simultâneos atingido"
MAX_FRAME_BYTES = 8 * 1024 * 1024
_stream_slots: asyncio.Semaphore | None = None


def stream_slots(settings: Settings) -> asyncio.Semaphore:
    global _stream_slots
    if _stream_slots is None:
        _stream_slots = asyncio.Semaphore(settings.public_stream_max_clients)
    return _stream_slots


async def repositories(request: Request) -> AsyncIterator[dict[str, Any]]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Painel público indisponível: sem persistência")
    async with database.session() as session:
        yield {
            "public": PublicRepository(session),
            "decisions": DecisionRepository(session),
            "inference": InferenceRepository(session),
            "service": CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            ),
        }


Repos = Annotated[dict[str, Any], Depends(repositories)]


def camera_state(settings: Settings) -> ScoutCameraPublic:
    """Modo da câmera a partir do que existe de verdade — nunca "LIVE" sem fonte."""
    if settings.scout_stream_url:
        return ScoutCameraPublic(mode="live_video", stream_url="/api/v1/public/scout/stream")
    if settings.scout_frame_url:
        return ScoutCameraPublic(mode="live_snapshots", frame_url="/api/v1/public/scout/frame")
    return ScoutCameraPublic(mode="unavailable", reason=NO_CAMERA)


@router.get("/status", response_model=StatusPublic)
async def public_status(repos: Repos, response: Response) -> StatusPublic:
    settings = get_settings()
    overview = await repos["public"].overview()
    model = await repos["inference"].promoted_vision_model()
    device = await repos["public"].scout_device()
    response.headers["Cache-Control"] = CACHE_SHORT
    return public_view.status_public(
        overview=overview,
        database_ok=True,
        detector=(
            {"version": model.version, "stage": (model.metrics or {}).get("stage")}
            if model
            else None
        ),
        scout=public_view.scout_public(device, camera_state(settings)),
    )


@router.get("/scout", response_model=ScoutPublic)
async def public_scout(repos: Repos, response: Response) -> ScoutPublic:
    response.headers["Cache-Control"] = CACHE_SHORT
    return public_view.scout_public(
        await repos["public"].scout_device(), camera_state(get_settings())
    )


@router.get("/events", response_model=list[EventSummaryPublic])
async def public_events(
    repos: Repos,
    response: Response,
    limit: int = Query(default=50, gt=0, le=200),
    urmind_class: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    south: float | None = Query(default=None, ge=-90, le=90),
    west: float | None = Query(default=None, ge=-180, le=180),
    north: float | None = Query(default=None, ge=-90, le=90),
    east: float | None = Query(default=None, ge=-180, le=180),
) -> list[EventSummaryPublic]:
    corners = (south, west, north, east)
    if any(value is None for value in corners) and any(value is not None for value in corners):
        raise HTTPException(status_code=422, detail="bbox exige south, west, north e east")
    bbox = None if corners[0] is None else (south, west, north, east)  # type: ignore[assignment]
    rows = await repos["public"].events(
        limit=limit, bbox=bbox, urmind_class=urmind_class, status=status, since=since
    )
    response.headers["Cache-Control"] = CACHE_SHORT
    return [public_view.summary(row) for row in rows]


@router.get("/events/{event_id}", response_model=EventDetailPublic)
async def public_event(event_id: uuid.UUID, repos: Repos, response: Response) -> EventDetailPublic:
    row = await repos["public"].event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada")
    try:
        dossier = await repos["service"].event_dossier(event_id)
    except EventNotFoundError as exc:  # pragma: no cover - corrida improvável
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada") from exc

    capture = dossier.get("capture")
    quality = None
    if capture is not None:
        stored = await repos["service"].captures.get(capture["id"])
        quality = stored.quality if stored else None
    image = public_view.image_availability(capture, quality)
    detections = [
        DetectionPublic(
            urmind_class=item["urmind_class"],
            confidence=item["confidence"],
            bbox=item["bbox"] if image.available else None,
        )
        for item in dossier.get("detections", [])
    ]
    risk_row = await repos["decisions"].latest_risk(event_id)
    risk = public_view.risk_public(
        {
            "severity": risk_row.severity,
            "priority_score": risk_row.priority_score,
            "uncertainty": risk_row.uncertainty,
            "factors": risk_row.factors,
            "created_at": risk_row.created_at,
        }
        if risk_row
        else None
    )
    rule = await repos["decisions"].get_rule(risk_row.responsibility_rule_id) if risk_row else None
    responsibility = public_view.responsibility_public(
        {"responsible": rule.responsible, "source": rule.source, "version": rule.version}
        if rule
        else None
    )
    action_row = await repos["decisions"].get_action(risk_row.action_id) if risk_row else None
    action = (
        ActionPublic(code=action_row.code, label=action_row.label, version=action_row.version)
        if action_row
        else None
    )
    context = public_view.context_public(
        [
            {"source": item["source"], "payload": item, "fetched_at": item.get("fetched_at")}
            for item in dossier.get("context", [])
        ]
    )
    model = await repos["inference"].promoted_vision_model()
    base = public_view.summary(row)
    response.headers["Cache-Control"] = CACHE_SHORT
    return EventDetailPublic(
        **base.model_dump(),
        distance_to_road_m=row.get("distance_to_road_m"),
        location_accuracy_m=row.get("location_accuracy_m"),
        road=(
            RoadPublic(
                name=row.get("road_name"),
                highway=row.get("road_highway"),
                distance_m=row.get("distance_to_road_m"),
                jurisdiction=row.get("road_jurisdiction"),
            )
            if row.get("road_segment_id")
            else None
        ),
        detections=detections,
        image=image,
        risk=risk,
        action=action,
        responsibility=responsibility,
        context=context,
        prediction=public_view.prediction_public(await repos["public"].prediction(event_id)),
        trace=public_view.decision_trace(
            capture=capture,
            detections=detections,
            context=context,
            risk=risk,
            responsibility=responsibility,
            action=action,
            model_version=model.version if model else None,
            event=base,
        ),
        model_version=model.version if model else None,
        model_stage=(model.metrics or {}).get("stage") if model else None,
        dataset_version=None,
        reviewed=bool(dossier.get("reviews")),
    )


@router.get("/transparency", response_model=TransparencyPublic)
async def public_transparency(repos: Repos, response: Response) -> TransparencyPublic:
    model = await repos["inference"].promoted_vision_model()
    dataset = None
    if model is not None and model.dataset_version_id is not None:
        dataset_row = await repos["inference"].dataset_version(model.dataset_version_id)
        dataset = (
            {
                "name": dataset_row.name,
                "version": dataset_row.version,
                "license": dataset_row.license,
                "source": dataset_row.source,
            }
            if dataset_row
            else None
        )
    response.headers["Cache-Control"] = CACHE_MEDIUM
    return public_view.transparency_public(
        {"name": model.name, "version": model.version, "metrics": model.metrics} if model else None,
        dataset,
    )


@router.get("/scout/frame")
async def public_scout_frame() -> Response:
    """Último frame real da câmera do Scout, quando existir fonte configurada."""
    settings = get_settings()
    if not settings.scout_frame_url:
        raise HTTPException(status_code=503, detail=NO_CAMERA)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            upstream = await client.get(settings.scout_frame_url)
            if len(upstream.content) > MAX_FRAME_BYTES:
                raise HTTPException(status_code=502, detail="quadro acima do tamanho aceito")
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"câmera inacessível: {type(exc).__name__}"
            ) from None
    if upstream.status_code != 200:
        raise HTTPException(status_code=502, detail="câmera respondeu com erro")
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/scout/stream")
async def public_scout_stream(request: Request) -> StreamingResponse:
    """Repasse do vídeo do Scout com limite de espectadores e fechamento garantido."""
    settings = get_settings()
    source = settings.scout_stream_url
    if not source:
        raise HTTPException(status_code=503, detail=NO_CAMERA)
    slots = stream_slots(settings)
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.05)
    except TimeoutError:
        raise HTTPException(status_code=503, detail=STREAM_BUSY) from None

    async def frames() -> AsyncIterator[bytes]:
        client = httpx.AsyncClient(timeout=None)
        try:
            async with client.stream("GET", source) as upstream:
                if upstream.status_code != 200:
                    return
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
        except httpx.HTTPError:
            return
        finally:
            # Fecha a conexão com a câmera e devolve a vaga mesmo se o cliente sumir.
            await client.aclose()
            slots.release()

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )
