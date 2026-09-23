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
