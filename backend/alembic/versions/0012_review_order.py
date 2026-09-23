"""Give review votes a durable insertion order, independent of transaction time.

Revision ID: 0012_review_order
Revises: 0011_security_indexes

PostgreSQL now() is constant within a transaction, so created_at plus UUID
cannot reliably identify the latest adjudication. Existing tied historical votes
need manual review; this sequence makes all new votes unambiguous.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_review_order"
down_revision: str | None = "0011_security_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table public.reviews add column review_sequence bigint generated always as identity"
    )
    op.execute(
        "create unique index reviews_event_sequence_idx "
        "on public.reviews (event_id, review_sequence)"
    )
    op.execute("revoke all on sequence public.reviews_review_sequence_seq from anon, authenticated")


def downgrade() -> None:
    raise RuntimeError("0012 is forward-only: dropping review order would corrupt adjudication")
