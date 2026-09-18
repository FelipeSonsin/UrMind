"""Competência municipal sobre via urbana (MASTER_PLAN §14.3).

A 0004 deixou a via municipal sem regra por falta de fundamento levantado. O
fundamento existe e é federal: o CTB (Lei 9.503/1997) atribui aos órgãos
executivos de trânsito dos Municípios a circunscrição sobre a via urbana, e o
art. 1º, §§2º e 3º responsabiliza os órgãos do Sistema Nacional de Trânsito pela
omissão na manutenção. Quando `ref` identifica rodovia federal (BR-) ou estadual
(UF-), a competência continua sendo daquele ente e a regra municipal não se
aplica.

A regra nomeia o ente ("Município"), não o órgão: qual secretaria ou
subprefeitura executa varia por cidade, e inventar o nome seria fabricar
precisão. O texto pede confirmação antes do encaminhamento.

O backfill reclassifica apenas trecho com `jurisdiction` nula, a partir das tags
do OSM já gravadas em `attributes->osm_tags` — nada é buscado na rede e nenhuma
geometria é tocada. Idempotente: rodar de novo não muda linha nenhuma.

Revision ID: 0007_municipal_responsibility
Revises: 0006_realtime_events
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_municipal_responsibility"
down_revision: str | None = "0006_realtime_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mantido em sincronia com app.services.osm_import.URBAN_HIGHWAYS.
URBAN_HIGHWAYS = (
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "pedestrian",
    "footway",
)
HIGHWAY_LIST = ", ".join(f"'{value}'" for value in URBAN_HIGHWAYS)

SQL = rf"""
insert into public.responsibility_rules (jurisdiction, asset_type, urmind_class, responsible, source, version)
select 'BR-via-urbana-municipal', 'pavimento', c,
       'Município — órgão executivo de trânsito e de zeladoria viária',
       'CTB (Lei 9.503/1997), art. 24 e art. 1º, §§ 2º e 3º: via urbana sem rodovia federal ou estadual identificada está na circunscrição do Município. Confirmar o órgão executor local antes de encaminhar.',
       'v1'
from unnest(array['URMIND_ROAD_D00', 'URMIND_ROAD_D10', 'URMIND_ROAD_D20', 'URMIND_ROAD_D40']) as c
on conflict (jurisdiction, asset_type, urmind_class, version) do nothing;

update public.road_segments
   set jurisdiction = 'BR-via-urbana-municipal'
 where jurisdiction is null
   and highway in ({HIGHWAY_LIST})
   and coalesce(attributes->'osm_tags'->>'ref', '') !~* '(^|;)\s*(BR|AC|AL|AM|AP|BA|CE|DF|ES|GO|MA|MG|MS|MT|PA|PB|PE|PI|PR|RJ|RN|RO|RR|RS|SC|SE|SP|TO)-';
"""


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        context.impl.static_output(SQL)
        return
    # psycopg trata `%` do regex como placeholder quando a query passa pelo
    # SQLAlchemy; o cursor DBAPI executa o texto literal.
    cursor = op.get_bind().connection.dbapi_connection.cursor()
    try:
        cursor.execute(SQL)
    finally:
        cursor.close()


def downgrade() -> None:
    raise RuntimeError(
        "0007 é forward-only: a regra pode estar referenciada por avaliações de risco"
    )
