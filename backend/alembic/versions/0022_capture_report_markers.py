"""Mobile Capture reports: description and owner-scoped Realtime, without fake Events."""

from alembic import op

revision = "0022_capture_report_markers"
down_revision = "0021_history_snapshot_retention"
branch_labels = None
depends_on = None

SQL = """
alter table public.captures add column user_description text
    constraint captures_description_length check (char_length(user_description) <= 500);
create index captures_mobile_owner_created_idx
    on public.captures ((quality->>'uploaded_by'), created_at desc)
    where source in ('pwa_photo', 'exif_upload');
create policy captures_mobile_read on public.captures for select to authenticated
using (source in ('pwa_photo', 'exif_upload') and (
    quality->>'uploaded_by' = (select auth.uid())::text or (
        coalesce(((select auth.jwt())->>'is_anonymous')::boolean, false) = false and
        ((select auth.jwt())->'app_metadata'->>'urmind_role') in ('reviewer', 'admin')
    )
));
grant select on public.captures to authenticated;
alter publication supabase_realtime add table public.captures;
create or replace trigger captures_enqueue_inference
after insert on public.captures for each row
when (new.storage_path is not null and
    (new.source not in ('pwa_photo', 'exif_upload') or new.point is not null))
execute function public.enqueue_capture_inference();
create trigger captures_location_enqueue
after update of point on public.captures
for each row when (old.point is null and new.point is not null
    and new.storage_path is not null and new.source in ('pwa_photo', 'exif_upload'))
execute function public.enqueue_capture_inference();
"""


def upgrade() -> None:
    for statement in SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists (select 1 from public.captures where user_description is not null) then
            raise exception 'cannot discard citizen descriptions';
        end if;
    end $$""")
    op.execute("drop trigger captures_location_enqueue on public.captures")
    op.execute("""create or replace trigger captures_enqueue_inference
        after insert on public.captures for each row when (new.storage_path is not null)
        execute function public.enqueue_capture_inference()""")
    op.execute("alter publication supabase_realtime drop table public.captures")
    op.execute("revoke select on public.captures from authenticated")
    op.execute("drop policy captures_mobile_read on public.captures")
    op.execute("drop index public.captures_mobile_owner_created_idx")
    op.execute("alter table public.captures drop column user_description")
