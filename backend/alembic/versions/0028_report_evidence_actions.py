"""Record evidence detachment as operational review, never a Ground Truth vote."""

from alembic import op

revision = "0028_report_evidence_actions"
down_revision = "0027_capture_privacy_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.reviews drop constraint reviews_decision_check")
    op.execute("""alter table public.reviews add constraint reviews_decision_check
        check (decision in ('confirm','correct','reject','detach_evidence'))""")
    op.execute("""alter table public.reviews add constraint evidence_review_capture_only
        check (decision <> 'detach_evidence' or (capture_id is not null and event_id is null))""")
    op.execute("""create index captures_additional_evidence_idx
        on public.captures ((quality->'additional_evidence'->>'capture_id'))
        where quality ? 'additional_evidence'""")


def downgrade() -> None:
    op.execute("""do $$ begin
        if exists(select 1 from public.reviews where decision='detach_evidence') then
            raise exception 'operational review evidence exists; downgrade would lose semantics';
        end if;
    end $$""")
    op.execute("drop index public.captures_additional_evidence_idx")
    op.execute("alter table public.reviews drop constraint evidence_review_capture_only")
    op.execute("alter table public.reviews drop constraint reviews_decision_check")
    op.execute("""alter table public.reviews add constraint reviews_decision_check
        check (decision in ('confirm','correct','reject'))""")
