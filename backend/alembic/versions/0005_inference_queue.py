"""Fila de inferência no Supabase Queues/pgmq (MASTER_PLAN §7).

Revision ID: 0005_inference_queue
Revises: 0004_decision_reference_data
Create Date: 2026-09-16

Capture com foto → mensagem `{capture_id}` em `inference_jobs`. A mensagem leva só
o ID; o Worker baixa a imagem do Storage. Nenhum papel de cliente enxerga a fila.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_inference_queue"
down_revision: str | None = "0004_decision_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL = r"""
create extension if not exists pgmq;

do $queue$
begin
    if not exists (select 1 from pgmq.list_queues() where queue_name = 'inference_jobs') then
        perform pgmq.create('inference_jobs');
    end if;
end
$queue$;

create or replace function public.enqueue_capture_inference()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $fn$
begin
    perform pgmq.send('inference_jobs', jsonb_build_object('capture_id', new.id));
    return new;
end;
$fn$;

revoke all on function public.enqueue_capture_inference() from public, anon, authenticated;

create or replace trigger captures_enqueue_inference
after insert on public.captures
for each row
when (new.storage_path is not null)
execute function public.enqueue_capture_inference();

revoke all on schema pgmq from anon, authenticated;
revoke all on all tables in schema pgmq from anon, authenticated;
revoke all on all functions in schema pgmq from anon, authenticated;
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
    raise RuntimeError("0005 é forward-only: a fila pode ter jobs pendentes")
