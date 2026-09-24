"""Serialize new record order through commit and freeze snapshot assessments.

Revision ID: 0018_serialized_order
Revises: 0017_snapshot_order

Identity values are allocated before commit. A transaction-scoped advisory lock
held until commit makes the separate commit_order tokens safe for chronology.
All existing rows remain legacy because their old identity order cannot prove it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_serialized_order"
down_revision: str | None = "0017_snapshot_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("events", "reviews", "risk_assessments")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"drop trigger {table}_provenance_guard on public.{table}")
    op.execute("drop function public.guard_order_and_assessment_snapshot()")
    for table in _TABLES:
        op.execute(f"alter table public.{table} drop constraint {table}_order_source_check")
        op.execute(f"update public.{table} set order_source = 'legacy_backfill'")
        op.execute(
            f"alter table public.{table} alter column order_source "
            "set default 'serialized_commit_order'"
        )
        op.execute(
            f"alter table public.{table} add constraint {table}_order_source_check "
            "check (order_source in ('serialized_commit_order', 'legacy_backfill'))"
        )
        op.execute(f"alter table public.{table} add column commit_order bigint")
        op.execute(f"create sequence public.{table}_commit_order_seq as bigint")
        op.execute(
            f"alter sequence public.{table}_commit_order_seq owned by public.{table}.commit_order"
        )
        op.execute(
            f"revoke all on sequence public.{table}_commit_order_seq from anon, authenticated"
        )
        op.execute(
            f"create unique index {table}_commit_order_idx "
            f"on public.{table} (commit_order) where commit_order is not null"
        )
    op.execute(
        "create index events_segment_commit_order_idx "
        "on public.events (road_segment_id, commit_order) "
        "where commit_order is not null"
    )
    op.execute(
        """
        create function public.assign_serialized_order()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $$
        begin
          if new.order_source = 'legacy_backfill' then
            new.commit_order := null;
            return new;
          end if;
          if tg_table_name = 'events' then
            perform pg_advisory_xact_lock(52135001);
            new.commit_order := nextval('public.events_commit_order_seq');
          elsif tg_table_name = 'reviews' then
            perform pg_advisory_xact_lock(52135002);
            new.commit_order := nextval('public.reviews_commit_order_seq');
          else
            perform pg_advisory_xact_lock(52135003);
            new.commit_order := nextval('public.risk_assessments_commit_order_seq');
          end if;
          new.order_source := 'serialized_commit_order';
          return new;
        end;
        $$
        """
    )
    op.execute(
        """
        create function public.guard_order_and_assessment_snapshot()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $$
        begin
          if new.order_source is distinct from old.order_source
             or new.commit_order is distinct from old.commit_order then
            raise exception 'order provenance is immutable';
          end if;
          if tg_table_name = 'risk_assessments' then
            if old.factors ? 'phase4_snapshot' or new.factors ? 'phase4_snapshot' then
              raise exception 'snapshot assessment is immutable';
            end if;
          end if;
          return new;
        end;
        $$
        """
    )
    for function in ("assign_serialized_order", "guard_order_and_assessment_snapshot"):
        op.execute(f"revoke all on function public.{function}() from public, anon, authenticated")
    for table in _TABLES:
        op.execute(
            f"create trigger {table}_serialized_order before insert on public.{table} "
            "for each row execute function public.assign_serialized_order()"
        )
        op.execute(
            f"create trigger {table}_provenance_guard before update on public.{table} "
            "for each row execute function public.guard_order_and_assessment_snapshot()"
        )


def downgrade() -> None:
    raise RuntimeError(
        "0018 is forward-only: dropping commit-safe order or assessment immutability "
        "would weaken historical evidence"
    )
