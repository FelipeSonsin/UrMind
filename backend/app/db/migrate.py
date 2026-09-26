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


# --- Least-privilege runtime role (0033_runtime_least_privilege) -------------------

RUNTIME_ROLE = "urmind_runtime"


def scram_sha256_verifier(
    password: str, *, iterations: int = 4096, salt: bytes | None = None
) -> str:
    """PostgreSQL SCRAM-SHA-256 verifier computed locally (RFC 5802/7677).

    Sending the verifier instead of the plaintext keeps the password out of the
    SQL text, pg_stat_statements and server logs.
    """
    import base64
    import hashlib
    import hmac
    import os

    salt = salt if salt is not None else os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def runtime_pooler_url(migration_url: str, project_ref: str, password: str) -> str:
    """Same Supavisor host/port/options as the migration URL, runtime role credentials."""
    from urllib.parse import quote, urlsplit, urlunsplit

    parts = urlsplit(migration_url)
    if parts.hostname is None:
        raise ValueError("MIGRATION_DATABASE_URL sem host")
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    user = f"{RUNTIME_ROLE}.{project_ref}"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def replace_env_value(text: str, key: str, value: str) -> str:
    """Replace exactly one `KEY=...` line, keeping every other line untouched."""
    lines = text.splitlines(keepends=True)
    matches = [
        i for i, line in enumerate(lines) if line.lstrip("\ufeff").split("=", 1)[0].strip() == key
    ]
    if len(matches) != 1:
        raise ValueError(f"{key} deve aparecer exatamente uma vez no arquivo env")
    index = matches[0]
    ending = "\r\n" if lines[index].endswith("\r\n") else "\n"
    bom = "\ufeff" if lines[index].startswith("\ufeff") else ""
    lines[index] = f"{bom}{key}='{value}'{ending}"
    return "".join(lines)


def _read_env(env_file: Path) -> str:
    # Bytes, not read_text: universal newlines would silently turn CRLF into LF.
    if not env_file.is_file():
        raise RuntimeError(f"arquivo env ausente: {env_file.name}")
    return env_file.read_bytes().decode("utf-8")


def _write_env_atomically(env_file: Path, text: str) -> None:
    import os

    partial = env_file.with_name(env_file.name + ".tmp")
    partial.write_bytes(text.encode("utf-8"))
    os.replace(partial, env_file)


def _admin_connection_url() -> tuple[str, str]:
    from urllib.parse import urlsplit

    from app.config import URMIND_DEV_SHADOW_REF, get_settings

    migration_url = get_settings().migration_database_url
    if not migration_url:
        raise RuntimeError("MIGRATION_DATABASE_URL não configurada")
    if urlsplit(migration_url).username != f"postgres.{URMIND_DEV_SHADOW_REF}":
        raise RuntimeError("identidade admin do Urmind DEV não confirmada na URL de migration")
    return migration_url, "postgresql://" + migration_url.split("://", 1)[1]


def set_runtime_password(env_file: Path) -> str:
    """Rotate the runtime password and point DATABASE_POOLER_URL at the runtime role.

    Everything that can fail is checked before the password changes: env file and
    its single DATABASE_POOLER_URL line, DEV admin identity, role present without
    elevated attributes, runtime URL buildable. Then the SCRAM verifier is set and
    the env file is replaced atomically. Recovery: `runtime-password --rollback`
    points the runtime back at the admin identity. Returns the runtime username.
    """
    import secrets

    import psycopg
    from psycopg import sql

    from app.config import URMIND_DEV_SHADOW_REF
    from app.db.session import connect_args

    original = _read_env(env_file)
    replace_env_value(original, "DATABASE_POOLER_URL", "validation-only")
    migration_url, plain_url = _admin_connection_url()
    password = secrets.token_urlsafe(32)
    url = runtime_pooler_url(migration_url, URMIND_DEV_SHADOW_REF, password)
    updated = replace_env_value(original, "DATABASE_POOLER_URL", url)
    verifier = scram_sha256_verifier(password)
    with psycopg.connect(plain_url, **connect_args(plain_url)) as connection:
        role = connection.execute(
            "select rolsuper or rolcreatedb or rolcreaterole or rolreplication or rolbypassrls,"
            " rolcanlogin from pg_roles where rolname = %s",
            (RUNTIME_ROLE,),
        ).fetchone()
        if role is None:
            raise RuntimeError("role urmind_runtime ausente: aplique 0033 antes")
        elevated, can_login = role
        if elevated or not can_login:
            raise RuntimeError("urmind_runtime com atributos inesperados; senha não alterada")
        connection.execute(
            sql.SQL("alter role {} with password {}").format(
                sql.Identifier(RUNTIME_ROLE), sql.Literal(verifier)
            )
        )
    _write_env_atomically(env_file, updated)
    return f"{RUNTIME_ROLE}.{URMIND_DEV_SHADOW_REF}"


def rollback_runtime_url(env_file: Path) -> str:
    """Recovery path: point DATABASE_POOLER_URL back at the admin identity."""
    original = _read_env(env_file)
    migration_url, _ = _admin_connection_url()
    _write_env_atomically(
        env_file, replace_env_value(original, "DATABASE_POOLER_URL", migration_url)
    )
    from urllib.parse import urlsplit

    return urlsplit(migration_url).username or ""


def verify_runtime_login() -> str:
    """Log in with DATABASE_POOLER_URL and return `current_user` (no secret shown)."""
    import psycopg

    from app.config import get_settings
    from app.db.session import connect_args

    url = get_settings().database_pooler_url
    if not url:
        raise RuntimeError("DATABASE_POOLER_URL não configurada")
    plain_url = "postgresql://" + url.split("://", 1)[1]
    with psycopg.connect(plain_url, **connect_args(plain_url)) as connection:
        row = connection.execute("select current_user").fetchone()
    return str(row[0]) if row else ""


def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.config import BACKEND_DIR

    parser = argparse.ArgumentParser(prog="python -m app.db.migrate")
    sub = parser.add_subparsers(dest="command", required=True)
    rotate = sub.add_parser(
        "runtime-password",
        help="rotaciona a senha de urmind_runtime e aponta DATABASE_POOLER_URL para ela",
    )
    rotate.add_argument("--env-file", type=Path, default=BACKEND_DIR / ".env")
    rotate.add_argument(
        "--rollback",
        action="store_true",
        help="recuperação: aponta DATABASE_POOLER_URL de volta para a identidade admin",
    )
    sub.add_parser("verify-runtime", help="login com DATABASE_POOLER_URL e mostra current_user")
    args = parser.parse_args(argv)
    if args.command == "verify-runtime":
        print(f"current_user={verify_runtime_login()}")
        return 0
    if args.rollback:
        user = rollback_runtime_url(args.env_file)
        print(f"DATABASE_POOLER_URL restaurado em {args.env_file.name} para o usuário {user}")
        return 0
    user = set_runtime_password(args.env_file)
    # Never print the URL or the password.
    print(f"DATABASE_POOLER_URL atualizado em {args.env_file.name} para o usuário {user}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
