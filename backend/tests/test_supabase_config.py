"""Separação Supabase API / Session Pooler (runtime e migrations) / conexão direta opcional.

Nenhum teste aqui abre conexão: URLs e chaves são fictícias, e o engine do
SQLAlchemy só conecta no primeiro uso.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import JWKS_PATH, Settings
from app.db.session import (
    Database,
    DatabaseNotConfiguredError,
    connect_args,
    is_transaction_pooler_port,
)

BACKEND = Path(__file__).resolve().parents[1]

# Valores fictícios, com marcadores fáceis de procurar em repr e mensagens.
PASSWORD = "senha-FICTICIA-123"
SECRET_KEY = "sb_secret_FICTICIA_nao_usar"
PUBLISHABLE_KEY = "sb_publishable_FICTICIA_nao_usar"
BASE = "https://refficticio.supabase.co"
DIRECT = f"postgresql://postgres:{PASSWORD}@db.refficticio.supabase.co:5432/postgres"
POOLER = f"postgresql://postgres.refficticio:{PASSWORD}@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
TRANSACTION_POOLER = POOLER.replace(":5432/", ":6543/")


def _settings(**overrides: str) -> Settings:
    env = {
        "DATABASE_URL": DIRECT,
        "MIGRATION_DATABASE_URL": POOLER,
        "DATABASE_POOLER_URL": POOLER,
        "SUPABASE_URL": BASE,
        "SUPABASE_SECRET_KEY": SECRET_KEY,
        "SUPABASE_PUBLISHABLE_KEY": PUBLISHABLE_KEY,
        "SUPABASE_JWKS_URL": BASE + JWKS_PATH,
    }
    env.update(overrides)
    return Settings.model_validate(env)


# --------------------------------------------------------------------- carga


def test_carrega_as_sete_variaveis() -> None:
    s = _settings()
    assert s.database_url == DIRECT
    assert s.migration_database_url == POOLER
    assert s.database_pooler_url == POOLER
    assert s.supabase_url == BASE
    assert s.supabase_secret_key == SECRET_KEY
    assert s.supabase_publishable_key == PUBLISHABLE_KEY
    assert s.supabase_jwks_url == BASE + JWKS_PATH


def test_suite_nao_herda_o_env_de_desenvolvimento() -> None:
    """Com ENVIRONMENT=test, importar app.config não injeta o backend/.env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "SUPABASE_"))}
    env["ENVIRONMENT"] = "test"
    probe = (
        "import os, app.config; "
        "print(any(os.getenv(k) for k in ('DATABASE_URL','DATABASE_POOLER_URL',"
        "'SUPABASE_SECRET_KEY','SUPABASE_PUBLISHABLE_KEY')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


# ------------------------------------------------------------- SUPABASE_URL


def test_supabase_url_base_normaliza_barra_final() -> None:
    assert _settings(SUPABASE_URL=BASE + "/").supabase_url == BASE


@pytest.mark.parametrize("caminho", ["/rest/v1", "/rest/v1/", "/auth/v1"])
def test_supabase_url_recusa_caminho_de_endpoint(caminho: str) -> None:
    with pytest.raises(ValidationError, match="sem caminho"):
        _settings(SUPABASE_URL=BASE + caminho, SUPABASE_JWKS_URL="")


def test_supabase_url_exige_https() -> None:
    with pytest.raises(ValidationError):
        _settings(SUPABASE_URL="http://refficticio.supabase.co", SUPABASE_JWKS_URL="")


# ------------------------------------------------------------------- JWKS


def test_jwks_exige_caminho_oficial() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_JWKS_URL"):
        _settings(SUPABASE_JWKS_URL=BASE + "/auth/v1/keys")


def test_jwks_de_outro_projeto_e_recusado() -> None:
    with pytest.raises(ValidationError, match="projeto diferente"):
        _settings(SUPABASE_JWKS_URL="https://outroprojeto.supabase.co" + JWKS_PATH)


# -------------------------------------------------- runtime x migrations


def test_runtime_usa_o_session_pooler() -> None:
    db = Database(_settings())
    try:
        assert db.engine.url.port == 5432
        assert db.engine.url.host.endswith("pooler.supabase.com")
    finally:
        db.engine.sync_engine.dispose()


def test_runtime_sem_pooler_falha_explicito_mesmo_com_database_url() -> None:
    """Sem fallback silencioso para a conexão direta das migrations."""
    with pytest.raises(DatabaseNotConfiguredError, match="DATABASE_POOLER_URL"):
        Database(_settings(DATABASE_POOLER_URL=""))


def test_migrations_nunca_leem_o_pooler() -> None:
    fonte = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "database_pooler_url" not in fonte
    assert "settings.migration_database_url or settings.database_url" in fonte


def test_migrations_usam_selector_event_loop_no_windows() -> None:
    fonte = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert 'sys.platform == "win32"' in fonte
    assert "asyncio.SelectorEventLoop" in fonte


def test_percent_encoding_permanece_inalterado_na_url_real() -> None:
    encoded = DIRECT.replace(PASSWORD, "senha%40com%3Areservados")
    settings = _settings(DATABASE_URL=encoded, MIGRATION_DATABASE_URL=encoded)
    assert settings.database_url == encoded
    assert settings.migration_database_url == encoded


def test_detecta_porta_do_transaction_pooler() -> None:
    assert is_transaction_pooler_port(TRANSACTION_POOLER)
    assert not is_transaction_pooler_port(POOLER)
    assert not is_transaction_pooler_port(DIRECT)


def test_runtime_recusa_transaction_pooler() -> None:
    with pytest.raises(DatabaseNotConfiguredError, match="6543") as exc:
        Database(_settings(DATABASE_POOLER_URL=TRANSACTION_POOLER))
    assert PASSWORD not in str(exc.value)


def test_alembic_recusa_transaction_pooler() -> None:
    fonte = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "if is_transaction_pooler_port(url):" in fonte


# ------------------------------------------------ psycopg: pooler e SSL


def test_session_pooler_mantem_prepared_statements_padrao() -> None:
    assert "prepare_threshold" not in connect_args(POOLER)
    assert "prepare_threshold" not in connect_args(DIRECT)


def test_url_com_sslmode_require_explicito_e_aceita() -> None:
    assert "sslmode" not in connect_args(f"{POOLER}?sslmode=require")


def test_host_supabase_recebe_sslmode_require() -> None:
    assert connect_args(DIRECT)["sslmode"] == "require"
    assert connect_args(POOLER)["sslmode"] == "require"


@pytest.mark.parametrize("modo", ["verify-ca", "verify-full", "require"])
def test_sslmode_seguro_explicito_e_respeitado(modo: str) -> None:
    assert "sslmode" not in connect_args(f"{DIRECT}?sslmode={modo}")


@pytest.mark.parametrize("modo", ["disable", "allow", "prefer"])
def test_sslmode_inseguro_em_host_supabase_e_recusado(modo: str) -> None:
    with pytest.raises(ValueError, match="inseguro") as exc:
        connect_args(f"{DIRECT}?sslmode={modo}")
    assert PASSWORD not in str(exc.value)


def test_host_local_fica_com_padrao_do_libpq() -> None:
    local = "postgresql://postgres:x@localhost:5432/urmind"
    assert "sslmode" not in connect_args(local)


# -------------------------------------------------------- segredos


def test_repr_e_str_nao_expoem_segredos() -> None:
    s = _settings()
    for texto in (repr(s), str(s)):
        for segredo in (PASSWORD, SECRET_KEY, PUBLISHABLE_KEY):
            assert segredo not in texto


def test_validation_error_nao_ecoa_o_valor_recebido() -> None:
    # Valor CURTO de propósito: o pydantic trunca `input_value` longo no meio, e
    # um valor longo esconderia o vazamento por acaso. Sem hide_input_in_errors
    # este teste falha (conferido por mutação).
    marcador = "SEGREDO42"
    with pytest.raises(ValidationError) as exc:
        _settings(SUPABASE_URL="ftp://" + marcador, SUPABASE_JWKS_URL="")
    assert marcador not in str(exc.value)
    assert marcador not in repr(exc.value)


def test_engine_nao_expoe_senha_na_url() -> None:
    db = Database(_settings())
    try:
        assert PASSWORD not in repr(db.engine.url)
        assert PASSWORD not in str(db.engine.url)
    finally:
        db.engine.sync_engine.dispose()


def test_erro_de_configuracao_nao_contem_url() -> None:
    with pytest.raises(DatabaseNotConfiguredError) as exc:
        Database(_settings(DATABASE_POOLER_URL=""))
    assert PASSWORD not in str(exc.value)


def test_cors_exige_origens_explicitas() -> None:
    assert _settings(CORS_ALLOWED_ORIGINS="").cors_origins == []
    assert _settings(
        CORS_ALLOWED_ORIGINS="https://app.example.org/, http://localhost:4173"
    ).cors_origins == [
        "https://app.example.org",
        "http://localhost:4173",
    ]
    for invalid in ("*", "http://app.example.org", "https://app.example.org/api"):
        with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS|origem"):
            _settings(CORS_ALLOWED_ORIGINS=invalid)
