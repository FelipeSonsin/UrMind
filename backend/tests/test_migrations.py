"""Garantias sobre as migrations do Alembic — rodam sem banco."""

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Computed

from app.config import ALEMBIC_VERSIONS_DIR
from app.db.migrate import alembic_config, head_revision
from app.models.core import Base

SQL = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted(ALEMBIC_VERSIONS_DIR.glob("*.py"))
)


def test_existe_uma_unica_head():
    """Duas heads significam histórico bifurcado e migration aplicada pela metade."""
    script = ScriptDirectory.from_config(alembic_config())
    assert len(script.get_heads()) == 1
    assert head_revision() is not None


def test_cadeia_de_revisoes_e_continua():
    script = ScriptDirectory.from_config(alembic_config())
    revisions = list(script.walk_revisions())
    assert revisions, "nenhuma revisão encontrada"
    # A revisão mais antiga é a base; nenhuma outra pode ter down_revision nulo.
    bases = [rev for rev in revisions if rev.down_revision is None]
    assert len(bases) == 1


def test_migration_de_proveniencia_nao_confunde_backfill_com_ordem_real():
    migration = (ALEMBIC_VERSIONS_DIR / "0017_assessment_snapshot_order_provenance.py").read_text(
        encoding="utf-8"
    )
    assert "set order_source = 'legacy_backfill'" in migration
    assert "set default 'persisted_at_creation'" in migration
    assert "assessment feature snapshot is immutable" in migration
    serial = (ALEMBIC_VERSIONS_DIR / "0018_serialized_order_immutable_assessment.py").read_text(
        encoding="utf-8"
    )
    assert "pg_advisory_xact_lock" in serial
    assert "set order_source = 'legacy_backfill'" in serial
    assert "snapshot assessment is immutable" in serial
    retention = (ALEMBIC_VERSIONS_DIR / "0019_context_ingestion_assessment_retention.py").read_text(
        encoding="utf-8"
    )
    assert "add column ingested_at timestamptz" in retention
    assert "update public.event_context set ingested_at" not in retention
    assert "archived assessment cannot be deleted" in retention


def test_toda_revisao_tem_downgrade():
    """Sem downgrade não há como reverter uma aplicação malsucedida (§26)."""
    for path in sorted(ALEMBIC_VERSIONS_DIR.glob("*.py")):
        conteudo = path.read_text(encoding="utf-8")
        assert "def downgrade()" in conteudo, path.name
        corpo = conteudo.split("def downgrade()", 1)[1]
        assert "pass" not in corpo.split("\n")[1:3], f"{path.name}: downgrade vazio"


def test_postgis_e_habilitado():
    assert "create extension if not exists postgis" in SQL


@pytest.mark.parametrize(
    "table",
    [
        "missions",
        "devices",
        "captures",
        "sensor_assets",
        "detections",
        "events",
        "road_segments",
        "event_context",
        "risk_assessments",
        "responsibility_rules",
        "actions_catalog",
        "predictions",
        "reviews",
        "model_versions",
        "dataset_versions",
        "audit_log",
    ],
)
def test_tabela_do_master_plan_existe(table):
    """MASTER_PLAN §5 lista os objetos lógicos obrigatórios."""
    assert f"create table if not exists public.{table} " in SQL


@pytest.mark.parametrize("table", ["captures", "events", "road_segments"])
def test_tabelas_geoespaciais_tem_indice_gist(table):
    assert f"on public.{table} using gist" in SQL


def test_rls_ligada_em_todas_as_tabelas_do_nucleo():
    for table in ("captures", "events", "detections", "road_segments", "reviews"):
        assert f"alter table public.{table} enable row level security;" in SQL


def test_realtime_autenticado_tem_apenas_acesso_de_schema_necessario():
    assert "revoke usage on schema public from anon" in SQL
    assert "grant usage on schema public to authenticated" in SQL


def test_modelos_orm_cobrem_as_tabelas_das_migrations():
    """Os modelos espelham o SQL; divergência silenciosa quebra as consultas."""
    mapped = {table.name for table in Base.metadata.tables.values()}
    for table in (
        "captures",
        "events",
        "detections",
        "road_segments",
        "missions",
        "devices",
        "audit_log",
    ):
        assert table in mapped
        assert f"create table if not exists public.{table} " in SQL


def test_road_segments_geog_e_computed_persistida():
    road_segments = next(
        table for table in Base.metadata.tables.values() if table.name == "road_segments"
    )
    geog = road_segments.c.geog
    assert isinstance(geog.computed, Computed)
    assert geog.computed.persisted is True
    assert str(geog.computed.sqltext) == "geom::geography"


def test_transicao_parte_da_revisao_remota_sem_drop_table():
    script = ScriptDirectory.from_config(alembic_config())
    revision = script.get_revision("0002_align_urmind_core")
    assert revision is not None
    assert revision.down_revision == "0001_core"
    migration_path = ALEMBIC_VERSIONS_DIR / "0002_align_urmind_core.py"
    upgrade_sql = migration_path.read_text(encoding="utf-8").split("def downgrade()", 1)[0].lower()
    assert "drop table" not in upgrade_sql
    assert "drop trigger" not in upgrade_sql


def _runtime_migration():
    import importlib.util

    path = ALEMBIC_VERSIONS_DIR / "0033_runtime_least_privilege.py"
    spec = importlib.util.spec_from_file_location("runtime_role_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _emitted_sql(module, step="upgrade"):
    import re

    statements = []

    class Collector:
        def execute(self, sql):
            statements.append(re.sub(r"\s+", " ", str(sql)).strip().lower())

    module.op = Collector()
    getattr(module, step)()
    return statements


def test_runtime_role_nao_tem_atributos_administrativos():
    module, _ = _runtime_migration()
    emitted = " ; ".join(_emitted_sql(module))
    attributes = "login noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
    assert emitted.count(attributes) == 1  # only at CREATE ROLE
    # Supabase's non-superuser `postgres` may not ALTER a role mentioning SUPERUSER,
    # REPLICATION or BYPASSRLS, even negated: attributes are verified, never altered.
    for statement in emitted.split(" ; "):
        for clause in statement.split("alter role")[1:]:
            assert not any(
                word in clause.split(";")[0]
                for word in ("superuser", "replication", "bypassrls", "createrole", "login")
            ), clause
    assert "rolsuper or rolcreatedb or rolcreaterole or rolreplication or rolbypassrls" in emitted
    assert "refusing to grant" in emitted
    assert "password" not in emitted
    assert "grant execute on function public.snap_to_road" in emitted
    assert "service_role" not in emitted and "grant postgres" not in emitted


def test_runtime_role_downgrade_nao_depende_de_drop_owned():
    module, _ = _runtime_migration()
    emitted = " ; ".join(_emitted_sql(module, "downgrade"))
    assert "drop owned" not in emitted
    assert "drop role urmind_runtime" in emitted
    for policy, table in module.CONDITIONAL_POLICIES:
        assert f"drop policy if exists {policy} on public.{table}" in emitted
    for table in module.TABLE_PRIVILEGES:
        assert f"drop policy if exists urmind_runtime_access on public.{table}" in emitted


def test_runtime_role_cobre_toda_tabela_mapeada_e_nada_administrativo():
    module, _ = _runtime_migration()
    privileges = module.TABLE_PRIVILEGES
    mapped = {table.name for table in Base.metadata.tables.values()}
    assert mapped <= set(privileges), f"tabela ORM sem grant de runtime: {mapped - set(privileges)}"
    assert "demo_events" not in privileges
    for table in ("model_versions", "dataset_versions", "alembic_version", "operational_territory"):
        assert privileges[table] == "select"
    assert "update" not in privileges["audit_log"] and "delete" not in privileges["audit_log"]
    # Consent evidence is insert-only: the app never deletes consent rows.
    assert privileges["capture_privacy_consents"] == "select, insert"
    for grant in privileges.values():
        assert not {"truncate", "references", "trigger", "all"} & set(
            grant.replace(",", " ").split()
        )


def test_demo_events_sem_acesso_cliente_no_head():
    source_0031 = (ALEMBIC_VERSIONS_DIR / "0031_demo_events.py").read_text(encoding="utf-8")
    upgrade_0031 = source_0031.split("def downgrade()", 1)[0]
    # A clean upgrade must not fail without DEMO_MODE and must not grant client reads.
    assert "raise" not in upgrade_0031
    assert upgrade_0031.index('!= "1"') < upgrade_0031.index("to anon, authenticated")
    source_0032 = (ALEMBIC_VERSIONS_DIR / "0032_demo_events_private.py").read_text(encoding="utf-8")
    upgrade_0032 = source_0032.split("def downgrade()", 1)[0]
    assert "drop policy if exists demo_events_public_read" in upgrade_0032
    assert "revoke all on public.demo_events from public, anon, authenticated" in upgrade_0032


def test_runtime_role_encontra_o_postgis_no_schema_extensions():
    import importlib.util

    path = ALEMBIC_VERSIONS_DIR / "0034_runtime_search_path.py"
    spec = importlib.util.spec_from_file_location("runtime_search_path", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    emitted = " ; ".join(_emitted_sql(module))
    assert 'alter role urmind_runtime set search_path = "$user", public, extensions' in emitted
    # Only the session default: no attribute and no privilege is touched here.
    assert "grant" not in emitted and "superuser" not in emitted
