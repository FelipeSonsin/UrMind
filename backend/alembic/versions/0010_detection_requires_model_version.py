"""Require each Detection to reference a real ModelVersion.

Revision ID: 0010_detection_model_version
Revises: 0009_authenticated_schema_usage

Legacy rows lacking lineage must be quarantined manually; no model is invented.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_detection_model_version"
down_revision: str | None = "0009_authenticated_schema_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from public.detections where model_version_id is null) then
            raise exception 'detections without model_version_id require manual quarantine';
          end if;
        end
        $$;
        """
    )
    op.execute("alter table public.detections alter column model_version_id set not null")
    op.execute(
        "alter table public.detections drop constraint if exists detections_model_version_id_fkey"
    )
    op.execute(
        "alter table public.detections add constraint detections_model_version_id_fkey "
        "foreign key (model_version_id) references public.model_versions(id) on delete restrict"
    )


def downgrade() -> None:
    raise RuntimeError("0010 is forward-only: Detection without model lineage is invalid")
