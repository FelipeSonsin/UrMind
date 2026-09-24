"""Dados-base versionados de ação e competência (MASTER_PLAN §14.3, §14.4).

Revision ID: 0004_decision_reference_data
Revises: 0003_storage_captures_bucket
Create Date: 2026-09-16

Só entra regra com fundamento verificável. Rodovia federal tem competência
definida em lei federal; via municipal depende de legislação local que o projeto
ainda não levantou para o piloto, então não recebe regra e o evento fica
`requires_triage` — que é o comportamento pedido pelo §14.3.

Idempotente por chave natural (`code`; jurisdiction+asset+classe+versão).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_decision_reference_data"
down_revision: str | None = "0003_storage_captures_bucket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SQL = r"""
insert into public.actions_catalog (code, label, description, version) values
    ('inspecao_tecnica', 'Inspeção técnica',
     'Vistoria por equipe do órgão responsável para confirmar o defeito e definir a intervenção.', 'v1'),
    ('sinalizacao_temporaria', 'Sinalização temporária',
     'Isolamento ou sinalização provisória do ponto até a intervenção definitiva.', 'v1'),
    ('reparo_pavimento', 'Reparo do pavimento',
     'Intervenção corretiva no pavimento, definida pelo órgão após inspeção.', 'v1')
on conflict (code) do nothing;

insert into public.responsibility_rules (jurisdiction, asset_type, urmind_class, responsible, source, version)
select 'BR-rodovia-federal', 'pavimento', c,
       'DNIT — Departamento Nacional de Infraestrutura de Transportes',
       'Lei 10.233/2001, art. 82, IV. Trecho concedido cabe à concessionária regulada pela ANTT: confirmar concessão antes de encaminhar.',
       'v1'
from unnest(array['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40']) as c
on conflict (jurisdiction, asset_type, urmind_class, version) do nothing;
"""


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        context.impl.static_output(SQL)
        return
    cursor = op.get_bind().connection.dbapi_connection.cursor()
    try:
        cursor.execute(SQL)
    finally:
        cursor.close()


def downgrade() -> None:
    raise RuntimeError("0004 é forward-only: regras podem estar referenciadas por avaliações")
