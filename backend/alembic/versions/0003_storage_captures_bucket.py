"""Bucket privado de evidências e privilégios padrão fechados.

Revision ID: 0003_storage_captures_bucket
Revises: 0002_align_urmind_core
Create Date: 2026-09-16

- `storage.buckets.captures`: privado, 10 MB, só JPEG/PNG/WebP (§4.4, §17). O
  upload passa pelo FastAPI com a secret key; nenhuma policy libera o bucket ao
  cliente, então anon/authenticated não leem nem gravam objetos diretamente.
- Objetos que o `postgres` criar em `public` deixam de nascer com privilégio para
  anon/authenticated. Antes disso, toda tabela nova ficava exposta ao Data API
  até alguém lembrar do `revoke` (achado da auditoria pós-0002).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_storage_captures_bucket"
down_revision: str | None = "0002_align_urmind_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL = r"""
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('captures', 'captures', false, 10485760, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
    set public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

alter default privileges for role postgres in schema public
    revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on functions from anon, authenticated, public;
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
    raise RuntimeError("0003 é forward-only: o bucket pode conter evidências")
