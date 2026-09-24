"""Marco histórico do schema legado já aplicado no Supabase.

Revision ID: 0001_core
Revises:

O DDL original não existe neste repositório. Recriá-lo a partir do estado atual
seria reescrever o histórico. A revisão seguinte cria ou alinha todo o schema
canônico; o banco existente já registra este marco em ``alembic_version``.
"""

from collections.abc import Sequence

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Não reexecuta nem inventa o DDL histórico ausente."""


def downgrade() -> None:
    raise RuntimeError("0001_core é o marco histórico mínimo e não pode ser revertido")
