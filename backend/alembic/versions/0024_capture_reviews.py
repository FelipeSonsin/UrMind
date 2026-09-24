"""Reuse Review for citizen reports that have no machine Event."""

from alembic import op

revision = "0024_capture_reviews"
down_revision = "0023_report_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""alter table public.reviews
        alter column event_id drop not null,
        add column capture_id uuid references public.captures(id) on delete restrict,
        add constraint reviews_subject_required check (event_id is not null or capture_id is not null)""")
    op.execute("create index reviews_capture_idx on public.reviews(capture_id, review_sequence)")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.reviews where capture_id is not null) then
            raise exception 'cannot discard citizen review lineage';
        end if;
    end $$""")
    op.execute("alter table public.reviews drop constraint reviews_subject_required")
    op.execute(
        "alter table public.reviews drop column capture_id, alter column event_id set not null"
    )
