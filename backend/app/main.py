from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.core import router as core_router
from app.api.v1.routes import router as api_router
from app.config import get_settings
from app.db.session import Database, DatabaseNotConfiguredError
from app.logging import configure_logging

configure_logging()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        app.state.database = Database(settings)
    except DatabaseNotConfiguredError:
        # Sem DATABASE_URL a API sobe, mas /health declara `not_configured` e
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


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    request_id = request.headers.get("X-Correlation-ID", str(uuid4()))
    structlog.contextvars.bind_contextvars(correlation_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Correlation-ID"] = request_id
    return response


@app.exception_handler(DatabaseNotConfiguredError)
async def database_not_configured_handler(_request: Request, exc: DatabaseNotConfiguredError):
    log.warning("database_not_configured", error=str(exc))
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(api_router)
app.include_router(core_router)
