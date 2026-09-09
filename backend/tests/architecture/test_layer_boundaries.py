"""Fronteiras da arquitetura oficial descrita no MASTER_PLAN e no README."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
REPO = BACKEND.parent


def python_files(package: Path) -> list[Path]:
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in path.parts
    )


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", python_files(APP / "api"), ids=lambda path: path.name)
def test_api_does_not_access_persistence_directly(path: Path) -> None:
    """Endpoints validam HTTP e delegam; acesso a dados fica nos repositórios."""
    imported = imports(path)
    forbidden = {
        name
        for name in imported
        if name.startswith(("sqlalchemy", "geoalchemy2", "psycopg", "app.models", "app.db"))
    }
    assert not forbidden, f"{path.name} acessa persistência: {sorted(forbidden)}"


@pytest.mark.parametrize("path", python_files(APP / "services"), ids=lambda path: path.name)
def test_services_do_not_depend_on_http(path: Path) -> None:
    """Regras e orquestração do produto não dependem de FastAPI ou Starlette."""
    forbidden = {
        name
        for name in imports(path)
        if name.split(".")[0] in {"fastapi", "starlette"}
    }
    assert not forbidden, f"{path.name} depende de HTTP: {sorted(forbidden)}"


def test_only_the_canonical_repository_module_executes_queries() -> None:
    repository_modules = {path.name for path in python_files(APP / "repositories")}
    assert repository_modules == {"__init__.py", "core.py"}


def test_deprecated_architecture_does_not_return() -> None:
    """O passo 0 do plano elimina a infraestrutura e o domínio paralelos antigos."""
    deprecated_paths = (
        REPO / "compose.yaml",
        REPO / "compose.yml",
        REPO / "Makefile",
        REPO / "infrastructure",
        REPO / "ml",
        REPO / "scout-agent",
        BACKEND / "migrations",
        APP / "db" / "models.py",
        APP / "integrations" / "auth" / "keycloak.py",
        APP / "integrations" / "storage" / "seaweedfs.py",
    )
    present = [path.relative_to(REPO).as_posix() for path in deprecated_paths if path.exists()]
    assert not present, f"Caminhos da arquitetura descartada reapareceram: {present}"
