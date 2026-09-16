"""Regenera ou confere o registro a partir de ``artifact_contract.yaml``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "datasets/metadata/artifact_registry.json"
CONTRACT = ROOT / "datasets/metadata/artifact_contract.yaml"
_ARTIFACT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}
# Prefixos cobertos pelo registro. `backend/` entrou porque backend/ml-stack.json
# e entrada de portao de `app.ml.training.validate_readiness` e ficava fora de
# qualquer verificacao de integridade: o filtro so reconhecia `datasets/`, entao
# declara-lo no contrato era silenciosamente descartado. Artefato que governa
# portao sem hash conferivel e portao que se pode editar sem deixar rastro.
_ARTIFACT_PREFIXES = ("datasets/", "backend/")
_CLOUD_ONLY_ATTRIBUTES = 0x00000400 | 0x00001000 | 0x00400000


@dataclass(frozen=True)
class ContractArtifacts:
    required: set[str]
    optional: set[str]


def _is_cloud_only(path: Path) -> bool:
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    return os.name == "nt" and bool(attributes & _CLOUD_ONLY_ATTRIBUTES)


def require_local_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise RuntimeError(f"não é arquivo regular: {path}")
    if _is_cloud_only(path):
        raise RuntimeError(f"cloud-only: hash recusado sem hidratação: {path}")
    return path


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with require_local_file(path).open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            paths.update(_artifact_paths(child))
    elif isinstance(value, list):
        for child in value:
            paths.update(_artifact_paths(child))
    elif isinstance(value, str):
        candidate = value.split("#", 1)[0].strip()
        path = Path(candidate)
        if (
            candidate.startswith(_ARTIFACT_PREFIXES)
            and path.suffix.lower() in _ARTIFACT_SUFFIXES
        ):
            paths.add(path.as_posix())
    return paths


def contract_artifacts(path: Path = CONTRACT) -> ContractArtifacts:
    document = (
        yaml.safe_load(require_local_file(path).read_text(encoding="utf-8")) or {}
    )
    declared = _artifact_paths(document)
    if path.is_relative_to(ROOT):
        declared.add(path.relative_to(ROOT).as_posix())
    policy = document.get("artifact_registry_contract", {})
    optional = {Path(item).as_posix() for item in policy.get("optional", [])}
    ignored = {Path(item).as_posix() for item in policy.get("ignored", [])}
    return ContractArtifacts(required=declared - optional - ignored, optional=optional)


def current_scripts(root: Path = ROOT) -> dict[str, dict[str, int | str]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted((root / "scripts/datasets").glob("*.py"))
        if path.name != "__init__.py"
    }


def _current_artifacts(root: Path, required: set[str], optional: set[str]):
    current: dict[str, dict[str, int | str]] = {}
    missing: list[str] = []
    unreadable: list[str] = []
    for relative in sorted(required | optional):
        path = root / relative
        if not path.is_file():
            if relative in required:
                missing.append(relative)
            continue
        try:
            current[relative] = {"sha256": sha(path), "size_bytes": path.stat().st_size}
        except (OSError, RuntimeError) as exc:
            unreadable.append(f"{relative}: {exc}")
    return current, missing, unreadable


def _section_drift(
    registered: list[dict],
    current: dict,
    *,
    required: set[str],
    allowed: set[str] | None = None,
):
    previous = {item["path"]: item for item in registered}
    allowed = required if allowed is None else allowed
    return {
        "stale": sorted(
            path
            for path in previous.keys() & current.keys()
            if previous[path].get("sha256") != current[path]["sha256"]
            or previous[path].get("size_bytes") != current[path]["size_bytes"]
        ),
        "missing_on_disk": sorted(path for path in required if path not in current),
        "unregistered_required": sorted(
            path for path in required if path not in previous
        ),
        "orphaned": sorted(path for path in previous if path not in allowed),
    }


def check_registry(
    registered: dict,
    *,
    root: Path,
    scripts: dict,
    required: set[str],
    optional: set[str],
):
    artifacts, missing, unreadable = _current_artifacts(root, required, optional)
    result = {
        "scripts": _section_drift(
            registered.get("scripts", []), scripts, required=set(scripts)
        ),
        "artifacts": _section_drift(
            registered.get("artifacts", []),
            artifacts,
            required=required,
            allowed=required | optional,
        ),
    }
    result["artifacts"]["missing_on_disk"] = sorted(
        set(result["artifacts"]["missing_on_disk"]) | set(missing)
    )
    result["artifacts"]["unreadable"] = unreadable
    return result


def difference_count(result: dict) -> int:
    return sum(len(paths) for section in result.values() for paths in section.values())


def _entries(current: dict) -> list[dict]:
    return [{"path": path, **metadata} for path, metadata in sorted(current.items())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    registry = json.loads(require_local_file(REG).read_text(encoding="utf-8"))
    contract = contract_artifacts()
    scripts = current_scripts()
    result = check_registry(
        registry,
        root=ROOT,
        scripts=scripts,
        required=contract.required,
        optional=contract.optional,
    )
    if args.check:
        print(
            f"registro: {len(scripts)} scripts, {len(registry.get('artifacts', []))} artefatos registrados"
        )
        for section, differences in result.items():
            print(f"{section}:")
            for kind, paths in differences.items():
                print(f"  {kind}: {len(paths)}")
                for path in paths:
                    print(f"    - {path}")
        return 1 if difference_count(result) else 0

    artifacts, missing, unreadable = _current_artifacts(
        ROOT, contract.required, contract.optional
    )
    if missing or unreadable:
        for path in missing:
            print(f"artefato obrigatório ausente: {path}")
        for reason in unreadable:
            print(f"artefato obrigatório ilegível: {reason}")
        return 1
    registry["scripts"] = _entries(scripts)
    registry["artifacts"] = _entries(artifacts)
    registry["updated"] = time.strftime("%Y-%m-%d")
    REG.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"registro: {len(scripts)} scripts, {len(artifacts)} artefatos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
