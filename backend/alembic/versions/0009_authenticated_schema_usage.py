"""Permite ao papel autenticado alcançar as tabelas publicadas no Realtime.

Revision ID: 0009_authenticated_schema_usage
Revises: 0008_sidewalk_not_municipal
Create Date: 2026-09-20

O projeto Supabase mantém ``USAGE`` do schema ``public`` revogado. A 0006 já
limitou ``authenticated`` a SELECT em ``events`` e ``risk_assessments``, mas sem
USAGE essas permissões e as policies não são alcançáveis. ``anon`` permanece
bloqueado e nenhuma permissão de escrita é concedida.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_authenticated_schema_usage"
down_revision: str | None = "0008_sidewalk_not_municipal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("revoke usage on schema public from anon")
    op.execute("grant usage on schema public to authenticated")


def downgrade() -> None:
    raise RuntimeError(
        "0009 é forward-only: revogar USAGE interromperia o Realtime autenticado"
    )
