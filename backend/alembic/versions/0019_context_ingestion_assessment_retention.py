"""Record trustworthy context ingestion time and retain archived assessments.

Revision ID: 0019_context_retention
Revises: 0018_serialized_order

Existing EventContext rows retain NULL ingested_at: their original ingestion
instant cannot be reconstructed from a provider timestamp or transaction now().
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_context_retention"
down_revision: str | None = "0018_serialized_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table public.event_context add column ingested_at timestamptz")
    op.execute(
        "alter table public.event_context alter column ingested_at set default clock_timestamp()"
    )
    op.execute(
        """
        create function public.guard_archived_assessment_delete()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $$
        begin
          if old.factors ? 'phase4_snapshot' then
            raise exception 'archived assessment cannot be deleted';
          end if;
          return old;
        end;
        $$
        """
    )
    op.execute(
        "revoke all on function public.guard_archived_assessment_delete() "
        "from public, anon, authenticated"
    )
    op.execute(
        "create trigger risk_assessments_snapshot_delete_guard "
        "before delete on public.risk_assessments for each row "
        "execute function public.guard_archived_assessment_delete()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0019 is forward-only: dropping ingestion provenance or archived assessment "
        "retention would weaken historical evidence"
    )
