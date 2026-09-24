"""Serialize a caller's photo admission across API processes without long DB locks."""

from alembic import op

revision = "0026_photo_admission_leases"
down_revision = "0025_operational_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.photo_admission_leases (
        owner_id uuid primary key,
        token uuid not null,
        expires_at timestamptz not null
    )""")
    op.execute("create index photo_admission_expiry on public.photo_admission_leases(expires_at)")
    op.execute("alter table public.photo_admission_leases enable row level security")
    op.execute("revoke all on public.photo_admission_leases from public, anon, authenticated")
    op.execute(
        "grant select, insert, update, delete on public.photo_admission_leases to service_role"
    )
    op.execute("""create policy photo_admission_service on public.photo_admission_leases
        to service_role using (true) with check (true)""")
    op.execute("""do $$ begin
        if exists(select 1 from pg_roles where rolname='urmind_runtime') then
            grant select,insert,update,delete on public.photo_admission_leases to urmind_runtime;
            create policy photo_admission_runtime on public.photo_admission_leases
                to urmind_runtime using (true) with check (true);
        end if;
    end $$""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.photo_admission_leases where expires_at>clock_timestamp()) then
            raise exception 'active photo admission lease; retry downgrade after uploads finish';
        end if;
    end $$""")
    op.execute("drop table public.photo_admission_leases")
