"""Persist event insertion order for leakage-resistant historical features.

Revision ID: 0015_event_order
Revises: 0014_realtime_policy_plan

The event occurrence time alone cannot prove the event was already available.
PostgreSQL now() also ties rows created in one transaction. A sequence excludes
backdated events inserted after the assessed event in that transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_event_order"
down_revision: str | None = "0014_realtime_policy_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table public.events add column event_sequence bigint generated always as identity"
    )
    op.execute(
        "create unique index events_segment_sequence_idx "
        "on public.events (road_segment_id, event_sequence)"
    )
    op.execute("revoke all on sequence public.events_event_sequence_seq from anon, authenticated")


def downgrade() -> None:
    raise RuntimeError(
        "0015 is forward-only: dropping event order would weaken historical features"
    )
