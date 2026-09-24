"""Ambiente do Alembic — fonte única de alterações estruturais (MASTER_PLAN §18.2).

A URL vem do `Settings` da aplicação, que a lê do ambiente. Nenhuma credencial
entra no alembic.ini nem no repositório (§4.3, §17).

Autogenerate aqui é apenas um gerador de candidato: o resultado precisa ser lido
e corrigido à mão antes de virar revisão, porque o PostGIS cria objetos próprios
(`spatial_ref_sys`, colunas de geometria, índices GiST) que o Alembic não sabe
interpretar sozinho [R10][R43].
"""

import asyncio
import selectors
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importado pelo efeito colateral de registrar as tabelas em Base.metadata.
import app.models.core  # noqa: F401
from alembic import context
from app.config import get_settings
from app.db.base import Base
from app.db.session import connect_args, is_transaction_pooler_port, normalize_database_url

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


def _raw_database_url() -> str:
    """Migrations usam MIGRATION_DATABASE_URL (Session Pooler 5432); DATABASE_URL é
    fallback legado opcional. Nunca DATABASE_POOLER_URL."""
    settings = get_settings()
    url = settings.migration_database_url or settings.database_url
    if not url:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL não configurada. As migrations usam o Session Pooler "
            "do Supabase (5432); não existe fallback para DATABASE_POOLER_URL."
        )
    if is_transaction_pooler_port(url):
        raise RuntimeError(
            "A URL de migrations aponta para o transaction pooler (porta 6543). "
            "Migrations usam o Session Pooler (5432) ou a conexão direta (5432)."
        )
    return url


def _database_url() -> str:
    return normalize_database_url(_raw_database_url())


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
        connect_args=connect_args(_raw_database_url()),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    if sys.platform == "win32":
        # psycopg 3 async depende de add_reader/add_writer, indisponíveis no
        # ProactorEventLoop que é o padrão do asyncio no Windows.
        asyncio.run(
            run_async_migrations(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
