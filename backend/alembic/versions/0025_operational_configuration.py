"""Audited runtime photo-admission policy, not credentials or Auth configuration."""

from alembic import op

revision = "0025_operational_configuration"
down_revision = "0024_capture_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.operational_configuration (
        key text primary key check (key='photo_gate'),
        payload jsonb not null check (jsonb_typeof(payload)='object'),
        updated_at timestamptz not null default now()
    )""")
    op.execute("alter table public.operational_configuration enable row level security")
    op.execute("revoke all on public.operational_configuration from public, anon, authenticated")
    op.execute("grant select, insert, update on public.operational_configuration to service_role")
    op.execute("""do $$ begin
        if exists(select 1 from pg_roles where rolname='urmind_runtime') then
            grant select, insert, update on public.operational_configuration to urmind_runtime;
            create policy operational_runtime_role on public.operational_configuration
                to urmind_runtime using (true) with check (true);
        end if;
    end $$""")
    # Runtime is separately restricted by API role checks and this table policy.
    op.execute("""create policy operational_runtime on public.operational_configuration
        to service_role using (true) with check (true)""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.operational_configuration) then
            raise exception 'cannot discard saved operational configuration';
        end if;
    end $$""")
    op.execute("drop table public.operational_configuration")
