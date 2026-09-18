"""Bootstrap oficial da API: ``python -m app``.

Desenvolvimento: ``python -m app`` (HTTP em 127.0.0.1:8000, PWA pelo Vite).
Produção/HTTPS: ``python -m app --host 0.0.0.0 --port 8443 --ssl-certfile cert.pem
--ssl-keyfile key.pem``, com ``SERVE_FRONTEND_DIR`` apontando para o build do PWA —
API e PWA na mesma origem, sob TLS, sem proxy adicional e sem Docker (§3.1, §6.1).
"""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys

import uvicorn


def _loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def main(argv: list[str] | None = None) -> None:
    """Inicia o app existente com um loop compatível com psycopg async."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args(argv)
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile e --ssl-keyfile andam juntos")
    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )
    server = uvicorn.Server(config)
    try:
        asyncio.run(server.serve(), loop_factory=_loop_factory)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
