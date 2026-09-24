"""Rotas operacionais da API (saúde e prontidão).

O domínio do UrMind fica em app/api/v1/core.py. Aqui só entra o que descreve o
estado do próprio processo.
"""

from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Estado real da API e da conexão PostgreSQL/PostGIS.

    Sem banco configurado o serviço sobe, mas isso é declarado como
    `not_configured` — nunca como `ok` (MASTER_PLAN §26).
    """
    result = {"status": "ok"}
    database = getattr(request.app.state, "database", None)
    if database is None:
        result["database"] = "not_configured"
    else:
        result.update(await database.health())
    return result


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str]:
    """Readiness for a load balancer: 200 only when the database answers.

    Liveness stays in `/health`. No configuration value or error text is exposed.
    """
    database = getattr(request.app.state, "database", None)
    if database is None:
        response.status_code = 503
        return {"status": "not_ready", "database": "not_configured"}
    try:
        await database.health()
    except Exception:  # noqa: BLE001 — any failure means not ready; details stay in logs
        response.status_code = 503
        return {"status": "not_ready", "database": "unreachable"}
    return {"status": "ready", "database": "connected"}
