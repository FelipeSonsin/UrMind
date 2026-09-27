"""Painel público, somente leitura (§16.1, §17).

Sem escrita. Acesso público exige publicação explícita; autenticação opcional
permite ao titular consultar seu próprio resultado. Cada resposta é montada por `app.services.public_view`
a partir dos schemas de `app.schemas.public`: o que não está no contrato não sai.

A câmera do Scout nunca é exposta direto ao público: o backend fica no meio, aplica o
portão de privacidade e limita conexões simultâneas.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from threading import BoundedSemaphore
from typing import Annotated, Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.core import get_storage
from app.auth import AuthenticatedUser, require_user
from app.config import Settings, get_settings
from app.repositories.core import (
    CaptureRepository,
    DecisionRepository,
    EventRepository,
    PublicImageQuota,
    PublicRepository,
    QuotaExceededError,
    QuotaUnavailableError,
)
from app.schemas.issue_taxonomy import taxonomy_payload
from app.schemas.public import (
    ActionPublic,
    AddressSearchPublic,
    AddressSearchRequest,
    DetectionPublic,
    EventDetailPublic,
    EventSummaryPublic,
    IssueTaxonomyPublic,
    RoadPublic,
    ScoutCameraPublic,
    ScoutPublic,
    StatusPublic,
    TransparencyPublic,
)
from app.services import public_view
from app.services.context import STATUS_OK, NominatimSearch
from app.services.core import CoreService, EventNotFoundError
from app.services.external_sources.http import ExternalHttpClient
from app.services.report import build_urban_analysis
from app.services.storage import (
    MAX_BYTES,
    InvalidImageError,
    StorageClient,
    StorageError,
)

router = APIRouter(prefix="/api/v1/public", tags=["public"])

CACHE_SHORT = "public, max-age=5"
CACHE_MEDIUM = "public, max-age=30"
NO_CAMERA = "nenhuma fonte de câmera configurada para o Scout"
STREAM_BUSY = "limite de espectadores simultâneos atingido"
MAX_FRAME_BYTES = 8 * 1024 * 1024
_stream_slots: asyncio.Semaphore | None = None
# Local concurrency backpressure complements the shared PostgreSQL rate budget;
# public reads cannot exhaust upload decode slots.
_public_image_slots = BoundedSemaphore(4)
# Cheap lookup attempts and expensive, verified downloads have independent budgets.
# ASGI client is resolved by the server's trusted-proxy configuration; never parse
# caller-supplied X-Forwarded-For here. JWT identity, when present, is verified.


def image_quota(request: Request) -> PublicImageQuota:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Controle de consultas indisponível")
    return PublicImageQuota(database.sessionmaker)


async def _admit_image(
    quota: PublicImageQuota, caller: str, event_id: uuid.UUID | None = None
) -> None:
    stage: Literal["lookup", "download"] = "download" if event_id is not None else "lookup"
    try:
        await quota.admit(stage, hashlib.sha256(caller.encode()).hexdigest(), event_id)
    except QuotaExceededError:
        structlog.get_logger(__name__).info("public_image_quota_rejected", category=stage)
        raise HTTPException(
            status_code=429,
            detail="Limite de consultas de imagem atingido",
            headers={"Retry-After": "60"},
        ) from None
    except QuotaUnavailableError:
        # Never expose SQL parameters, connection details or fall back to local memory.
        structlog.get_logger(__name__).warning("public_image_quota_unavailable", category=stage)
        raise HTTPException(status_code=503, detail="Controle de consultas indisponível") from None


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
            "service": CoreService(
                CaptureRepository(session), EventRepository(session), DecisionRepository(session)
            ),
        }


Repos = Annotated[dict[str, Any], Depends(repositories)]


@router.get("/photo-policy")
async def public_photo_policy(repos: Repos) -> dict[str, Any]:
    decisions = repos["service"].decisions
    if decisions is None:
        raise HTTPException(status_code=503, detail="Política indisponível")
    policy = await decisions.photo_gate_policy()
    return policy.model_dump(
        include={"min_side", "brightness_min", "brightness_max", "laplacian_min"}
    )


@router.get("/privacy-notice")
async def public_privacy_notice() -> dict[str, str]:
    from app.schemas.core import CAPTURE_PRIVACY_TEXT, CAPTURE_PRIVACY_VERSION

    return {"version": CAPTURE_PRIVACY_VERSION, "text": CAPTURE_PRIVACY_TEXT}


@router.get("/capture-markers")
async def public_capture_markers(repos: Repos) -> list[dict[str, Any]]:
    service = repos["service"]
    if service.decisions is None:
        return []
    policy = await service.decisions.photo_gate_policy()
    if not policy.public_capture_markers_enabled:
        return []
    return await service.capture_markers("", public=True)


async def optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
) -> AuthenticatedUser | None:
    """A supplied token must validate; missing credentials retain public-only access."""
    return await require_user(credentials) if credentials else None


OptionalUser = Annotated[AuthenticatedUser | None, Depends(optional_user)]


@router.get("/auth-origin")
async def public_auth_origin() -> dict[str, str | bool]:
    """Publishable Auth origin, so a PWA cannot sign visitors into another project."""
    settings = get_settings()
    configured = settings.supabase_url
    if not configured:
        raise HTTPException(status_code=503, detail="Auth não configurado")
    return {
        "auth_origin": configured.rstrip("/"),
        "visitor_upload_enabled": await _anonymous_auth_enabled(settings),
    }


async def _anonymous_auth_enabled(settings: Settings) -> bool:
    """Advertise visitor upload only if Supabase Auth actually accepts it."""
    if not (
        settings.visitor_upload_enabled
        and settings.supabase_url
        and settings.supabase_publishable_key
    ):
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                f"{settings.supabase_url}/auth/v1/settings",
                headers={"apikey": settings.supabase_publishable_key},
            )
        if response.status_code != 200:
            return False
        external = response.json().get("external")
        return isinstance(external, dict) and external.get("anonymous_users") is True
    except (httpx.HTTPError, ValueError, TypeError):
        return False


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
    device = await repos["public"].scout_device()
    response.headers["Cache-Control"] = CACHE_SHORT
    return public_view.status_public(
        overview=overview,
        database_ok=True,
        detector=None,
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


@router.get("/events/{public_id}", response_model=EventDetailPublic)
async def public_event(
    public_id: str, repos: Repos, response: Response, user: OptionalUser
) -> EventDetailPublic:
    row = await repos["public"].event(public_id, owner_id=user.id if user else None)
    if row is None:
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada")
    public_id = row["public_id"]
    event_id = uuid.UUID(str(row["id"]))
    try:
        dossier = await repos["service"].event_dossier(event_id)
    except EventNotFoundError as exc:  # pragma: no cover - corrida improvável
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada") from exc

    capture = dossier.get("capture")
    quality = None
    if capture is not None:
        stored = await repos["service"].captures.get(capture["id"])
        quality = stored.quality if stored else None
    published = await repos["public"].event(event_id) if user else row
    derivative = published.get("publication_image") if published else None
    derivative = derivative if isinstance(derivative, dict) else {}
    image_quality = (
        {**(quality or {}), "public_image": derivative}
        if (
            published
            and derivative.get("review_id") == published.get("publication_review_id")
            and isinstance(derivative.get("storage_path"), str)
            and derivative["storage_path"].startswith(f"public-derived/{event_id}/")
        )
        else None
    )
    image = public_view.image_availability(capture, image_quality)
    if image.available:
        image = image.model_copy(update={"url": f"/api/v1/public/events/{public_id}/image"})
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
            "id": risk_row.id,
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
    current_context = [
        {"source": item["source"], "payload": item, "fetched_at": item.get("fetched_at")}
        for item in dossier.get("context", [])
    ]
    context = public_view.context_public(
        public_view.assessed_context_records(
            risk_row.factors if risk_row else None, current_context
        )
    )
    base = public_view.summary(row)
    response.headers["Cache-Control"] = "private, no-store" if user else "no-store"
    response.headers["Vary"] = "Authorization"
    detail = EventDetailPublic(
        **base.model_dump(),
        distance_to_road_m=row.get("distance_to_road_m"),
        location_accuracy_m=None,
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
            model_version=None,
            event=base,
        ),
        model_version=None,
        model_stage=None,
        dataset_version=None,
        reviewed=bool(dossier.get("reviews")),
    )
    return detail.model_copy(update={"analysis": build_urban_analysis(detail)})


@router.get("/events/{public_id}/image")
async def public_event_image(
    public_id: str,
    repos: Repos,
    storage: Annotated[StorageClient, Depends(get_storage)],
    request: Request,
    user: OptionalUser,
    quota: Annotated[PublicImageQuota, Depends(image_quota)],
) -> Response:
    """Proxy only the derivative bound to this event's current public Review."""
    caller = (
        f"user:{user.id}"
        if user
        else f"peer:{request.client.host if request.client else 'unknown'}"
    )
    await _admit_image(quota, caller)
    row = await repos["public"].event(public_id)
    if row is None or not row.get("capture_id"):
        raise HTTPException(status_code=404, detail="Imagem não disponível")
    event_id = uuid.UUID(str(row["id"]))
    capture = await repos["service"].captures.get(row["capture_id"])
    quality = (capture.quality or {}) if capture else {}
    derivative = row.get("publication_image")
    derivative = derivative if isinstance(derivative, dict) else {}
    image = public_view.image_availability(
        {"id": capture.id, "storage_path": capture.storage_path} if capture else None,
        {**quality, "public_image": derivative},
    )
    path = derivative.get("storage_path", "")
    if (
        not image.available
        or derivative.get("review_id") != row.get("publication_review_id")
        or not path.startswith(f"public-derived/{event_id}/")
    ):
        raise HTTPException(status_code=404, detail="Imagem não disponível")
    # The snapshot above contains plain values. Release this read transaction
    # before quota opens its short transaction, even with a one-connection pool.
    await repos["public"].session.rollback()
    await _admit_image(quota, caller, event_id)
    if not _public_image_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429, detail="Consultas de imagem ocupadas", headers={"Retry-After": "1"}
        )
    try:
        try:
            data = await storage.download(path)
            # Bytes were generated/validated at publication time. Verify the
            # immutable artifact, not a second raster decode on every read.
            if (
                len(data) > MAX_BYTES
                or not data.startswith(b"\xff\xd8\xff")
                or not data.endswith(b"\xff\xd9")
                or hashlib.sha256(data).hexdigest() != derivative["sha256"]
            ):
                raise InvalidImageError("derivative mismatch")
        except (StorageError, InvalidImageError):
            raise HTTPException(status_code=404, detail="Imagem não disponível") from None
    finally:
        _public_image_slots.release()
    # Download yielded control. A withdrawal, new Review or republish
    # during that interval must invalidate this pending response.
    current = await repos["public"].event(event_id)
    if (
        current is None
        or current.get("capture_id") != row.get("capture_id")
        or current.get("publication_review_id") != row.get("publication_review_id")
        or current.get("publication_image") != derivative
    ):
        raise HTTPException(status_code=404, detail="Imagem não disponível")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def address_search_provider(request: Request) -> AsyncIterator[NominatimSearch]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Busca de endereço indisponível")
    async with ExternalHttpClient(
        user_agent=get_settings().external_http_user_agent, timeout_seconds=4, max_attempts=1
    ) as client:
        yield NominatimSearch(client, sessions=database.sessionmaker)


@router.post("/geocode", response_model=AddressSearchPublic)
async def search_address(
    payload: AddressSearchRequest,
    provider: Annotated[NominatimSearch, Depends(address_search_provider)],
    response: Response,
) -> AddressSearchPublic:
    """Rua, número, bairro ou CEP → lugares sugeridos no Brasil (Nominatim/OSM).

    Uma busca por pedido explícito, sob o mesmo limite de 1 req/s do reverse; nunca
    grava nem registra o texto digitado.
    """
    try:
        result = await provider.search(payload.query)
    except ValueError:
        raise HTTPException(status_code=422, detail="Digite rua, número, bairro ou CEP.") from None
    if result.status != STATUS_OK:
        if result.error == "address_pending":
            raise HTTPException(
                status_code=429,
                detail="Busca ocupada. Tente de novo em instantes.",
                headers={"Retry-After": "2"},
            )
        raise HTTPException(status_code=503, detail="Busca de endereço indisponível no momento.")
    response.headers["Cache-Control"] = "private, no-store"
    return AddressSearchPublic.model_validate(result.data)


@router.get("/taxonomy", response_model=IssueTaxonomyPublic)
async def public_taxonomy(response: Response) -> IssueTaxonomyPublic:
    """Registro canônico da taxonomia; o frontend não mantém lista própria de classes."""
    response.headers["Cache-Control"] = CACHE_MEDIUM
    return IssueTaxonomyPublic.model_validate(taxonomy_payload())


@router.get("/transparency", response_model=TransparencyPublic)
async def public_transparency(repos: Repos, response: Response) -> TransparencyPublic:
    response.headers["Cache-Control"] = CACHE_MEDIUM
    return public_view.transparency_public()


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
