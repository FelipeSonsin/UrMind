from contextlib import asynccontextmanager
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.v1.core import router as core_router
from app.api.v1.public import router as public_router
from app.api.v1.routes import router as api_router
from app.config import get_settings
from app.db.session import Database, DatabaseNotConfiguredError
from app.logging import configure_logging
from app.services.storage import MAX_BYTES

configure_logging()
log = structlog.get_logger()
MAX_CAPTURE_MULTIPART_BYTES = MAX_BYTES + 1024 * 1024


class CaptureBodyLimitMiddleware:
    """Bound multipart bytes before FastAPI spools an untrusted photo to disk."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/api/v1/captures/photo":
            await self.app(scope, receive, send)
            return

        async def too_large() -> None:
            await JSONResponse(status_code=413, content={"detail": "foto acima do limite"})(
                scope, receive, send
            )

        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_size = 0
        if declared_size > MAX_CAPTURE_MULTIPART_BYTES:
            await too_large()
            return
        if not headers.get(b"authorization", b"").lower().startswith(b"bearer "):
            await JSONResponse(
                status_code=401,
                content={"detail": "Autenticação necessária"},
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        # Inspect the entire bounded body before the multipart parser can run.
        # An exception from receive() is swallowed by Starlette as a generic 400;
        # spooling lets us return an honest 413 with no partial route execution.
        with SpooledTemporaryFile(max_size=1024 * 1024) as body:
            received_bytes = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > MAX_CAPTURE_MULTIPART_BYTES:
                    await too_large()
                    return
                body.write(chunk)
                if not message.get("more_body", False):
                    break

            body.seek(0)
            exhausted = False

            async def replay() -> dict[str, object]:
                nonlocal exhausted
                if exhausted:
                    return {"type": "http.disconnect"}
                chunk = body.read(64 * 1024)
                more_body = body.tell() < received_bytes
                exhausted = not more_body
                return {"type": "http.request", "body": chunk, "more_body": more_body}

            await self.app(scope, replay, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        app.state.database = Database(settings)
    except DatabaseNotConfiguredError:
        # Sem DATABASE_POOLER_URL a API sobe, mas /health declara `not_configured` e
        # as rotas de domínio respondem 503 — nada é simulado (§26).
        app.state.database = None
        log.warning("database_not_configured")
    yield
    if app.state.database is not None:
        await app.state.database.close()


app = FastAPI(
    title="URMIND API",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(CaptureBodyLimitMiddleware)


def content_security_policy() -> str:
    """Same-origin PWA plus explicit Auth/map origins; no arbitrary script host."""
    url = urlsplit(get_settings().supabase_url or "")
    remote = f"https://{url.hostname}" if url.scheme == "https" and url.hostname else ""
    websocket = f"wss://{url.hostname}" if remote else ""
    maps = " ".join(
        [
            "https://tiles.openfreemap.org",
            "https://basemaps.cartocdn.com",
            "https://tiles.basemaps.cartocdn.com",
            *[f"https://tiles-{letter}.basemaps.cartocdn.com" for letter in "abcd"],
        ]
    )
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            # Só compilação WebAssembly (ONNX Runtime da detecção ao vivo); eval de JS segue proibido.
            "script-src 'self' 'wasm-unsafe-eval'",
            "style-src 'self' 'unsafe-inline'",  # MapLibre positioning and evidence boxes
            "worker-src 'self' blob:",
            "font-src 'self'",
            f"img-src 'self' blob: data: {remote} {maps}",
            f"connect-src 'self' {remote} {websocket} {maps}",
        ]
    )


# Headers apply to API and the same-origin production PWA. A separately hosted
# frontend must configure the equivalent policy on its own HTTPS host.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(self), geolocation=(self), microphone=()",
}


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    request_id = request.headers.get("X-Correlation-ID", str(uuid4()))
    structlog.contextvars.bind_contextvars(correlation_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Correlation-ID"] = request_id
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.path not in {"/api/v1/docs", "/docs/oauth2-redirect", "/redoc"}:
        response.headers.setdefault("Content-Security-Policy", content_security_policy())
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.exception_handler(DatabaseNotConfiguredError)
async def database_not_configured_handler(_request: Request, exc: DatabaseNotConfiguredError):
    log.warning("database_not_configured", error=str(exc))
    return JSONResponse(status_code=503, content={"detail": str(exc)})


if get_settings().cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Correlation-ID"],
    )

app.include_router(api_router)
app.include_router(core_router)
app.include_router(public_router)

# O PWA vai por último: as rotas /api/v1 já estão registradas e o StaticFiles(html=True)
# responde index.html para as rotas do app (navegação client-side).
_frontend = get_settings().serve_frontend_dir
if _frontend:
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="pwa")
    log.info("frontend_served", directory=_frontend)
