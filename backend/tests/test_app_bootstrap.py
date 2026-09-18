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
