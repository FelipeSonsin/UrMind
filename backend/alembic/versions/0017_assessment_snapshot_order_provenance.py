"""Mark preexisting order uncertain and guard assessment snapshots.

Revision ID: 0017_snapshot_order
Revises: 0016_assessment_order

Identity values assigned by earlier migrations to existing rows are not
historical insertion evidence. Only rows inserted after this migration get
persisted_at_creation by default. No timestamps or prior order are invented.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_snapshot_order"
down_revision: str | None = "0016_assessment_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("events", "reviews", "risk_assessments"):
        op.execute(f"alter table public.{table} add column order_source text")
        op.execute(f"update public.{table} set order_source = 'legacy_backfill'")
        op.execute(
            f"alter table public.{table} alter column order_source "
            "set default 'persisted_at_creation'"
        )
        op.execute(f"alter table public.{table} alter column order_source set not null")
        op.execute(
            f"alter table public.{table} add constraint {table}_order_source_check "
            "check (order_source in ('persisted_at_creation', 'legacy_backfill'))"
        )
    op.execute(
        "alter table public.risk_assessments alter column assessment_sequence set generated always"
    )
    op.execute(
        """
        create function public.guard_order_and_assessment_snapshot()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $$
        begin
          if new.order_source is distinct from old.order_source then
            raise exception 'order provenance is immutable';
          end if;
          if tg_table_name = 'risk_assessments' then
            if old.factors ? 'phase4_snapshot'
               and new.factors -> 'phase4_snapshot'
                   is distinct from old.factors -> 'phase4_snapshot' then
              raise exception 'assessment feature snapshot is immutable';
            end if;
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "revoke all on function public.guard_order_and_assessment_snapshot() "
        "from public, anon, authenticated"
    )
    for table in ("events", "reviews", "risk_assessments"):
        op.execute(
            f"create trigger {table}_provenance_guard before update on public.{table} "
            "for each row execute function public.guard_order_and_assessment_snapshot()"
        )


def downgrade() -> None:
    raise RuntimeError(
        "0017 is forward-only: dropping order provenance and immutable snapshots "
        "would silently weaken historical assessments"
    )
