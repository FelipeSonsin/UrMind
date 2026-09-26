import asyncio
from types import SimpleNamespace

from app import __main__ as bootstrap


def test_windows_bootstrap_usa_selector(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    loop = bootstrap._loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_main_trata_ctrl_c_como_encerramento_normal(monkeypatch):
    server = SimpleNamespace(serve=lambda: object())
    monkeypatch.setattr(bootstrap.uvicorn, "Server", lambda _config: server)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap.asyncio, "run", interrupt)
    bootstrap.main([])  # argv explícito: o entrypoint agora aceita host/porta/TLS


def test_tls_exige_certificado_e_chave_juntos(monkeypatch):
    import pytest

    monkeypatch.setattr(
        bootstrap.uvicorn, "Server", lambda _config: SimpleNamespace(serve=lambda: object())
    )
    monkeypatch.setattr(bootstrap.asyncio, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        bootstrap.main(["--ssl-certfile", "cert.pem"])


def test_flags_chegam_ao_uvicorn(monkeypatch):
    captured = {}

    def config(app, **kwargs):
        captured.update({"app": app, **kwargs})
        return object()

    monkeypatch.setattr(bootstrap.uvicorn, "Config", config)
    monkeypatch.setattr(
        bootstrap.uvicorn, "Server", lambda _config: SimpleNamespace(serve=lambda: object())
    )
    monkeypatch.setattr(bootstrap.asyncio, "run", lambda *a, **k: None)
    bootstrap.main(
        ["--host", "0.0.0.0", "--port", "8443", "--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem"]
    )
    assert captured["app"] == "app.main:app"
    assert (captured["host"], captured["port"]) == ("0.0.0.0", 8443)
    assert (captured["ssl_certfile"], captured["ssl_keyfile"]) == ("c.pem", "k.pem")


def test_ready_is_503_without_database_and_security_headers_are_set() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/api/v1/ready")
    if getattr(app.state, "database", None) is None:
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "not_configured"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "camera=(self)" in response.headers["Permissions-Policy"]
    assert "Strict-Transport-Security" not in response.headers  # plain-HTTP test client
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'wasm-unsafe-eval';" in policy
    assert "worker-src 'self' blob:" in policy
    assert "object-src 'none'" in policy
    # 'wasm-unsafe-eval' só permite compilar WebAssembly; eval de JavaScript continua proibido.
    assert "'unsafe-eval'" not in policy
    assert "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";", 1)[0]
