"""Stop automatic inference jobs while captures go directly to human review."""

from alembic import op

revision = "0035_manual_capture_review"
down_revision = "0034_runtime_search_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("drop trigger if exists captures_enqueue_inference on public.captures")
    op.execute("drop trigger if exists captures_location_enqueue on public.captures")
    # Unprocessed photos with no event or review must not appear indefinitely queued.
    op.execute("""
        update public.captures c
        set quality = jsonb_set(
            coalesce(c.quality, '{}'::jsonb), '{inference}',
            jsonb_build_object(
                'status', 'needs_review',
                'reason', 'manual_review_required',
                'at', clock_timestamp()
            ), true
        )
        where c.source in ('pwa_photo', 'exif_upload')
          and c.storage_path is not null
          and (c.quality->'inference' is null
               or c.quality->'inference'->>'status' = 'queued')
          and not exists (select 1 from public.events e where e.capture_id = c.id)
          and not exists (select 1 from public.reviews r where r.capture_id = c.id)
    """)


def downgrade() -> None:
    raise RuntimeError("0035 is forward-only: re-enabling automatic inference requires a new plan")
