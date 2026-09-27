"""Observabilidade sem infraestrutura nova (MASTER_PLAN §19).

O plano é explícito: nada de pilha de monitoramento separada. O que existe aqui
lê estruturas que já estão no banco — `pg_stat_statements` para SQL caro e as
próprias tabelas do domínio para responder se há material suficiente para
falar de drift. Métrica de fila e latência do Worker fica em
`InferenceRepository.stats()`, que já as mede.

Nenhuma função inventa número: quando falta dado, o retorno diz que falta.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_SLOW_QUERIES = """
select calls,
       round(mean_exec_time::numeric, 2) as mean_ms,
       round(total_exec_time::numeric, 2) as total_ms,
       rows,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 160) as query
  from extensions.pg_stat_statements
 where query not ilike '%%pg_stat_statements%%'
 order by mean_exec_time desc
 limit :limit
"""


async def slow_queries(session: AsyncSession, limit: int = 10) -> dict[str, Any]:
    """SQL mais caro por tempo médio, direto do `pg_stat_statements` do Supabase.

    A extensão vive no schema `extensions`. Se não estiver instalada, o retorno
    diz isso em vez de estourar: observabilidade não pode derrubar operação.
    """
    installed = await session.scalar(
        text("select count(*) from pg_extension where extname = 'pg_stat_statements'")
    )
    if not installed:
        return {
            "available": False,
            "reason": "pg_stat_statements não está instalada neste projeto",
            "queries": [],
        }
    rows = (await session.execute(text(_SLOW_QUERIES), {"limit": limit})).mappings().all()
    return {"available": True, "queries": [dict(row) for row in rows]}
