"""Persist risk assessment insertion order for deterministic latest reads.

Revision ID: 0016_assessment_order
Revises: 0015_event_order
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_assessment_order"
down_revision: str | None = "0015_event_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table public.risk_assessments "
        "add column assessment_sequence bigint generated always as identity"
    )
    op.execute(
        "create unique index risk_assessments_event_sequence_idx "
        "on public.risk_assessments (event_id, assessment_sequence)"
    )
    op.execute(
        "revoke all on sequence public.risk_assessments_assessment_sequence_seq "
        "from anon, authenticated"
    )


def downgrade() -> None:
    raise RuntimeError("0016 is forward-only: dropping order would make latest reads ambiguous")
