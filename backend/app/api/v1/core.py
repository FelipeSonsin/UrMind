"""Rotas do núcleo geoespacial: capturas e eventos. Todas exigem usuário autenticado."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import lru_cache, partial
from threading import BoundedSemaphore
from typing import Annotated, Any, Literal

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
from fastapi.responses import StreamingResponse

from app.auth import CurrentUser, require_user
from app.config import get_settings
from app.repositories.core import (
    CaptureRepository,
    DecisionRepository,
    EventRepository,
    InferenceRepository,
)
from app.schemas.core import (
    CAPTURE_PRIVACY_VERSION,
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
    decode_page_cursor,
)
from app.schemas.issue_taxonomy import get_issue
from app.services.context import NominatimReverse, pending_address, pending_report_context
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
from app.services.territory import TerritoryUnavailable, assess_brazil_location

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


def page_boundary(
    cursor: Annotated[str | None, Query(max_length=120)] = None,
) -> tuple[datetime, uuid.UUID] | None:
    try:
        return decode_page_cursor(cursor)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Cursor inválido") from exc


PageBoundary = Annotated[tuple[datetime, uuid.UUID] | None, Depends(page_boundary)]


async def get_photo_gate_policy(service: Core) -> PhotoGatePolicy:
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Configuração do porteiro indisponível")
    return await service.decisions.photo_gate_policy()


async def get_address_provider(request: Request) -> AsyncIterator[NominatimReverse]:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Banco do runtime não configurado")
    async with ExternalHttpClient(
        user_agent=get_settings().external_http_user_agent, timeout_seconds=3, max_attempts=1
    ) as client:
        yield NominatimReverse(client, sessions=database.sessionmaker)


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


@router.get("/ops/integrations")
async def integration_health(
    user: CurrentUser, service: Core, response: Response
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Saúde das integrações exige papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Observações indisponíveis")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.observed_integrations()


@router.get("/ops/reports")
async def operational_reports(
    user: CurrentUser,
    service: Core,
    response: Response,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Indicadores exigem papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Indicadores indisponíveis")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.report_totals(days)


@router.get("/ops/audit")
async def operational_audit(
    user: CurrentUser,
    service: Core,
    response: Response,
    after: PageBoundary = None,
    operation: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Auditoria exige papel interno")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Auditoria indisponível")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.decisions.audit_page(operation=operation, after=after, limit=limit)


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


@router.get("/captures/{capture_id}/timeline")
async def capture_timeline(
    capture_id: uuid.UUID, user: CurrentUser, service: Core, response: Response
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await service.capture_timeline(capture_id, user.id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/captures/markers")
async def capture_markers(
    user: CurrentUser,
    service: Core,
    response: Response,
    after: PageBoundary = None,
    only_mine: bool = False,
    include_unlocated: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.capture_markers(
        user.id,
        user.can_review and not only_mine,
        include_unlocated=include_unlocated,
        after=after,
        limit=limit,
    )


@router.get("/captures/export")
async def export_capture_markers(
    user: CurrentUser,
    service: Core,
    format: Literal["csv", "geojson"] = "csv",
    status: str = "",
    family: str = "",
    issue: str = "",
    start: datetime | None = None,
    end: datetime | None = None,
) -> StreamingResponse:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Exportação exige papel interno")
    if any(value is not None and value.tzinfo is None for value in (start, end)):
        raise HTTPException(status_code=422, detail="Período exige fuso horário")
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="Período inválido")
    cutoff = min(end, datetime.now(UTC)) if end else datetime.now(UTC)

    async def chunks():
        cursor = None
        first = True
        yield (
            "latitude,longitude,issue_code,status,date\r\n"
            if format == "csv"
            else '{"type":"FeatureCollection","features":['
        )
        while True:
            batch = await service.capture_markers(user.id, True, limit=100, after=cursor)
            if not batch:
                break
            for row in batch:
                at = row["created_at"]
                if isinstance(at, str):
                    at = datetime.fromisoformat(at)
                definition = get_issue(row.get("urmind_class") or "")
                if (
                    at > cutoff
                    or (start and at < start)
                    or (status and row["report_status"] != status)
                    or (issue and row.get("urmind_class") != issue)
                    or (family and (definition is None or definition.family.value != family))
                    or row.get("latitude") is None
                    or row.get("longitude") is None
                ):
                    continue
                values = {
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "issue_code": row.get("urmind_class") or "",
                    "status": row["report_status"],
                    "date": at.isoformat(),
                }
                if format == "csv":
                    buffer = io.StringIO()
                    csv.writer(buffer).writerow(values.values())
                    yield buffer.getvalue()
                else:
                    yield ("" if first else ",") + json.dumps(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [values.pop("longitude"), values.pop("latitude")],
                            },
                            "properties": values,
                        }
                    )
                    first = False
            last = batch[-1]
            cursor = (last["created_at"], last["id"])
            if len(batch) < 100:
                break
        if format == "geojson":
            yield "]}"

    return StreamingResponse(
        chunks(),
        media_type="text/csv" if format == "csv" else "application/geo+json",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="urmind-relatos.{format}"',
        },
    )


async def _validate_operational_location(session: Any, coordinate: Coordinate) -> tuple[str, str]:
    try:
        status, source_sha = await assess_brazil_location(session, coordinate)
    except TerritoryUnavailable as exc:
        raise HTTPException(status_code=503, detail="Validacao territorial indisponivel") from exc
    if status == "outside":
        raise HTTPException(
            status_code=422,
            detail="Localizacao fora do Brasil. Corrija o ponto antes de enviar; o rascunho foi preservado.",
        )
    return status, source_sha


@router.patch("/captures/{capture_id}/location")
async def capture_location(
    capture_id: uuid.UUID, payload: Coordinate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if payload.latitude == 0 and payload.longitude == 0:
        raise HTTPException(status_code=422, detail="confirme uma localização válida")
    capture = await service.captures.get_for_review(capture_id)
    if capture is None or (capture.quality or {}).get("uploaded_by") != user.id:
        raise HTTPException(status_code=404, detail="Captura não encontrada")
    territory_status, territory_sha = await _validate_operational_location(
        service.captures.session, payload
    )
    if not await service.captures.fill_missing_location(capture_id, user.id, payload):
        raise HTTPException(status_code=409, detail="A localização original já está registrada")
    if service.decisions is None:
        raise HTTPException(status_code=503, detail="Auditoria indisponível")
    await service.decisions.add_audit(
        operation="capture_location",
        entity_type="capture",
        entity_id=capture_id,
        actor=user.id,
        before={},
        after={"source": "manual"},
        event_hash=f"location-{uuid.uuid4()}",
    )
    quality = pending_address(capture.quality or {})
    quality["operational_territory"] = {
        "country": "BR",
        "status": territory_status,
        "source_sha256": territory_sha,
    }
    if territory_status == "uncertain":
        quality["location_review_required"] = True
    capture.quality = quality
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


@router.get("/captures/nearby-reports")
async def nearby_reports(
    user: CurrentUser,
    service: Core,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    policy: Annotated[PhotoGatePolicy | None, Depends(get_photo_gate_policy)] = None,
) -> list[dict[str, Any]]:
    if latitude == 0 and longitude == 0:
        raise HTTPException(status_code=422, detail="Confirme a localização")
    rows = await service.captures.nearby_reports(
        user.id,
        Coordinate(latitude=latitude, longitude=longitude),
        (policy or PhotoGatePolicy()).nearby_radius_m,
    )
    # Cross-owner suggestions can only be published, and reveal no private protocol or image.
    return [{"public_id": row["public_id"], "distance_m": round(row["distance_m"])} for row in rows]


async def require_photo_consent(
    user: CurrentUser,
    service: Core,
    privacy_version: Annotated[str | None, Form(max_length=100)] = None,
) -> None:
    if privacy_version is not None:
        if privacy_version != CAPTURE_PRIVACY_VERSION:
            raise HTTPException(
                status_code=409, detail="Aviso de privacidade atualizado; leia e aceite novamente."
            )
        await service.captures.accept_privacy_notice(user.id, privacy_version)
        await service.captures.session.commit()
    elif not await service.captures.has_privacy_consent(user.id, CAPTURE_PRIVACY_VERSION):
        raise HTTPException(
            status_code=422, detail="Leia e aceite o aviso de privacidade antes de enviar."
        )


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


@router.post(
    "/captures/photo",
    status_code=201,
    dependencies=[Depends(require_photo_consent), Depends(photo_admission_lease)],
)
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
    manual_overrides_exif: Annotated[bool, Form()] = False,
    tz_offset_minutes: Annotated[int | None, Form(ge=-840, le=840)] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    location_timestamp: Annotated[datetime | None, Form()] = None,
    heading_deg: Annotated[float | None, Form(ge=0, le=360)] = None,
    speed_mps: Annotated[float | None, Form(ge=0)] = None,
    note: Annotated[str | None, Form(max_length=500)] = None,
    user_description: Annotated[str | None, Form(max_length=500)] = None,
    additional_to: Annotated[str | None, Form(pattern=r"^[a-f0-9]{32}$")] = None,
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
    if manual_overrides_exif and (
        location_source is not LocationSource.MANUAL or latitude is None or longitude is None
    ):
        raise HTTPException(status_code=422, detail="correcao EXIF exige ponto manual confirmado")
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
        manual_overrides_exif=manual_overrides_exif,
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
    if ingest.capture.coordinate is not None:
        territory_status, territory_sha = await _validate_operational_location(
            service.captures.session, ingest.capture.coordinate
        )
        ingest.capture.quality["operational_territory"] = {
            "country": "BR",
            "status": territory_status,
            "source_sha256": territory_sha,
        }
        if territory_status == "uncertain":
            ingest.capture.quality["location_review_required"] = True
    if additional_to is not None:
        if ingest.capture.coordinate is None:
            raise HTTPException(status_code=422, detail="Evidência adicional exige localização")
        candidates = await service.captures.nearby_reports(
            user.id, ingest.capture.coordinate, (gate_policy or PhotoGatePolicy()).nearby_radius_m
        )
        parent = next((row for row in candidates if row["public_id"] == additional_to), None)
        if parent is None:
            raise HTTPException(status_code=404, detail="Relato próximo não disponível")
        ingest.capture.quality["additional_evidence"] = {
            "capture_id": str(parent["id"]),
            "public_id": additional_to,
            "confirmed_by_sender": True,
        }
        ingest.capture.quality["inference"] = {
            "status": "needs_review",
            "reason": "additional_evidence",
        }
        await service.captures.session.rollback()
    if ingest.capture.coordinate is not None:
        ingest.capture.quality["report_context"] = pending_report_context()
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
        if address.status != "ok":
            ingest.capture.quality = pending_address(ingest.capture.quality)
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
        if additional_to is not None:
            link = ingest.capture.quality["additional_evidence"]
            assert isinstance(link, dict) and ingest.capture.coordinate is not None
            await service.captures.get_for_review(uuid.UUID(str(link["capture_id"])))
            latest = await service.captures.nearby_reports(
                user.id,
                ingest.capture.coordinate,
                (gate_policy or PhotoGatePolicy()).nearby_radius_m,
            )
            if not any(row["public_id"] == additional_to for row in latest):
                raise HTTPException(
                    status_code=409, detail="Relato alterado durante o envio; tente novamente"
                )
        result = await service.register_capture(ingest.capture)
        if additional_to is not None and result["created"]:
            if service.decisions is None:
                raise HTTPException(status_code=503, detail="Auditoria indisponível")
            await service.captures.notify_evidence_change(uuid.UUID(str(link["capture_id"])))
            await service.decisions.add_audit(
                operation="attach_report_evidence",
                entity_type="capture",
                entity_id=result["id"],
                actor=user.id,
                before={},
                after={"additional_to": additional_to},
                event_hash=hashlib.sha256(
                    f"attach:{result['id']}:{additional_to}".encode()
                ).hexdigest(),
            )
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
        "additional_evidence": additional_to is not None,
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
    after: PageBoundary = None,
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
        after=after,
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
        "additional_evidence": (capture.quality or {}).get("additional_evidence"),
        "location_conflict": (capture.quality or {}).get("location_conflict", False),
        # Sugestão zero-shot não calibrada; só a revisão vê e só o revisor decide a classe.
        "urban_auxiliary": ((capture.quality or {}).get("inference") or {}).get("urban_auxiliary"),
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


@router.post("/captures/{capture_id}/detach-evidence")
async def detach_evidence(
    capture_id: uuid.UUID, user: CurrentUser, service: Core
) -> dict[str, bool]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    capture = await service.captures.get_for_review(capture_id)
    if capture is None or service.decisions is None:
        raise HTTPException(status_code=404, detail="Relato não encontrado")
    quality = deepcopy(capture.quality or {})
    link = quality.pop("additional_evidence", None)
    if link is None:
        return {"detached": False}
    quality["inference"] = {
        "status": "needs_review",
        "reason": "evidence_detached",
        "at": datetime.now(UTC).isoformat(),
    }
    capture.quality = quality
    await service.captures.notify_evidence_change(uuid.UUID(link["capture_id"]))
    await service.decisions.add_review(
        event_id=None,
        capture_id=capture_id,
        reviewer=user.id,
        decision="detach_evidence",
        corrected_class=None,
        notes="Evidência desanexada para revisão independente",
    )
    await service.decisions.add_audit(
        operation="detach_report_evidence",
        entity_type="capture",
        entity_id=capture_id,
        actor=user.id,
        before={"additional_evidence": link},
        after={},
        event_hash=hashlib.sha256(
            f"detach:{capture_id}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest(),
    )
    await service.captures.session.commit()
    return {"detached": True}


@router.post("/captures/{capture_id}/reviews", status_code=201)
async def review_capture(
    capture_id: uuid.UUID, payload: CaptureReviewCreate, user: CurrentUser, service: Core
) -> dict[str, Any]:
    if not user.can_review:
        raise HTTPException(status_code=403, detail="Revisão exige papel de revisor")
    if payload.corrected_location is not None:
        await _validate_operational_location(service.captures.session, payload.corrected_location)
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


def _publication_evidence_quality(quality: dict[str, Any]) -> dict[str, Any]:
    """Compare review/evidence fields while allowing independent Worker enrichment."""
    volatile = {"inference", "address", "address_history", "report_context"}
    return {key: value for key, value in quality.items() if key not in volatile}


@router.post("/events/{event_id}/publication")
async def publish_event(
    event_id: uuid.UUID,
    payload: PublicationRequest,
    user: CurrentUser,
    service: Core,
    storage: Storage,
    policy: Annotated[PhotoGatePolicy | None, Depends(get_photo_gate_policy)] = None,
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
            image = await _decode_upload(raw, partial(sanitize_public_image, policy=policy))
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
                or _publication_evidence_quality(capture.quality or {})
                != _publication_evidence_quality(expected_quality)
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
