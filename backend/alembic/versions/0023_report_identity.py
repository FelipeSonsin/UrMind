"""Independent public identities and owner-facing report protocols."""

from alembic import op

revision = "0023_report_identity"
down_revision = "0022_capture_report_markers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""alter table public.captures
        add column public_id text unique,
        add column protocol_code text unique""")
    op.execute("""create function public.assign_report_identity() returns trigger
    language plpgsql security invoker set search_path = pg_catalog, public as $$
    declare candidate text; entropy bytea; i integer; attempt integer;
    begin
        for attempt in 1..100 loop
            candidate := replace(gen_random_uuid()::text, '-', '');
            perform pg_advisory_xact_lock(hashtextextended('report-public:' || candidate, 0));
            exit when not exists(select 1 from public.captures where public_id=candidate);
            if attempt=100 then raise exception 'report identity exhausted'; end if;
        end loop;
        new.public_id := candidate;
        for attempt in 1..100 loop
            entropy := decode(replace(gen_random_uuid()::text, '-', ''), 'hex');
            candidate := 'URM-';
            for i in 0..7 loop
                candidate := candidate || substr('23456789ABCDEFGHJKLMNPQRSTUVWXYZ',
                    (get_byte(entropy, i) % 32) + 1, 1);
            end loop;
            perform pg_advisory_xact_lock(hashtextextended('report-protocol:' || candidate, 0));
            exit when not exists(select 1 from public.captures where protocol_code=candidate);
            if attempt=100 then raise exception 'report protocol exhausted'; end if;
        end loop;
        new.protocol_code := candidate;
        return new;
    end $$""")
    op.execute("revoke all on function public.assign_report_identity() from public")
    op.execute("""create trigger captures_assign_identity before insert or update of public_id
        on public.captures for each row when (new.public_id is null)
        execute function public.assign_report_identity()""")
    op.execute("update public.captures set public_id=null where public_id is null")
    op.execute("""alter table public.captures
        alter column public_id set not null,
        alter column protocol_code set not null,
        add constraint captures_public_id_format check (public_id ~ '^[a-f0-9]{32}$'),
        add constraint captures_protocol_format check (protocol_code ~ '^URM-[2-9A-HJ-NP-Z]{8}$')""")
    # Events can be produced by older/non-mobile sources without a Capture.
    op.execute("""alter table public.events add column public_id text not null
        default replace(gen_random_uuid()::text, '-', '') unique""")


def downgrade() -> None:
    # Issued identities must never silently disappear from existing user links.
    op.execute("""do $$ begin
        if exists(select 1 from public.captures) or exists(select 1 from public.events) then
            raise exception 'cannot discard issued report identities';
        end if;
    end $$""")
    op.execute("alter table public.events drop column public_id")
    op.execute("drop trigger captures_assign_identity on public.captures")
    op.execute("drop function public.assign_report_identity()")
    op.execute("alter table public.captures drop column protocol_code, drop column public_id")
