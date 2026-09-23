"""Toda Detection deve apontar para um ModelVersion existente.

Revision ID: 0010_detection_requires_model_version
Revises: 0009_authenticated_schema_usage
Create Date: 2026-09-22

A migration falha antes de alterar o schema se houver legado sem lineage. Isso
evita atribuir artificialmente detecções antigas a um modelo qualquer.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_detection_requires_model_version"
down_revision: str | None = "0009_authenticated_schema_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from public.detections where model_version_id is null) then
            raise exception 'detections sem model_version_id exigem quarentena manual';
          end if;
        end
        $$;
        """
    )
    op.execute(
        "alter table public.detections alter column model_version_id set not null"
    )
    op.execute(
        "alter table public.detections drop constraint if exists detections_model_version_id_fkey"
    )
    op.execute(
        "alter table public.detections add constraint detections_model_version_id_fkey "
        "foreign key (model_version_id) references public.model_versions(id) on delete restrict"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0010 é forward-only: Detection sem lineage de modelo viola o gate científico"
    )
