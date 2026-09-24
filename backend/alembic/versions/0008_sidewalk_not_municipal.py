"""Passeio sai da competência municipal presumida (MASTER_PLAN §14.3).

A 0007 tratou `footway` e `pedestrian` como via urbana e atribuiu a competência
ao Município. Passeio não é leito carroçável: em várias cidades a conservação
recai sobre o proprietário do imóvel lindeiro — em São Paulo, a Lei municipal
15.442/2011 — e o projeto não levantou essa legislação para o piloto. Apontar o
Município nesses trechos seria apontar o responsável errado, então eles voltam a
não ter jurisdição e caem em `requires_triage`, como manda o §14.3.

Reverte apenas o que a 0007 escreveu: trecho de passeio cuja jurisdição é a
municipal presumida. Nada mais é tocado.

Revision ID: 0008_sidewalk_not_municipal
Revises: 0007_municipal_responsibility
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_sidewalk_not_municipal"
down_revision: str | None = "0007_municipal_responsibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
update public.road_segments
   set jurisdiction = null
 where jurisdiction = 'BR-via-urbana-municipal'
   and highway in ('footway', 'pedestrian');
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    raise RuntimeError(
        "0008 é forward-only: reatribuir passeio ao Município seria o erro corrigido"
    )
