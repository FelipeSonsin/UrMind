"""Fronteiras da arquitetura oficial descrita no MASTER_PLAN e no README."""

from __future__ import annotations

import ast
import re
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


def _has_real_content(path) -> bool:
    """O caminho descartado voltou de fato — ou é só cache do interpretador?

    `backend/migrations` reapareceu contendo unicamente
    `__pycache__/env.cpython-313.pyc`: bytecode órfão de um `env.py` que não
    existe mais. Bastava alguém ter importado o módulo antes da remoção para o
    arquivo ficar lá. Tratar isso como "a arquitetura antiga voltou" é alarme
    falso, e alarme falso que não se consegue apagar ensina a ignorar o teste.

    Arquivo isolado conta sempre. Diretório conta quando tem algum arquivo que
    não seja cache Python — uma migration de verdade continua reprovando.
    """
    if not path.exists():
        return False
    if path.is_file():
        return True
    return any(
        p.is_file() and p.suffix not in (".pyc", ".pyo")
        for p in path.rglob("*")
    )


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
    present = [
        path.relative_to(REPO).as_posix()
        for path in deprecated_paths
        if _has_real_content(path)
    ]
    assert not present, f"Caminhos da arquitetura descartada reapareceram: {present}"


# ------------------------------------------------- geradores não fixam data


GENERATORS = ("refresh_sources.py", "refresh_readiness.py")


@pytest.mark.parametrize("script", GENERATORS)
def test_gerador_nao_tem_data_escrita_a_mao(script):
    """Data no código envelhece sozinha e desacredita os números que estão certos.

    Datas fixas em saídas geradas deixam de refletir a execução atual.
    """
    caminho = REPO / "scripts" / "datasets" / script
    if not caminho.is_file():
        pytest.skip(f"{script} não existe neste checkout")

    # Data em docstring é NARRATIVA: registra quando algo aconteceu
    # ("mapillary_msls, aposentado em 2026-09-08") e é informação legítima. O que
    # não pode existir é data em string de código, porque essa chega à saída e
    # afirma quando o artefato foi gerado — afirmação que mente no dia seguinte.
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    docstrings = {
        id(no.body[0].value)
        for no in ast.walk(arvore)
        if isinstance(no, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and no.body
        and isinstance(no.body[0], ast.Expr)
        and isinstance(no.body[0].value, ast.Constant)
        and isinstance(no.body[0].value.value, str)
    }
    # `removed_at` de uma fonte aposentada é fato datado: mudá-lo para hoje
    # seria mentir sobre quando a remoção aconteceu. A exceção é nominal e
    # pequena de propósito — todo campo novo com data precisa ser discutido.
    fatos_historicos = ("2026-09-08",)
    padrao = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
    suspeitas = [
        no.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Constant)
        and isinstance(no.value, str)
        and id(no) not in docstrings
        and padrao.search(no.value)
        and no.value not in fatos_historicos
    ]

    assert not suspeitas, f"{script} tem data escrita à mão no código: {suspeitas}"
