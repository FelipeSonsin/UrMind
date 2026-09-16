"""Regressões do registro: contrato, scripts e artefatos precisam concordar."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "datasets" / "refresh_registry.py"
SPEC = importlib.util.spec_from_file_location("refresh_registry", SCRIPT)
registry = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = registry
SPEC.loader.exec_module(registry)


def _write_contract(path: Path) -> None:
    path.write_text(
        """
artifact_pipeline:
  inventory: datasets/manifests/required.jsonl
urban_community_derived_pipeline:
  validation: datasets/reports/optional.json
artifact_registry_contract:
  optional:
    - datasets/reports/optional.json
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_contrato_descobre_obrigatorio_e_respeita_optional(tmp_path):
    contract = tmp_path / "contract.yaml"
    _write_contract(contract)

    discovered = registry.contract_artifacts(contract)

    assert discovered.required == {"datasets/manifests/required.jsonl"}
    assert discovered.optional == {"datasets/reports/optional.json"}


def test_check_detecta_artifact_stale_ausente_e_nao_registrado(tmp_path):
    present = tmp_path / "datasets" / "manifests" / "present.jsonl"
    missing = tmp_path / "datasets" / "reports" / "missing.json"
    present.parent.mkdir(parents=True)
    missing.parent.mkdir(parents=True)
    present.write_text("current\n", encoding="utf-8")
    registered = {
        "scripts": [],
        "artifacts": [
            {"path": "datasets/manifests/present.jsonl", "size_bytes": 1, "sha256": "0" * 64},
            {"path": "datasets/reports/orphan.json", "size_bytes": 1, "sha256": "0" * 64},
        ],
    }

    result = registry.check_registry(
        registered,
        root=tmp_path,
        scripts={},
        required={
            "datasets/manifests/present.jsonl",
            "datasets/reports/missing.json",
        },
        optional=set(),
    )

    assert result["artifacts"]["stale"] == ["datasets/manifests/present.jsonl"]
    assert result["artifacts"]["missing_on_disk"] == ["datasets/reports/missing.json"]
    assert result["artifacts"]["unregistered_required"] == ["datasets/reports/missing.json"]
    assert result["artifacts"]["orphaned"] == ["datasets/reports/orphan.json"]


def test_optional_ausente_nao_falha(tmp_path):
    result = registry.check_registry(
        {"scripts": [], "artifacts": []},
        root=tmp_path,
        scripts={},
        required=set(),
        optional={"datasets/reports/optional.json"},
    )

    assert registry.difference_count(result) == 0


def test_refresh_registry_e_o_unico_writer_do_registry():
    root = SCRIPT.parents[2]
    writers = []
    for path in sorted((root / "scripts" / "datasets").glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        if "REG.write_text" in source or re.search(
            r"write_json_report\(\s*[\"']artifact_registry\.json[\"']", source
        ):
            writers.append(path.name)

    assert writers == ["refresh_registry.py"]


def test_build_report_nao_escreve_registry():
    root = SCRIPT.parents[2]
    source = (root / "scripts" / "datasets" / "build_report.py").read_text(encoding="utf-8-sig")

    assert 'write_json_report("artifact_registry.json"' not in source


def test_audit_storage_e_o_unico_writer_do_relatorio_de_armazenamento():
    root = SCRIPT.parents[2]
    writers = []
    pattern = re.compile(r"write_json_report\(\s*[\"']storage_audit\.json[\"']")
    for path in sorted((root / "scripts" / "datasets").glob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8-sig")):
            writers.append(path.name)

    assert writers == ["audit_storage.py"]
