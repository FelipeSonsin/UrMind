"""Ambiente do Alembic — fonte única de alterações estruturais (MASTER_PLAN §18.2).

A URL vem do `Settings` da aplicação, que a lê do ambiente. Nenhuma credencial
entra no alembic.ini nem no repositório (§4.3, §17).

Autogenerate aqui é apenas um gerador de candidato: o resultado precisa ser lido
e corrigido à mão antes de virar revisão, porque o PostGIS cria objetos próprios
(`spatial_ref_sys`, colunas de geometria, índices GiST) que o Alembic não sabe
interpretar sozinho [R10][R43].
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importado pelo efeito colateral de registrar as tabelas em Base.metadata.
import app.models.core  # noqa: F401
from alembic import context
from app.config import get_settings
from app.db.base import Base
from app.db.session import normalize_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Objetos que pertencem às extensões, não ao esquema do UrMind. Sem isso o
# autogenerate propõe apagar tabelas do PostGIS a cada execução.
EXCLUDED_TABLES = {"spatial_ref_sys", "geography_columns", "geometry_columns"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in EXCLUDED_TABLES:
        return False
    # Índices espaciais são criados junto da coluna; o Alembic os veria como órfãos.
    return not (type_ == "index" and name and name.endswith("_gix"))


def _database_url() -> str:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL não configurada. As migrations exigem o PostgreSQL do "
            "Supabase; não existe fallback local (MASTER_PLAN §18.2)."
        )
    return normalize_database_url(settings.database_url)


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar — útil para revisar o que será aplicado."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
