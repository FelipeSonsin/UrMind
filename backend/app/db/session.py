from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_POOLER_URL ausente: a persistência do runtime está desligada."""


#: sslmode que de fato exigem TLS. `prefer`/`allow` aceitam cair para texto claro.
SECURE_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})


def normalize_database_url(url: str) -> str:
    """Garante o driver async psycopg 3 escolhido no MASTER_PLAN §3."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


def _is_supabase_host(host: str | None) -> bool:
    return host is not None and host.endswith((".supabase.co", ".supabase.com"))


def connect_args(url: str) -> dict[str, Any]:
    """Argumentos do psycopg 3 para a conexão. Nunca inclui a URL em mensagem.

    Runtime e migrations usam o Supavisor em session mode (5432), que mantém uma
    conexão de servidor por cliente: prepared statements do psycopg funcionam e
    não há ajuste de pooler aqui. Host do Supabase exige TLS: sem `sslmode` na
    URL, aplica `require`; um `sslmode` explícito que admite texto claro é
    recusado. Host local fica com o padrão do libpq.
    """
    parts = urlsplit(url)
    args: dict[str, Any] = {}
    if _is_supabase_host(parts.hostname):
        explicit = parse_qs(parts.query).get("sslmode", [None])[-1]
        if explicit is None:
            args["sslmode"] = "require"
        elif explicit not in SECURE_SSLMODES:
            raise ValueError(
                "sslmode inseguro para host do Supabase; use require, verify-ca ou verify-full"
            )
    return args


def is_transaction_pooler_port(url: str) -> bool:
    """Porta 6543 é o transaction pooler do Supabase, fora da arquitetura oficial."""
    return urlsplit(url).port == 6543


class Database:
    """Engine + sessionmaker do runtime, sobre o Supavisor em session mode (5432).

    O runtime usa DATABASE_POOLER_URL e nada mais: sem ela a persistência fica
    desligada e a API responde 503 (§26), em vez de cair em silêncio para outra
    conexão.
    """

    def __init__(
        self, settings: Settings, *, role: Literal["runtime", "admin"] = "runtime"
    ) -> None:
        # runtime = API/Worker (least-privilege `urmind_runtime`); admin = model
        # registration/promotion and maintenance CLIs, on the migration identity.
        # Never falls back from one to the other.
        variable = "DATABASE_POOLER_URL" if role == "runtime" else "MIGRATION_DATABASE_URL"
        url = settings.database_pooler_url if role == "runtime" else settings.migration_database_url
        if not url:
            raise DatabaseNotConfiguredError(
                f"{variable} não configurada; use o Session Pooler do Supabase (porta 5432)."
            )
        if is_transaction_pooler_port(url):
            # Transaction mode não preserva prepared statements entre transações.
            raise DatabaseNotConfiguredError(
                f"{variable} aponta para o transaction pooler (porta 6543); "
                "use o Session Pooler do Supabase (porta 5432)."
            )
        self.role = role
        self.engine: AsyncEngine = create_async_engine(
            normalize_database_url(url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=settings.db_echo,
            connect_args=connect_args(url),
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Unidade de trabalho: commit no sucesso, rollback em qualquer erro."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def health(self) -> dict[str, str]:
        async with self.engine.connect() as conn:
            postgis = await conn.scalar(text("select postgis_version()"))
            return {"database": "connected", "postgis": str(postgis)}

    async def close(self) -> None:
        await self.engine.dispose()


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database(get_settings())
    return _database
