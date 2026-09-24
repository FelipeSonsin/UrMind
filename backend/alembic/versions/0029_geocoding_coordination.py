"""Shared reverse-geocoding cache and global request lease; no public access."""

from alembic import op

revision = "0029_geocoding_coordination"
down_revision = "0028_report_evidence_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.geocoding_cache (
        cache_key text primary key, payload jsonb not null, expires_at timestamptz not null
    )""")
    op.execute("""create table public.geocoding_leases (
        provider text primary key, token uuid not null, expires_at timestamptz not null
    )""")
    for table in ("geocoding_cache", "geocoding_leases"):
        op.execute(f"alter table public.{table} enable row level security")
        op.execute(f"revoke all on public.{table} from public,anon,authenticated")
        op.execute(f"grant select,insert,update,delete on public.{table} to service_role")
        op.execute(
            f"create policy geocoding_service on public.{table} to service_role using (true) with check (true)"
        )
        op.execute(f"""do $$ begin
            if exists(select 1 from pg_roles where rolname='urmind_runtime') then
                grant select,insert,update,delete on public.{table} to urmind_runtime;
                create policy geocoding_runtime on public.{table} to urmind_runtime using (true) with check (true);
            end if;
        end $$""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.geocoding_leases where expires_at>clock_timestamp()) then
            raise exception 'active geocoding lease; stop API/Worker and wait before downgrade';
        end if;
    end $$""")
    op.execute("drop table public.geocoding_cache")
    op.execute("drop table public.geocoding_leases")
