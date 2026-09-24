"""Supabase Realtime (Postgres Changes) para eventos e avaliações (MASTER_PLAN §16.1).

Revision ID: 0006_realtime_events
Revises: 0005_inference_queue
Create Date: 2026-09-17

V1 usa Postgres Changes só nas tabelas que atualizam a UI: `events` (novo evento,
status de revisão) e `risk_assessments` (risco reavaliado com contexto). Realtime
respeita RLS, então o usuário autenticado recebe apenas o que pode ler: SELECT, sem
escrita. `anon` continua sem acesso. Escala futura: Broadcast (não implementado).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_realtime_events"
down_revision: str | None = "0005_inference_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL = r"""
do $realtime$
declare
    t text;
begin
    foreach t in array array['events', 'risk_assessments'] loop
        if not exists (
            select 1 from pg_publication_tables
            where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t
        ) then
            execute format('alter publication supabase_realtime add table public.%I', t);
        end if;
        execute format('grant select on public.%I to authenticated', t);
        if not exists (
            select 1 from pg_policies
            where schemaname = 'public' and tablename = t and policyname = t || '_authenticated_read'
        ) then
            execute format(
                'create policy %I on public.%I for select to authenticated using (true)',
                t || '_authenticated_read', t
            );
        end if;
    end loop;
end
$realtime$;
"""


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        context.impl.static_output(SQL)
        return
    cursor = op.get_bind().connection.dbapi_connection.cursor()
    try:
        cursor.execute(SQL)
    finally:
        cursor.close()


def downgrade() -> None:
    raise RuntimeError("0006 é forward-only; remova a policy por nova revisão se necessário")
