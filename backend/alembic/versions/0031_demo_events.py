"""Demo-only geospatial events for the Streamlit presentation.

The public read policy below is scoped to this disposable demo table. It must
not be copied to production tables or deployed as a production access policy.

This revision was applied to Urmind DEV with DEMO_MODE=1. The table DDL always
runs so a clean `alembic upgrade head` reaches the same schema; only the client
grant and public policy stay behind DEMO_MODE=1, and 0032 revokes them anyway.
"""

import os

from alembic import op

revision = "0031_demo_events"
down_revision = "0030_brazil_territory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create schema if not exists extensions")
    op.execute("create extension if not exists postgis with schema extensions")
    op.execute("""create table if not exists public.demo_events (
        id uuid primary key,
        image_path text not null,
        geom extensions.geometry(Point, 4326) not null,
        geo_source text not null,
        defect_class text not null,
        bbox jsonb not null default '[]'::jsonb,
        risk_score double precision not null check (risk_score between 0 and 1),
        risk_calibration text not null,
        upde_decision text not null,
        detection_source text not null,
        created_at timestamptz not null
    )""")
    op.execute(
        "create index if not exists demo_events_geom_gix on public.demo_events using gist (geom)"
    )
    op.execute(
        "create index if not exists demo_events_created_at_idx on public.demo_events (created_at)"
    )
    op.execute("alter table public.demo_events enable row level security")
    op.execute("revoke all on public.demo_events from public, anon, authenticated")
    op.execute("grant select, insert, update, delete on public.demo_events to service_role")
    op.execute("""comment on table public.demo_events is
        'DEMO ONLY: synthetic locations/times; public read policy must not go to production.'""")
    if os.getenv("DEMO_MODE") != "1":
        # Clean upgrades get the table without client access, as 0032 leaves it.
        return
    op.execute("grant select on public.demo_events to anon, authenticated")
    # DEMO SCOPE ONLY: anonymous/public reads of demo_events; never use in production.
    op.execute("""do $$ begin
        if not exists (
            select 1 from pg_policies
            where schemaname = 'public' and tablename = 'demo_events'
              and policyname = 'demo_events_public_read'
        ) then
            create policy demo_events_public_read on public.demo_events
                for select to anon, authenticated using (true);
        end if;
    end $$""")


def downgrade() -> None:
    op.execute("drop table if exists public.demo_events")
