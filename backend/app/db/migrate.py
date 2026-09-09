"""Acesso programático às migrations do Alembic (MASTER_PLAN §18.2).

O Alembic é a única fonte de alterações estruturais do banco. Este módulo não
reimplementa nada dele: apenas embrulha os comandos em funções assíncronas para
que testes e rotinas de operação usem o mesmo caminho que a linha de comando.

Uso equivalente no terminal:

    python -m alembic upgrade head
    python -m alembic current
    python -m alembic upgrade head --sql     # revisar sem aplicar
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from alembic import command
from app.config import ALEMBIC_INI

__all__ = ["alembic_config", "current_revision", "head_revision", "pending", "upgrade"]


def alembic_config(ini_path: Path = ALEMBIC_INI) -> Config:
    if not ini_path.exists():
        raise FileNotFoundError(f"alembic.ini não encontrado em {ini_path}")
    return Config(str(ini_path))


def head_revision(config: Config | None = None) -> str | None:
    """Revisão mais recente descrita nos arquivos de versão."""
    return ScriptDirectory.from_config(config or alembic_config()).get_current_head()


async def current_revision(database) -> str | None:
    """Revisão realmente aplicada no banco; `None` se nenhuma foi aplicada."""

    def _read(connection) -> str | None:
        return MigrationContext.configure(connection).get_current_revision()

    async with database.engine.connect() as connection:
        return await connection.run_sync(_read)


async def pending(database) -> list[str]:
    """Revisões que faltam aplicar. Lista vazia significa banco em dia."""
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    current = await current_revision(database)
    head = script.get_current_head()
    if current == head:
        return []
    return [rev.revision for rev in script.iterate_revisions(head, current) if rev.revision]


async def upgrade(revision: str = "head") -> None:
    """Aplica as migrations pendentes.

    Roda em thread separada porque o Alembic abre a própria conexão síncrona
    através do `env.py`; misturar isso com o event loop trava o processo.
    """
    await asyncio.to_thread(command.upgrade, alembic_config(), revision)
