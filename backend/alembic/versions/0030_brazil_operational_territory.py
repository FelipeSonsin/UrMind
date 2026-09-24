"""Private, versioned IBGE geometry for operational Brazil admission."""

from alembic import op

revision = "0030_brazil_territory"
down_revision = "0029_geocoding_coordination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.operational_territory (
        code text primary key check (code='BR'),
        source_url text not null,
        source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
        imported_at timestamptz not null default now(),
        geom geometry(MultiPolygon,4326) not null
    )""")
    op.execute("alter table public.operational_territory enable row level security")
    op.execute("revoke all on public.operational_territory from public,anon,authenticated")
    op.execute("grant select on public.operational_territory to service_role")
    op.execute("""create policy territory_service on public.operational_territory
        for select to service_role using (true)""")
    op.execute("""do $$ begin
        if exists(select 1 from pg_roles where rolname='urmind_runtime') then
            grant select on public.operational_territory to urmind_runtime;
            create policy territory_runtime on public.operational_territory
                for select to urmind_runtime using (true);
        end if;
    end $$""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.operational_territory) then
            raise exception 'remove the installed operational territory explicitly before downgrade';
        end if;
    end $$""")
    op.execute("drop table public.operational_territory")
