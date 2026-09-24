"""Versioned photo/location consent, isolated by authenticated visitor."""

from alembic import op

revision = "0027_capture_privacy_consent"
down_revision = "0026_photo_admission_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create table public.capture_privacy_consents (
        owner_id uuid not null,
        notice_version text not null,
        accepted_at timestamptz not null default clock_timestamp(),
        primary key(owner_id,notice_version)
    )""")
    op.execute("alter table public.capture_privacy_consents enable row level security")
    op.execute("revoke all on public.capture_privacy_consents from public,anon,authenticated")
    op.execute("grant select on public.capture_privacy_consents to authenticated")
    op.execute("""create policy consent_owner_read on public.capture_privacy_consents
        for select to authenticated using (owner_id=auth.uid())""")
    op.execute("grant select,insert,delete on public.capture_privacy_consents to service_role")
    op.execute("""create policy consent_service on public.capture_privacy_consents
        to service_role using (true) with check (true)""")
    op.execute("""do $$ begin
        if exists(select 1 from pg_roles where rolname='urmind_runtime') then
            grant select,insert,delete on public.capture_privacy_consents to urmind_runtime;
            create policy consent_runtime on public.capture_privacy_consents
                to urmind_runtime using (true) with check (true);
        end if;
    end $$""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.capture_privacy_consents) then
            raise exception 'privacy consent records exist; preserve evidence before downgrade';
        end if;
    end $$""")
    op.execute("drop table public.capture_privacy_consents")
