"""Rotas do núcleo geoespacial: capturas e eventos. Todas exigem usuário autenticado."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from app.auth import CurrentUser, require_user
from app.config import get_settings
from app.repositories.core import (
    CaptureRepository,
    DecisionRepository,
    EventRepository,
    InferenceRepository,
)
from app.schemas.core import (
    CaptureCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EventStatus,
    LocationSource,
    NearbyQuery,
    ReviewCreate,
    UrmindClass,
)
from app.services.core import CoreService, DuplicateKeyError, EventNotFoundError
from app.services.photo_ingest import ingest_photo
from app.services.storage import (
    MAX_BYTES,
    InvalidImageError,
    StorageClient,
    StorageError,
    StorageNotConfiguredError,
    object_path,
    validate_image,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])
log = structlog.get_logger()


async def get_inference_repository(request: Request) -> AsyncIterator[InferenceRepository]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Banco do runtime não configurado")
    async with database.session() as session:
        yield InferenceRepository(session)


async def get_core_service(request: Request) -> AsyncIterator[CoreService]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=503, detail="Banco do runtime não configurado (DATABASE_POOLER_URL)"
        )
    async with database.session() as session:
        yield CoreService(
            CaptureRepository(session), EventRepository(session), DecisionRepository(session)
        )


def get_storage() -> StorageClient:
    try:
        return StorageClient(get_settings())
    except StorageNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="Storage não configurado") from exc


Core = Annotated[CoreService, Depends(get_core_service)]
Inference = Annotated[InferenceRepository, Depends(get_inference_repository)]
Storage = Annotated[StorageClient, Depends(get_storage)]


@router.get("/ops/metrics")
async def ops_metrics(user: CurrentUser, inference: Inference) -> dict[str, Any]:
    """Métricas do Worker e da fila (§19), medidas na hora — nenhuma é estimada.

    Fica atrás do papel de revisor: é informação de operação, não do painel
    público. Quem prefere o terminal tem o mesmo dado em `python -m app.worker
    --stats`.
    """
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Métricas de operação exigem papel de revisor")
    return await inference.stats()


@router.get("/me")
async def me(user: CurrentUser) -> dict[str, Any]:
    """Papel decidido no servidor (JWT/app_metadata); o frontend só exibe."""
    return {"id": user.id, "email": user.email, "can_review": user.can_review}


@router.post("/captures", status_code=201)
async def create_capture(payload: CaptureCreate, service: Core) -> dict[str, Any]:
    return await service.register_capture(payload)


@router.post("/captures/photo", status_code=201)
async def upload_photo(
    user: CurrentUser,
    service: Core,
    storage: Storage,
    file: Annotated[UploadFile, File()],
    source: Annotated[CaptureSource, Form()] = CaptureSource.PWA_PHOTO,
    latitude: Annotated[float | None, Form(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
    location_source: Annotated[LocationSource, Form()] = LocationSource.GPS_DEVICE,
    tz_offset_minutes: Annotated[int | None, Form(ge=-840, le=840)] = None,
) -> dict[str, Any]:
    """Foto real → Storage privado → Capture (§6.1, §6.3). Reenvio da mesma foto não duplica."""
    if source not in (CaptureSource.PWA_PHOTO, CaptureSource.EXIF_UPLOAD, CaptureSource.SCOUT):
        raise HTTPException(
            status_code=422, detail="source deve ser pwa_photo, exif_upload ou scout"
        )
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=422, detail="informe latitude e longitude juntas")
    if location_source not in (
        LocationSource.GPS_DEVICE,
        LocationSource.MANUAL,
        LocationSource.GPS_SCOUT,
    ):
        raise HTTPException(
            status_code=422, detail="location_source deve ser gps_device, manual ou gps_scout"
        )

    data = await file.read(MAX_BYTES + 1)
    try:
        image = validate_image(data)
    except InvalidImageError as exc:
        code = 413 if len(data) > MAX_BYTES else 415
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    received_at = datetime.now(UTC)
    path = object_path(user.id, image, received_at)
    coordinate = (
        Coordinate(latitude=latitude, longitude=longitude, accuracy_m=accuracy_m)
        if latitude is not None and longitude is not None
        else None
    )
    ingest = ingest_photo(
        capture_key=f"photo-{image.sha256}",
        image_bytes=image.data,
        received_at=received_at,
        manual_coordinate=coordinate,
        manual_location_source=location_source,
        client_timezone=(
            timezone(timedelta(minutes=tz_offset_minutes))
            if tz_offset_minutes is not None
            else None
        ),
        storage_path=path,
        source=source,
    )
    ingest.capture.quality.update(
        {
            "sha256": image.sha256,
            "mime": image.mime,
            "width": image.width,
            "height": image.height,
            "bytes": len(image.data),
            "uploaded_by": user.id,
        }
    )
    existing = await service.captures.get_by_key(ingest.capture.capture_key)
    if existing is not None:
        log.info(
            "capture_deduplicated",
            capture_id=str(existing.id),
            requires_manual_location=existing.point is None,
        )
        return {
            "id": existing.id,
            "capture_key": existing.capture_key,
            "created": False,
            "storage_path": existing.storage_path,
            "requires_manual_location": existing.point is None,
        }
    try:
        await storage.upload(path, image)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result = await service.register_capture(ingest.capture)
    log.info(
        "capture_stored",
        capture_id=str(result["id"]),
        source=source.value,
        location_source=ingest.capture.source_location.value,
        exif_status=ingest.exif.status.value,
        requires_manual_location=ingest.requires_manual_location,
        bytes=len(image.data),
    )
    return {
        **result,
        "storage_path": path,
        "requires_manual_location": ingest.requires_manual_location,
        "location_source": ingest.capture.source_location.value,
        "exif_status": ingest.exif.status.value,
    }


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
    query = NearbyQuery(latitude=latitude, longitude=longitude, radius_m=radius_m, limit=limit)
    return await service.events_nearby(query)


@router.post("/captures/{capture_id}/consolidate")
async def consolidate_capture(capture_id: uuid.UUID, service: Core) -> dict[str, Any]:
    """Detections da captura → Events deduplicados com risco (§25 passos 9 e 11)."""
    try:
        consolidated = await service.consolidate_capture(capture_id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log.info(
        "capture_consolidated",
        capture_id=str(capture_id),
        event_ids=[str(item["event_id"]) for item in consolidated["events"]],
        created=[str(i["event_id"]) for i in consolidated["events"] if i.get("created")],
    )
    return consolidated


@router.get("/events/{event_id}")
async def event_detail(event_id: uuid.UUID, service: Core, storage: Storage) -> dict[str, Any]:
    try:
        dossier = await service.event_dossier(event_id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    capture = dossier.get("capture")
    image_url = None
    if capture and capture.get("storage_path"):
        try:
            image_url = await storage.signed_url(capture["storage_path"])
        except StorageError:
            image_url = None  # evidência indisponível não derruba o detalhe
    return {**dossier, "image_url": image_url}


@router.post("/events/{event_id}/reviews", status_code=201)
async def review_event(
    event_id: uuid.UUID, payload: ReviewCreate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    try:
        review = await service.review_event(event_id, payload, reviewer=user.id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Quem revisou fica no audit_log, não no log de aplicação: identificar a
    # pessoa em log de operação seria dado pessoal sem necessidade (§17).
    log.info(
        "event_reviewed",
        event_id=str(event_id),
        decision=payload.decision.value,
        status=review["status"],
    )
    return review
