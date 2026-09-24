"""Limit direct Realtime/Data API reads to privileged UrMind roles.

Revision ID: 0013_realtime_role_scope
Revises: 0012_review_order

Ordinary users use the backend API for scoped access. Until an event ownership
contract exists, an all-authenticated SELECT would disclose every event.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_realtime_role_scope"
down_revision: str | None = "0012_review_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("events", "risk_assessments"):
        op.execute(f"drop policy if exists {table}_authenticated_read on public.{table}")
        op.execute(
            f"create policy {table}_privileged_read on public.{table} "
            "for select to authenticated using "
            "((select auth.jwt()->'app_metadata'->>'urmind_role') in ('reviewer', 'admin'))"
        )


def downgrade() -> None:
    raise RuntimeError("0013 is forward-only: broad read access must not be restored implicitly")
