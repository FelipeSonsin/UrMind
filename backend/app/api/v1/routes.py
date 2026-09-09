"""Rotas operacionais da API (saúde e prontidão).

O domínio do UrMind fica em app/api/v1/core.py. Aqui só entra o que descreve o
estado do próprio processo.
"""

from fastapi import APIRouter, Request

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
