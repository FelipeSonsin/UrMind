"""Shared, short-lived public image admission ledger (no client Data API access)."""

from alembic import op

revision = "0020_public_image_quota"
down_revision = "0019_context_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table public.public_image_admissions (
          id bigint generated always as identity primary key,
          stage text not null constraint public_image_admissions_stage_check check (stage in ('lookup', 'download')),
          caller_hash text not null constraint public_image_admissions_caller_check check (caller_hash ~ '^[0-9a-f]{64}$'),
          resource_id uuid,
          admitted_at timestamptz not null,
          constraint public_image_admissions_resource_check check ((stage = 'lookup' and resource_id is null)
              or (stage = 'download' and resource_id is not null))
        )
    """)
    op.execute(
        "create index public_image_admissions_window_idx "
        "on public.public_image_admissions (stage, admitted_at)"
    )
    op.execute("alter table public.public_image_admissions enable row level security")
    op.execute("revoke all on public.public_image_admissions from public, anon, authenticated")
    op.execute(
        "revoke all on sequence public.public_image_admissions_id_seq "
        "from public, anon, authenticated"
    )
    op.execute("grant select, insert, delete on public.public_image_admissions to service_role")
    op.execute("grant usage on sequence public.public_image_admissions_id_seq to service_role")


def downgrade() -> None:
    # Only ephemeral rate-limit state; no evidence or scientific lineage.
    op.execute("drop table public.public_image_admissions")
