"""Evaluate JWT once per statement in the privileged Realtime RLS policies.

Revision ID: 0014_realtime_policy_plan
Revises: 0013_realtime_role_scope
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_realtime_policy_plan"
down_revision: str | None = "0013_realtime_role_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("events", "risk_assessments"):
        op.execute(
            f"alter policy {table}_privileged_read on public.{table} using "
            "(((select auth.jwt())->'app_metadata'->>'urmind_role') in ('reviewer', 'admin'))"
        )


def downgrade() -> None:
    raise RuntimeError("0014 is forward-only: retain the reviewed RLS expression")
