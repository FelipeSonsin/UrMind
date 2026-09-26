"""Runtime role password rotation helpers: no plaintext leaves the process."""

import base64
import hashlib
import hmac

import pytest

from app.db.migrate import replace_env_value, runtime_pooler_url, scram_sha256_verifier


def test_scram_verifier_formato_e_chaves_derivadas_da_senha():
    salt = bytes(range(16))
    verifier = scram_sha256_verifier("pencil", salt=salt)
    head, keys = verifier.split("$", 1)[1].split("$")
    iterations, salt_b64 = head.split(":")
    stored_b64, server_b64 = keys.split(":")
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert base64.b64decode(salt_b64) == salt and int(iterations) == 4096
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    assert base64.b64decode(stored_b64) == hashlib.sha256(client_key).digest()
    assert base64.b64decode(server_b64) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    assert "pencil" not in verifier


def test_scram_verifier_usa_sal_aleatorio_por_padrao():
    assert scram_sha256_verifier("x") != scram_sha256_verifier("x")


def test_url_runtime_preserva_host_porta_opcoes_e_codifica_senha():
    migration = "postgresql+psycopg://postgres.ref:old@aws-0-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    url = runtime_pooler_url(migration, "ref", "a/b@c:d")
    assert url == (
        "postgresql+psycopg://urmind_runtime.ref:a%2Fb%40c%3Ad"
        "@aws-0-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    assert "old" not in url


def test_substitui_somente_a_linha_da_chave_e_preserva_crlf():
    text = "A=1\r\nDATABASE_POOLER_URL='old'\r\nMIGRATION_DATABASE_URL='keep'\r\n"
    out = replace_env_value(text, "DATABASE_POOLER_URL", "new")
    assert out == "A=1\r\nDATABASE_POOLER_URL='new'\r\nMIGRATION_DATABASE_URL='keep'\r\n"


@pytest.mark.parametrize("text", ["A=1\n", "DATABASE_POOLER_URL=a\nDATABASE_POOLER_URL=b\n"])
def test_chave_ausente_ou_duplicada_e_recusada(text):
    with pytest.raises(ValueError):
        replace_env_value(text, "DATABASE_POOLER_URL", "new")


DEV = "impmeitwtusjtwjouggy"
ADMIN = f"postgresql+psycopg://postgres.{DEV}:adm@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"


class _FakeConnection:
    def __init__(self, role_row, log):
        self.role_row, self.log = role_row, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self.log.append(statement)
        return type("R", (), {"fetchone": lambda _self: self.role_row})()


def _patch(monkeypatch, *, migration=ADMIN, role_row=(False, True)):
    from types import SimpleNamespace

    import psycopg

    log: list = []
    monkeypatch.setattr(
        "app.config.get_settings", lambda: SimpleNamespace(migration_database_url=migration)
    )
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConnection(role_row, log))
    return log


def _env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_chave_com_bom_na_primeira_linha_e_reconhecida():
    out = replace_env_value("\ufeffDATABASE_POOLER_URL='old'\n", "DATABASE_POOLER_URL", "new")
    assert out == "\ufeffDATABASE_POOLER_URL='new'\n"


@pytest.mark.parametrize(
    ("env_text", "migration", "role_row"),
    [
        (None, ADMIN, (False, True)),  # env file missing
        ("A=1\n", ADMIN, (False, True)),  # key missing
        ("DATABASE_POOLER_URL=a\nDATABASE_POOLER_URL=b\n", ADMIN, (False, True)),
        ("DATABASE_POOLER_URL='x'\n", ADMIN.replace(DEV, "outroprojeto"), (False, True)),
        ("DATABASE_POOLER_URL='x'\n", ADMIN, None),  # role absent
        ("DATABASE_POOLER_URL='x'\n", ADMIN, (True, True)),  # elevated attributes
    ],
)
def test_senha_nunca_muda_se_alguma_validacao_falha(
    tmp_path, monkeypatch, env_text, migration, role_row
):
    from app.db.migrate import set_runtime_password

    log = _patch(monkeypatch, migration=migration, role_row=role_row)
    env = tmp_path / ".env" if env_text is None else _env(tmp_path, env_text)
    before = env.read_bytes() if env.exists() else None
    with pytest.raises((RuntimeError, ValueError)):
        set_runtime_password(env)
    assert not any("alter role" in str(statement) for statement in log)
    assert (env.read_bytes() if env.exists() else None) == before


def test_rotacao_grava_verificador_e_troca_somente_a_url_runtime(tmp_path, monkeypatch):
    from app.db.migrate import set_runtime_password

    log = _patch(monkeypatch)
    env = _env(tmp_path, "A=1\r\nDATABASE_POOLER_URL='old'\r\nMIGRATION_DATABASE_URL='keep'\r\n")
    assert set_runtime_password(env) == f"urmind_runtime.{DEV}"
    alter = [statement for statement in log if "alter role" in str(statement)]
    assert len(alter) == 1
    lines = env.read_bytes().decode("utf-8").split("\r\n")
    assert lines[0] == "A=1" and lines[2] == "MIGRATION_DATABASE_URL='keep'"
    assert lines[1].startswith(f"DATABASE_POOLER_URL='postgresql+psycopg://urmind_runtime.{DEV}:")
    password = lines[1].split(":", 3)[2].split("@", 1)[0]
    assert password and password not in repr(alter[0])
    assert not (tmp_path / ".env.tmp").exists()


def test_rollback_aponta_o_runtime_de_volta_para_a_identidade_admin(tmp_path, monkeypatch):
    from app.db.migrate import rollback_runtime_url

    _patch(monkeypatch)
    env = _env(tmp_path, "DATABASE_POOLER_URL='runtime'\n")
    assert rollback_runtime_url(env) == f"postgres.{DEV}"
    assert env.read_bytes().decode("utf-8") == f"DATABASE_POOLER_URL='{ADMIN}'\n"
