"""Orçamento em GB decimais; preflight local sem downloads ou exclusões."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    BudgetError,
    free_disk_bytes,
    measure_dir,
    relative_to_project,
)

GB = 1_000_000_000
BUDGET_PATH = DATASETS_DIR / "metadata" / "storage_budget.yaml"


def _load_yaml(path):
    from _core import require_local

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML necessário; instale a versão indicada em scripts/datasets/requirements.txt"
        ) from exc
    result = yaml.safe_load(require_local(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise TypeError("configuração YAML deve ser um mapa")
    return result


def _minimal_yaml(text):
    raise RuntimeError(
        "Parser parcial desativado: instale PyYAML para não ignorar configuração"
    )


@dataclass(frozen=True)
class Budget:
    max_total_ml_gb: float
    max_local_dataset_gb: float
    min_free_disk_gb: float
    report_threshold_mb: float
    dataset_caps: dict
    raw: dict

    @property
    def max_total_ml_bytes(self):
        return int(self.max_total_ml_gb * GB)

    @property
    def max_local_dataset_bytes(self):
        return int(self.max_local_dataset_gb * GB)

    @property
    def min_free_disk_bytes(self):
        return int(self.min_free_disk_gb * GB)


def load_budget(path=None):
    data = _load_yaml(path or BUDGET_PATH)
    limits = data["limits"]
    values = [
        float(limits[k])
        for k in (
            "MAX_TOTAL_ML_STORAGE_GB",
            "MAX_LOCAL_DATASET_SIZE_GB",
            "MIN_FREE_DISK_GB",
            "REPORT_THRESHOLD_MB",
        )
    ]
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError("limites precisam ser positivos e finitos")
    if values[0] > 40 or values[1] > 40 or values[2] < 10:
        raise BudgetError(
            "configuração relaxa os limites atuais: total 40 GB / disco livre 10 GB"
        )
    return Budget(
        *values, {k: float(v["cap_gb"]) for k, v in data["datasets"].items()}, data
    )


def ml_inventory():
    """Inclui datasets e todos os arquivos fora de ambientes, código e dependências.

    Pastas ML desconhecidas são contadas conservadoramente como conteúdo adicional.
    .venv/node_modules e caches de lint/teste não são datasets essenciais.
    """
    excluded = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "urmind.egg-info",
    }
    code_extensions = {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".csv",
        ".xml",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".html",
        ".css",
        ".sql",
        ".ps1",
        ".bat",
        ".lock",
        ".example",
    }
    import os

    extra = 0
    entries = []

    def fail(error):
        raise error

    for current, dirs, names in os.walk(PROJECT_ROOT, onerror=fail):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in excluded
            and not (Path(current) == PROJECT_ROOT and d == "datasets")
        )
        from _core import assert_inside_project

        for name in dirs:
            directory = Path(current) / name
            assert_inside_project(directory)
            if directory.is_symlink() or (
                hasattr(Path, "is_junction") and directory.is_junction()
            ):
                raise RuntimeError("link em pasta de ML; inventário abortado")
        for name in sorted(names):
            p = Path(current) / name
            if name.startswith(".env"):
                continue
            ml_folder = any(
                part.lower()
                in {
                    "models",
                    "checkpoints",
                    ".cache",
                    "cache",
                    "caches",
                    ".dvc",
                    "mlruns",
                    "mlartifacts",
                    "weights",
                }
                for part in p.relative_to(PROJECT_ROOT).parts
            )
            if (
                not ml_folder
                and p.suffix.lower() in code_extensions
                and p.stat().st_size < 10_000_000
            ):
                continue
            from _core import assert_inside_project

            assert_inside_project(p)
            from _core import allocated_bytes, is_cloud_only

            logical = p.stat().st_size
            size = max(logical, 0 if is_cloud_only(p) else allocated_bytes(p))
            extra += size
            entries.append(
                {
                    "path": relative_to_project(p),
                    "logical_size": logical,
                    "accounted_bytes": size,
                }
            )
    data = measure_dir(DATASETS_DIR)
    accounted = max(data.logical_size, data.size_on_disk)
    return {
        "datasets_bytes": accounted,
        "datasets_logical_bytes": data.logical_size,
        "datasets_allocated_bytes": data.size_on_disk,
        "accounting_basis": "max(logical, allocated) + other ML/conservative files; placeholders counted logically",
        "other_ml_or_conservative_bytes": extra,
        "total_ml_bytes": accounted + extra,
        "other_files": entries,
        "excluded_directories": sorted(excluded),
        "scope": "project_only; external locations reported separately",
    }


@dataclass
class PreflightResult:
    operation: str
    allowed: bool
    reasons: list
    current_datasets_bytes: int
    current_ml_bytes: int
    free_disk_bytes: int
    estimated_delta_bytes: int
    estimated_peak_bytes: int
    projected_datasets_bytes: int
    projected_free_bytes: int

    def as_dict(self):
        return asdict(self)

    def render(self):
        return "\n".join(f"{k}: {v}" for k, v in self.as_dict().items())


def preflight(
    operation,
    estimated_delta_bytes,
    estimated_peak_bytes=None,
    *,
    budget=None,
    current_datasets_bytes=None,
    current_ml_bytes=None,
    dataset_id=None,
    raise_on_block=False,
):
    budget = budget or load_budget()
    peak = (
        estimated_delta_bytes if estimated_peak_bytes is None else estimated_peak_bytes
    )
    if (
        any(not isinstance(v, int) or v < 0 for v in (estimated_delta_bytes, peak))
        or peak < estimated_delta_bytes
    ):
        raise ValueError("delta/pico devem ser inteiros não negativos; pico >= delta")
    if current_datasets_bytes is None or current_ml_bytes is None:
        inventory = ml_inventory()
        current_datasets_bytes = inventory["datasets_bytes"]
        current_ml_bytes = inventory["total_ml_bytes"]
    free = free_disk_bytes()
    if (
        any(
            type(v) is not int or v < 0
            for v in (current_datasets_bytes, current_ml_bytes)
        )
        or current_ml_bytes < current_datasets_bytes
    ):
        raise ValueError("inventário de bytes inválido")
    reasons = []
    if current_ml_bytes + peak > budget.max_total_ml_bytes:
        reasons.append("pico ML acima de 40 GB")
    if current_datasets_bytes + peak > budget.max_local_dataset_bytes:
        reasons.append("pico datasets acima de 40 GB")
    if free - peak < budget.min_free_disk_bytes:
        reasons.append("menos de 10 GB livres no pico")
    if dataset_id is not None and dataset_id not in budget.raw["datasets"]:
        raise ValueError("dataset não registrado no orçamento")
        # Toda operação deve incluir arquivos compactados, temporários e derivados em seu pico.
        # A distribuição individual é orientativa por decisão do usuário.
        # O consumo agregado continua limitado pelo preflight acima.
    result = PreflightResult(
        operation,
        not reasons,
        reasons,
        current_datasets_bytes,
        current_ml_bytes,
        free,
        estimated_delta_bytes,
        peak,
        current_datasets_bytes + estimated_delta_bytes,
        free - peak,
    )
    if raise_on_block and reasons:
        raise BudgetError(result.render())
    return result
