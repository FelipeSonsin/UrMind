"""Retain forward-only history observations in the canonical audit archive."""

from alembic import op

revision = "0021_history_snapshot_retention"
down_revision = "0020_public_image_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create function public.guard_history_snapshot_retention() returns trigger
        language plpgsql security invoker set search_path = pg_catalog as $$
        begin
            if old.operation = 'history_snapshot' then
                raise exception 'history snapshots are immutable' using errcode = '23514';
            end if;
            if tg_op = 'UPDATE' and new.operation = 'history_snapshot' then
                raise exception 'history snapshots must be inserted' using errcode = '23514';
            end if;
            if tg_op = 'DELETE' then return old; end if;
            return new;
        end $$
    """)
    op.execute("revoke all on function public.guard_history_snapshot_retention() from public")
    op.execute("""
        create trigger history_snapshot_retention before update or delete on public.audit_log
        for each row execute function public.guard_history_snapshot_retention()
    """)


def downgrade() -> None:
    # Never silently remove protection from an archive already used as evidence.
    op.execute("""
        do $$ begin
            if exists (select 1 from public.audit_log where operation = 'history_snapshot') then
                raise exception 'cannot downgrade while history snapshots exist';
            end if;
        end $$
    """)
    op.execute("drop trigger history_snapshot_retention on public.audit_log")
    op.execute("drop function public.guard_history_snapshot_retention()")
