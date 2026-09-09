"""Fixtures compartilhadas da suíte do backend."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# A suíte nunca deve herdar um .env de desenvolvimento nem cair no guarda de
# produção do app.config. Definido antes de qualquer import de `app`.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")

#: Raiz do repositório (…/FECART Sistema), duas pastas acima de backend/tests.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """Cliente HTTP sobre a aplicação em memória. Não sobe servidor."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
