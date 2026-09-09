from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_URL ausente: a camada de persistência direta está desligada."""


def normalize_database_url(url: str) -> str:
    """Garante o driver async psycopg 3 escolhido no MASTER_PLAN §3."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


class Database:
    """Engine + sessionmaker do PostgreSQL/PostGIS do Supabase."""

    def __init__(self, settings: Settings) -> None:
        if not settings.database_url:
            raise DatabaseNotConfiguredError(
                "DATABASE_URL não configurada; defina a connection string do Supabase."
            )
        self.engine: AsyncEngine = create_async_engine(
            normalize_database_url(settings.database_url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=settings.db_echo,
        )
        self.sessionmaker = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )

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
