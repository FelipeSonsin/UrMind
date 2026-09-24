"""Regra arquitetural automatizada. Documento 1, §14.5.

    Obrigatório, roda no CI, bloqueia o merge. Valida o pyproject.toml e todos
    os imports de backend/app e scripts contra a lista proibida.
    Falha também se aparecer qualquer configuração como OPENAI_API_KEY,
    ANTHROPIC_API_KEY, GEMINI_API_KEY ou OLLAMA_HOST.

Este teste não substitui revisão de arquitetura, mas impede que uma integração
generativa entre no núcleo sem que ninguém perceba.
"""

from __future__ import annotations

import ast
import os
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Documento 1, §14.5 — lista proibida, literal.
FORBIDDEN_DISTRIBUTIONS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "ollama",
        "google-generativeai",
        "google-genai",
        "llama-cpp-python",
        "transformers",
        "vllm",
        "litellm",
    }
)

#: Nomes de módulo correspondentes, como apareceriam em um import.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "ollama",
        "google_generativeai",
        "google_genai",
        "llama_cpp",
        "transformers",
        "vllm",
        "litellm",
    }
)

#: Documento 1, §14.5 — configuração proibida.
FORBIDDEN_CONFIG_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OLLAMA_HOST",
    "HUGGINGFACEHUB_API_TOKEN",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
)

#: Árvores varridas, conforme a §14.5.
SCANNED_SOURCE_DIRS: tuple[str, ...] = ("backend/app", "scripts")

#: pyproject.toml de cada projeto Python do repositório.
SCANNED_PYPROJECTS: tuple[str, ...] = ("backend/pyproject.toml",)

#: Arquivos varridos em busca de configuração proibida.
CONFIG_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".env", ".example"}
)
CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        ".env.example",
        "compose.yaml",
        "compose.yml",
        "Makefile",
        "Dockerfile",
        "Caddyfile",
    }
)

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "site-packages",
    }
)
EXCLUDED_ROOT_TREES: frozenset[str] = frozenset({"datasets"})

#: Este próprio arquivo cita as listas proibidas e não pode se autoacusar.
SELF = Path(__file__).resolve()


def _is_excluded(path: Path) -> bool:
    return bool(path.parts and path.parts[0] in EXCLUDED_ROOT_TREES) or any(
        part in EXCLUDED_DIR_NAMES for part in path.parts
    )


def _python_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCANNED_SOURCE_DIRS:
        base = REPO_ROOT / rel
        if not base.is_dir():
            continue
        files.extend(p for p in base.rglob("*.py") if not _is_excluded(p.relative_to(REPO_ROOT)))
    return files


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _declared_distributions(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        raw.extend(extra)

    names: set[str] = set()
    for requirement in raw:
        name = requirement.strip()
        for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
            name = name.split(separator)[0]
        if name:
            names.add(name.strip().lower().replace("_", "-"))
    return names


def _config_files() -> list[Path]:
    files: list[Path] = []

    # Excluir antes de descer: rglob atravessava .venv/node_modules/datasets
    # (inclusive placeholders OneDrive) para descartá-los só depois.
    def fail_on_unreadable_tree(error: OSError) -> None:
        raise RuntimeError("Diretório de configuração inacessível") from error

    for base, dirs, names in os.walk(
        REPO_ROOT, topdown=True, followlinks=False, onerror=fail_on_unreadable_tree
    ):
        dirs[:] = sorted(
            name for name in dirs if not _is_excluded((Path(base) / name).relative_to(REPO_ROOT))
        )
        for name in names:
            path = Path(base) / name
            if path == SELF:
                continue
            if name in CONFIG_FILENAMES or path.suffix in CONFIG_SUFFIXES:
                files.append(path)
    return files


def test_scanned_trees_are_not_silently_empty() -> None:
    """Uma varredura que não vê nenhum arquivo passaria por engano."""
    assert _python_files(), (
        "Nenhum arquivo .py encontrado em "
        f"{SCANNED_SOURCE_DIRS} a partir de {REPO_ROOT}. "
        "O teste arquitetural estaria passando sem varrer nada."
    )


def test_config_scan_prunes_only_excluded_trees(monkeypatch, tmp_path: Path) -> None:
    included = (
        "backend/app/config.py",
        "backend/app/datasets/rules.yaml",
        "scripts/setup.py",
        "models/model.yaml",
        "mlruns/run.yaml",
    )
    excluded = (
        "datasets/raw/metadata.yaml",
        ".venv/lib/settings.toml",
        "frontend/node_modules/pkg/config.yaml",
    )
    for relative in (*included, *excluded):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("safe: true", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    found = {path.relative_to(tmp_path).as_posix() for path in _config_files()}

    assert found == set(included)


def test_config_scan_fails_closed_on_unreadable_tree(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    def unreadable(_root, **kwargs):
        kwargs["onerror"](PermissionError("inaccessible"))
        yield tmp_path, [], []

    monkeypatch.setattr(os, "walk", unreadable)
    with pytest.raises(RuntimeError, match="inacessível"):
        _config_files()


def test_no_forbidden_import_in_source_trees() -> None:
    """O backend e os scripts não importam nenhum SDK de LLM."""
    offenders: list[str] = []
    for path in _python_files():
        forbidden = _imported_top_level_modules(path) & FORBIDDEN_MODULES
        if forbidden:
            relative = path.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{relative}: {', '.join(sorted(forbidden))}")

    assert not offenders, (
        "Import de modelo generativo de terceiros no núcleo "
        "(Documento 1, §0.1 e §14.5):\n  " + "\n  ".join(offenders)
    )


def test_no_forbidden_distribution_in_pyproject() -> None:
    """Nenhum pyproject.toml declara dependência de LLM, nem em extras."""
    offenders: list[str] = []
    for rel in SCANNED_PYPROJECTS:
        pyproject = REPO_ROOT / rel
        if not pyproject.is_file():
            continue
        forbidden = _declared_distributions(pyproject) & FORBIDDEN_DISTRIBUTIONS
        if forbidden:
            offenders.append(f"{rel}: {', '.join(sorted(forbidden))}")

    assert not offenders, (
        "Dependência de modelo generativo de terceiros declarada "
        "(Documento 1, §0.1 e §14.5):\n  " + "\n  ".join(offenders)
    )


def test_no_forbidden_config_key_anywhere() -> None:
    """Nenhum segredo ou variável de LLM aparece em código ou configuração."""
    offenders: list[str] = []
    files = _config_files()
    assert REPO_ROOT / "backend/pyproject.toml" in files, "Configuração canônica não foi varrida"
    for path in files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            offenders.append(f"{relative}: leitura indisponível ({type(exc).__name__})")
            continue
        hits = [key for key in FORBIDDEN_CONFIG_KEYS if key in content]
        if hits:
            offenders.append(f"{relative}: {', '.join(hits)}")

    assert not offenders, (
        "Configuração de modelo generativo de terceiros presente "
        "(Documento 1, §14.5):\n  " + "\n  ".join(offenders)
    )


def test_config_scan_does_not_ignore_unreadable_config(monkeypatch, tmp_path: Path) -> None:
    pyproject = tmp_path / "backend/pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text("[project]", encoding="utf-8")
    unreadable = tmp_path / "backend/broken.yaml"
    unreadable.write_bytes(b"\xff")
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    with pytest.raises(AssertionError, match="broken.yaml: leitura indisponível"):
        test_no_forbidden_config_key_anywhere()
