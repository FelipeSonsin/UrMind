"""Rotas do núcleo geoespacial: capturas e eventos. Todas exigem usuário autenticado."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import lru_cache, partial
from threading import BoundedSemaphore
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

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
    CaptureReviewCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EventStatus,
    LocationSource,
    NearbyQuery,
    PhotoGatePolicy,
    PublicationRequest,
    ReviewCreate,
    UrmindClass,
)
from app.services.context import NominatimReverse
from app.services.core import CoreService, DuplicateKeyError, EventNotFoundError
from app.services.external_sources.http import ExternalHttpClient
from app.services.photo_ingest import ingest_photo
from app.services.storage import (
    MAX_BYTES,
    InvalidImageError,
    PhotoRejectedError,
    StorageClient,
    StorageError,
    StorageNotConfiguredError,
    UploadAdmission,
    UploadBusyError,
    ValidatedImage,
    object_path,
    sanitize_public_image,
    validate_image,
    validate_report_photo,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])
log = structlog.get_logger()
_decode_slots = BoundedSemaphore(2)
_image_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="image-decode")


@lru_cache
def upload_admission() -> UploadAdmission:
    return UploadAdmission()


async def _decode_upload(
    data: bytes, operation: Callable[[bytes], ValidatedImage] = validate_image
) -> ValidatedImage:
    if not _decode_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Processamento de imagens ocupado")
    try:
        future = _image_executor.submit(operation, data)
    except BaseException:
        _decode_slots.release()
        raise
    # The concurrent future completes only when its worker really stops (or
    # cancellation succeeds before it starts), independently of HTTP task cancellation.
    future.add_done_callback(lambda _: _decode_slots.release())
    return await asyncio.wrap_future(future)


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


async def get_photo_gate_policy(service: Core) -> PhotoGatePolicy:
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Configuração do porteiro indisponível")
    return await service.decisions.photo_gate_policy()


async def get_address_provider() -> AsyncIterator[NominatimReverse]:
    async with ExternalHttpClient(
        user_agent=get_settings().external_http_user_agent, timeout_seconds=3, max_attempts=1
    ) as client:
        yield NominatimReverse(client)


@router.get("/ops/photo-gate", response_model=PhotoGatePolicy)
async def read_photo_gate(user: CurrentUser, service: Core, response: Response) -> PhotoGatePolicy:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Configuração exige papel interno")
    response.headers["Cache-Control"] = "private, no-store"
    return await get_photo_gate_policy(service)


@router.put("/ops/photo-gate", response_model=PhotoGatePolicy)
async def update_photo_gate(
    payload: PhotoGatePolicy, user: CurrentUser, service: Core, response: Response
) -> PhotoGatePolicy:
    if not user.can_review or user.urmind_role != "admin":
        raise HTTPException(status_code=403, detail="Alteração exige administrador")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Configuração indisponível")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        before = await service.decisions.photo_gate_policy(lock=True)
        await service.decisions.save_photo_gate_policy(payload)
        await service.decisions.add_audit(
            operation="photo_gate_configuration",
            entity_type="operational_configuration",
            entity_id=uuid.uuid5(uuid.NAMESPACE_URL, "urmind:configuration:photo_gate"),
            actor=user.id,
            before=before.model_dump(),
            after=payload.model_dump(),
            event_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        )
        await service.captures.session.commit()
    except Exception:
        await service.captures.session.rollback()
        raise
    return payload


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


@router.get("/ops/models")
async def operational_models(
    user: CurrentUser, service: Core, response: Response
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Registro de modelos exige papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Registro indisponível")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.operational_models()


@router.get("/ops/reports")
async def operational_reports(
    user: CurrentUser, service: Core, response: Response
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Indicadores exigem papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Indicadores indisponíveis")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.report_totals()


@router.get("/ops/ground-truth")
async def operational_ground_truth(
    user: CurrentUser, service: Core, response: Response
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Ground Truth exige papel interno")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.tabular_ground_truth()


@router.get("/ops/audit")
async def operational_audit(
    user: CurrentUser,
    service: Core,
    response: Response,
    operation: Annotated[str | None, Query(max_length=100)] = None,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Auditoria exige papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Auditoria indisponível")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.audit_page(operation=operation, offset=offset, limit=limit)


@router.get("/me")
async def me(user: CurrentUser) -> dict[str, Any]:
    """Papel decidido no servidor (JWT/app_metadata); o frontend só exibe."""
    return {
        "id": user.id,
        "email": user.email,
        "can_review": user.can_review,
        "can_admin": user.can_review and user.urmind_role == "admin",
    }


async def create_capture(payload: CaptureCreate, service: Core) -> dict[str, Any]:
    if payload.detections or payload.source is CaptureSource.SCOUT:
        raise HTTPException(
            status_code=403, detail="Detecções e origem Scout são geradas pelo servidor"
        )
    return await service.register_capture(payload)


@router.get("/captures/{capture_id}/processing")
async def capture_processing(
    capture_id: uuid.UUID, user: CurrentUser, service: Core, response: Response
) -> dict[str, Any]:
    """Owner/reviewer status; never disclose private Storage paths or provider payloads."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await service.capture_processing(capture_id, user.id, user.can_review)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/captures/by-protocol/{protocol}")
async def capture_by_protocol(
    protocol: str, user: CurrentUser, service: Core, response: Response
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    capture = await service.captures.get_by_protocol(protocol, user.id)
    if capture is None:
        raise HTTPException(status_code=404, detail="Relato não encontrado")
    return {"capture_id": capture.id, "protocol_code": capture.protocol_code}


@router.get("/captures/markers")
async def capture_markers(
    user: CurrentUser,
    service: Core,
    response: Response,
    only_mine: bool = False,
    include_unlocated: bool = False,
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.capture_markers(
        user.id, user.can_review and not only_mine, include_unlocated=include_unlocated
    )


@router.patch("/captures/{capture_id}/location")
async def capture_location(
    capture_id: uuid.UUID, payload: Coordinate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if payload.latitude == 0 and payload.longitude == 0:
        raise HTTPException(status_code=422, detail="confirme uma localização válida")
    capture = await service.captures.get(capture_id)
    if capture is None or (capture.quality or {}).get("uploaded_by") != user.id:
        raise HTTPException(status_code=404, detail="Captura não encontrada")
    if not await service.captures.fill_missing_location(capture_id, user.id, payload):
        raise HTTPException(status_code=409, detail="A localização original já está registrada")
    await service.captures.session.commit()
    return {"capture_id": capture_id, "location_source": "manual"}


@router.get("/captures/{capture_id}/image")
async def capture_image(
    capture_id: uuid.UUID, user: CurrentUser, service: Core, storage: Storage, response: Response
) -> dict[str, str]:
    response.headers["Cache-Control"] = "private, no-store"
    capture = await service.captures.get(capture_id)
    if capture is None or (
        not user.can_review and (capture.quality or {}).get("uploaded_by") != user.id
    ):
        raise HTTPException(status_code=404, detail="Captura não encontrada")
    if not capture.storage_path:
        raise HTTPException(status_code=404, detail="Imagem não disponível")
    return {"image_url": await storage.signed_url(capture.storage_path)}


async def photo_admission_lease(user: CurrentUser, service: Core) -> AsyncIterator[None]:
    token = uuid.uuid4()
    acquired = await service.captures.acquire_photo_lease(user.id, token)
    await service.captures.session.commit()
    if not acquired:
        raise HTTPException(status_code=429, detail="Outro envio seu está em andamento. Aguarde.")
    try:
        # Bound admission below the lease expiry; no long transaction during I/O.
        async with asyncio.timeout(180):
            yield
    finally:
        await service.captures.session.rollback()
        await service.captures.release_photo_lease(user.id, token)
        await service.captures.session.commit()


@router.post("/captures/photo", status_code=201, dependencies=[Depends(photo_admission_lease)])
async def upload_photo(
    user: CurrentUser,
    service: Core,
    storage: Storage,
    file: Annotated[UploadFile, File()],
    gate_policy: Annotated[PhotoGatePolicy | None, Depends(get_photo_gate_policy)] = None,
    address_provider: Annotated[NominatimReverse | None, Depends(get_address_provider)] = None,
    source: Annotated[CaptureSource, Form()] = CaptureSource.PWA_PHOTO,
    latitude: Annotated[float | None, Form(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
    location_source: Annotated[LocationSource, Form()] = LocationSource.GPS_DEVICE,
    tz_offset_minutes: Annotated[int | None, Form(ge=-840, le=840)] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    location_timestamp: Annotated[datetime | None, Form()] = None,
    heading_deg: Annotated[float | None, Form(ge=0, le=360)] = None,
    speed_mps: Annotated[float | None, Form(ge=0)] = None,
    note: Annotated[str | None, Form(max_length=500)] = None,
    user_description: Annotated[str | None, Form(max_length=500)] = None,
) -> dict[str, Any]:
    """Foto real → Storage privado → Capture (§6.1, §6.3). Reenvio da mesma foto não duplica."""
    settings = get_settings()
    if user.is_anonymous and not settings.visitor_upload_enabled:
        raise HTTPException(status_code=503, detail="Registro público temporariamente indisponível")
    try:
        upload_admission().admit(user.id)
    except UploadBusyError as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": "60"}
        ) from exc
    if source not in (CaptureSource.PWA_PHOTO, CaptureSource.EXIF_UPLOAD):
        raise HTTPException(status_code=422, detail="source deve ser pwa_photo ou exif_upload")
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=422, detail="informe latitude e longitude juntas")
    if location_source not in (
        LocationSource.GPS_DEVICE,
        LocationSource.MANUAL,
    ):
        raise HTTPException(status_code=422, detail="location_source deve ser gps_device ou manual")
    if (captured_at is not None and captured_at.tzinfo is None) or (
        location_timestamp is not None and location_timestamp.tzinfo is None
    ):
        raise HTTPException(status_code=422, detail="timestamps do dispositivo exigem fuso")
    if source is not CaptureSource.PWA_PHOTO and captured_at is not None:
        raise HTTPException(status_code=422, detail="captured_at do cliente exige foto da PWA")
    if location_source is not LocationSource.GPS_DEVICE and any(
        value is not None for value in (location_timestamp, heading_deg, speed_mps)
    ):
        raise HTTPException(
            status_code=422, detail="metadados GPS exigem location_source=gps_device"
        )

    data = await file.read(MAX_BYTES + 1)
    try:
        image = await _decode_upload(data, partial(validate_report_photo, policy=gate_policy))
    except PhotoRejectedError as exc:
        # Store metrics/reasons only. No image, hash, EXIF or storage object.
        attempt_id = uuid.uuid4()
        if service.decisions is None:
            raise HTTPException(
                status_code=503, detail="Registro do porteiro indisponível"
            ) from exc
        await service.decisions.add_audit(
            operation="photo_gate_rejected",
            entity_type="photo_attempt",
            entity_id=attempt_id,
            actor=user.id,
            before={},
            after=exc.result,
            event_hash=hashlib.sha256(f"photo-gate:{attempt_id}".encode()).hexdigest(),
        )
        await service.captures.session.commit()
        log.info("capture_rejected", reason="photo_quality", reasons=exc.result["reasons"])
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidImageError as exc:
        code = 413 if len(data) > MAX_BYTES else 415
        log.info("capture_rejected", reason="invalid_image", status_code=code)
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    received_at = datetime.now(UTC)
    if captured_at is not None and captured_at > received_at + timedelta(minutes=5):
        raise HTTPException(
            status_code=422, detail="captured_at futuro além da tolerância de relógio"
        )
    if location_timestamp is not None and location_timestamp > received_at + timedelta(minutes=5):
        raise HTTPException(
            status_code=422, detail="location_timestamp futuro além da tolerância de relógio"
        )
    # A unique object per attempt makes compensation safe under concurrent
    # uploads of identical bytes. The SHA-256 remains in the path and metadata.
    path = object_path(user.id, image, received_at, upload_id=uuid.uuid4().hex)
    coordinate = (
        Coordinate(latitude=latitude, longitude=longitude, accuracy_m=accuracy_m)
        if latitude is not None and longitude is not None
        else None
    )
    if coordinate is not None and coordinate.latitude == 0 and coordinate.longitude == 0:
        raise HTTPException(status_code=422, detail="coordenada (0,0) inválida; confirme no mapa")
    # Idempotency is scoped to the authenticated owner. A global image hash
    # would return another user's private Capture/Storage path for the same bytes.
    owner_image_key = hashlib.sha256(f"{user.id}:{image.sha256}".encode()).hexdigest()
    ingest = ingest_photo(
        capture_key=f"photo-{owner_image_key}",
        image_bytes=image.data,
        received_at=received_at,
        manual_coordinate=coordinate,
        manual_location_source=location_source,
        client_captured_at=captured_at,
        storage_path=path,
        source=source,
        user_description=user_description if user_description is not None else note,
        location_conflict_distance_m=settings.location_conflict_distance_m,
    )
    ingest.capture.quality.update(
        {
            "sha256": image.sha256,
            "phash": image.phash,
            "mime": image.mime,
            "width": image.width,
            "height": image.height,
            "bytes": len(image.data),
            "uploaded_by": user.id,
            "public_upload": not user.can_review,
            "location_attestation": "unverified_client_claim",
            "photo_gate": image.photo_quality,
        }
    )
    if tz_offset_minutes is not None:
        ingest.capture.quality["upload_timezone_offset_minutes_unverified"] = tz_offset_minutes
    if location_source is LocationSource.GPS_DEVICE and coordinate is not None:
        ingest.capture.heading_deg = heading_deg
        ingest.capture.speed_mps = speed_mps
        if location_timestamp is not None:
            ingest.capture.quality["location_timestamp"] = location_timestamp.isoformat()
    existing = await service.captures.get_by_key(ingest.capture.capture_key)
    if existing is not None:
        await _deduplicated_capture(existing, ingest.capture.coordinate, service, user.id)
        raise HTTPException(
            status_code=409, detail="Você já enviou esta foto. Consulte Meus relatos."
        )
    if not user.can_review:
        if (await service.captures.recent_public_uploads(received_at)) >= (
            settings.public_capture_global_limit_per_hour
        ) or (
            await service.captures.recent_owner_uploads(user.id, received_at)
            >= settings.public_capture_limit_per_hour
        ):
            log.info("capture_rejected", reason="rate_limited", status_code=429)
            raise HTTPException(status_code=429, detail="Limite de capturas por hora atingido")
        # Do not hold an idle transaction during the external Storage upload.
        await service.captures.session.rollback()
    if image.phash is not None and await service.captures.recent_similar_photo(
        user.id, image.phash, received_at, (gate_policy or PhotoGatePolicy()).phash_distance
    ):
        attempt_id = uuid.uuid4()
        if service.decisions is None:
            raise HTTPException(status_code=503, detail="Registro do porteiro indisponível")
        await service.decisions.add_audit(
            operation="photo_gate_rejected",
            entity_type="photo_attempt",
            entity_id=attempt_id,
            actor=user.id,
            before={},
            after={
                "status": "REJECTED",
                "reasons": ["duplicate"],
                "version": "urmind-photo-quality-v1",
            },
            event_hash=hashlib.sha256(f"photo-gate:{attempt_id}".encode()).hexdigest(),
        )
        await service.captures.session.commit()
        raise HTTPException(
            status_code=409, detail="Você já enviou esta foto. Consulte Meus relatos."
        )
    await service.captures.session.rollback()
    if ingest.capture.coordinate is not None and address_provider is not None:
        address = await address_provider.fetch(
            ingest.capture.coordinate.latitude, ingest.capture.coordinate.longitude, received_at
        )
        ingest.capture.quality["address"] = {
            "status": address.status,
            "source": address.source,
            "fetched_at": address.fetched_at,
            "provenance": address.provenance,
            "road": address.data.get("road"),
            "suburb": address.data.get("suburb"),
            "city": address.data.get("city"),
            "attribution": address.data.get("attribution"),
        }
    try:
        uploaded = await storage.upload(path, image)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        if not user.can_review:
            await service.captures.lock_public_uploads()
            await service.captures.lock_owner_uploads(user.id)
            if (await service.captures.recent_public_uploads(received_at)) >= (
                settings.public_capture_global_limit_per_hour
            ) or (
                await service.captures.recent_owner_uploads(user.id, received_at)
                >= settings.public_capture_limit_per_hour
            ):
                log.info("capture_rejected", reason="rate_limited", status_code=429)
                raise HTTPException(status_code=429, detail="Limite de capturas por hora atingido")
        result = await service.register_capture(ingest.capture)
        # The dependency's deferred commit would otherwise happen after this
        # handler returns, too late to compensate a failed DB transaction.
        await service.captures.session.commit()
    except BaseException:
        try:
            await service.captures.session.rollback()
        finally:
            if uploaded:
                try:
                    await storage.delete(path)
                except StorageError as exc:
                    log.error("storage_compensation_failed", error=str(exc))
        raise
    if not result["created"]:
        if uploaded:
            await storage.delete(path)
        existing = await service.captures.get_by_key(ingest.capture.capture_key)
        if existing is None:
            raise RuntimeError("captura deduplicada não encontrada")
        return await _deduplicated_capture(existing, ingest.capture.coordinate, service, user.id)
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
        "requires_manual_location": ingest.requires_manual_location,
        "location_source": ingest.capture.source_location.value,
        "exif_status": ingest.exif.status.value,
    }


async def _deduplicated_capture(
    existing: Any, coordinate: Coordinate | None, service: CoreService, actor: str
) -> dict[str, Any]:
    if (existing.quality or {}).get("uploaded_by") != actor:
        # Do not reveal even the existence of another user's private Capture.
        raise HTTPException(status_code=409, detail="captura já registrada")
    previous = await service.captures.location(existing.id)
    if (previous is None) != (coordinate is None) or (
        previous is not None
        and coordinate is not None
        and (
            abs(previous["latitude"] - coordinate.latitude) > 1e-6
            or abs(previous["longitude"] - coordinate.longitude) > 1e-6
            or previous["accuracy_m"] != coordinate.accuracy_m
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="foto já registrada com outra localização; correção requer revisão",
        )
    log.info(
        "capture_deduplicated",
        capture_id=str(existing.id),
        requires_manual_location=existing.point is None,
    )
    return {
        "id": existing.id,
        "protocol_code": existing.protocol_code,
        "capture_key": existing.capture_key,
        "created": False,
        "requires_manual_location": existing.point is None,
    }


async def create_event(payload: EventCreate, service: Core) -> dict[str, Any]:
    if payload.model_version_id is not None or (payload.factors or {}).get("evidence"):
        raise HTTPException(status_code=403, detail="Linhagem de inferência é gerada pelo servidor")
    try:
        return await service.register_event(payload)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/events")
async def list_events(
    service: Core,
    user: CurrentUser,
    urmind_class: UrmindClass | None = None,
    status: EventStatus | None = None,
    limit: int = Query(default=100, gt=0, le=500),
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Leitura interna exige papel de revisor")
    return await service.list_events(
        urmind_class=urmind_class.value if urmind_class else None,
        status=status.value if status else None,
        limit=limit,
    )


@router.get("/events/nearby")
async def events_nearby(
    service: Core,
    user: CurrentUser,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_m: float = Query(default=500, gt=0, le=20000),
    limit: int = Query(default=100, gt=0, le=500),
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Leitura interna exige papel de revisor")
    query = NearbyQuery(latitude=latitude, longitude=longitude, radius_m=radius_m, limit=limit)
    return await service.events_nearby(query)


async def consolidate_capture(
    capture_id: uuid.UUID, service: Core, user: CurrentUser
) -> dict[str, Any]:
    """Detections da captura → Events deduplicados com risco (§25 passos 9 e 11)."""
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Consolidação manual exige papel de revisor")
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
async def event_detail(
    event_id: uuid.UUID, service: Core, storage: Storage, user: CurrentUser
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Leitura interna exige papel de revisor")
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


@router.get("/captures/{capture_id}/review")
async def capture_review_detail(
    capture_id: uuid.UUID, user: CurrentUser, service: Core, response: Response
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    capture = await service.captures.get(capture_id)
    if capture is None or service.decisions is None:
        raise HTTPException(status_code=404, detail="Relato não encontrado")
    response.headers["Cache-Control"] = "private, no-store"
    events = await service.events.for_capture(capture_id)
    return {
        "id": capture.id,
        "protocol_code": capture.protocol_code,
        "user_description": capture.user_description,
        "location": await service.captures.location(capture_id),
        "location_source": capture.source_location,
        "photo_gate": (capture.quality or {}).get("photo_gate"),
        "human_review": (capture.quality or {}).get("human_review"),
        "location_conflict": (capture.quality or {}).get("location_conflict", False),
        "events": [
            {
                "id": e.id,
                "public_id": e.public_id,
                "status": e.status,
                "origin": (e.factors or {}).get("origin", "inference"),
            }
            for e in events
        ],
        "reviews": await service.decisions.capture_review_history(capture_id),
    }


@router.post("/captures/{capture_id}/reviews", status_code=201)
async def review_capture(
    capture_id: uuid.UUID, payload: CaptureReviewCreate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    try:
        result = await service.review_capture(
            capture_id, payload, reviewer=user.id, reviewer_role=user.urmind_role
        )
        await service.captures.session.commit()
        return result
    except (EventNotFoundError, PermissionError, ValueError) as exc:
        await service.captures.session.rollback()
        code = (
            404
            if isinstance(exc, EventNotFoundError)
            else 403
            if isinstance(exc, PermissionError)
            else 422
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/events/{event_id}/reviews", status_code=201)
async def review_event(
    event_id: uuid.UUID, payload: ReviewCreate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    if payload.adjudicate and user.urmind_role != "admin":
        raise HTTPException(status_code=403, detail="Adjudicação exige papel de admin")
    try:
        review = await service.review_event(
            event_id, payload, reviewer=user.id, reviewer_role=user.urmind_role
        )
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


@router.post("/events/{event_id}/publication")
async def publish_event(
    event_id: uuid.UUID,
    payload: PublicationRequest,
    user: CurrentUser,
    service: Core,
    storage: Storage,
) -> dict[str, Any]:
    """Explicit reviewed publication; originals and inference remain unchanged."""
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Publicação exige papel de revisor")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Persistência de revisão indisponível")
    session = service.captures.session
    path: str | None = None
    uploaded = False
    try:
        event = await service.events.get_for_review(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Ocorrência não encontrada")
        before = dict((event.factors or {}).get("publication") or {})
        now = datetime.now(UTC)
        publication: dict[str, Any] = {
            "policy_version": "urmind-publication-v1",
            "status": "published" if payload.publish else "withdrawn",
            "reviewer": user.id,
            "published_at": now.isoformat(),
        }
        if payload.publish:
            reviews = await service.decisions.reviews(event_id)
            review = reviews[-1] if reviews else None
            if (
                event.status != "confirmed"
                or review is None
                or review.id != payload.review_id
                or review.reviewer != user.id
                or review.decision not in {"confirm", "correct"}
                or not payload.visible_content_reviewed
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Exige revisão atual confirmada e inspeção de privacidade da imagem",
                )
            capture = await service.captures.get(event.capture_id) if event.capture_id else None
            if capture is None or not capture.storage_path:
                raise HTTPException(status_code=409, detail="Evidência original indisponível")
            # Snapshot plain values before releasing ORM state/locks. No DB
            # transaction is held during external I/O or raster sanitization.
            expected_factors = deepcopy(event.factors or {})
            expected_capture_id = capture.id
            expected_path = capture.storage_path
            expected_quality = deepcopy(capture.quality or {})
            expected_review_id = review.id
            await session.rollback()
            raw = await storage.download(expected_path)
            source_hash = hashlib.sha256(raw).hexdigest()
            if source_hash != expected_quality.get("sha256"):
                raise HTTPException(status_code=409, detail="Checksum da evidência não confere")
            image = await _decode_upload(raw, sanitize_public_image)
            path = f"public-derived/{event_id}/{uuid.uuid4().hex}.jpg"
            uploaded = await storage.upload(path, image)
            if not uploaded:
                raise HTTPException(
                    status_code=409, detail="Conflito na criação da cópia sanitizada"
                )
            event = await service.events.get_for_review(event_id)
            if (
                event is None
                or event.status != "confirmed"
                or event.capture_id != expected_capture_id
                or (event.factors or {}) != expected_factors
            ):
                raise HTTPException(
                    status_code=409, detail="Ocorrência alterada durante publicação"
                )
            capture = await service.captures.get(expected_capture_id)
            if capture is None:
                raise HTTPException(status_code=409, detail="Evidência alterada durante publicação")
            await session.refresh(capture, with_for_update=True)
            reviews = await service.decisions.reviews(event_id)
            review = reviews[-1] if reviews else None
            if (
                capture.storage_path != expected_path
                or (capture.quality or {}) != expected_quality
                or review is None
                or review.id != expected_review_id
                or review.reviewer != user.id
                or review.decision not in {"confirm", "correct"}
            ):
                raise HTTPException(
                    status_code=409, detail="Revisão ou evidência alterada durante publicação"
                )
            publication["public_image"] = {
                "storage_path": path,
                "sha256": image.sha256,
                "source_sha256": source_hash,
                "content_type": image.mime,
                "visible_content_reviewed": True,
                "metadata_stripped": True,
                "face_redaction": image.photo_quality,
                "created_at": now.isoformat(),
                "review_id": str(review.id),
            }
            publication["review_id"] = str(review.id)
        event.factors = {**(event.factors or {}), "publication": publication}
        await service.decisions.add_audit(
            operation="publish_event" if payload.publish else "withdraw_event",
            entity_type="event",
            entity_id=event_id,
            actor=user.id,
            before=before,
            after={**publication, "reason": payload.reason},
            event_hash=f"publication-{uuid.uuid4()}",
        )
        await session.commit()
        return {"event_id": event_id, "publication_status": publication["status"]}
    except BaseException:
        await session.rollback()
        if uploaded and path:
            try:
                await storage.delete(path)
            except StorageError:
                log.error("publication_compensation_failed", event_id=str(event_id))
        raise
