"""Valida caminhos portáteis e vínculos entre inventário, seleção e splits."""

import argparse
import csv
import json
from pathlib import PurePosixPath, PureWindowsPath

from _budget import preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    assert_inside_project,
    configure_stdout,
    relative_to_project,
    require_local,
    write_json_report,
)


def validate_path(value, check_files=False):
    if not isinstance(value, str) or not value:
        raise ValueError("caminho vazio")
    if (
        PureWindowsPath(value).drive
        or PureWindowsPath(value).root
        or PurePosixPath(value).is_absolute()
    ):
        raise ValueError("caminho absoluto")
    if ".." in PurePosixPath(value).parts or "\\" in value:
        raise ValueError("traversal ou separador não portátil")
    path = assert_inside_project(PROJECT_ROOT / value)
    if check_files and not path.exists():
        raise ValueError("arquivo referenciado ausente")
    return path


def walk_values(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str):
                yield k, v
            else:
                yield from walk_values(v)
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, str):
                yield "", v
            else:
                yield from walk_values(v)


def main():
    configure_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check-files", action="store_true")
    p.add_argument("--no-report", action="store_true")
    a = p.parse_args()
    issues = []
    count = 0
    for folder in ("metadata", "manifests", "splits", "reports"):
        for path in sorted((DATASETS_DIR / folder).glob("*")):
            if path.suffix not in (".json", ".jsonl", ".csv", ".txt", ".yaml"):
                continue
            if path.name == "portability_check.json":
                continue
            text = require_local(path).read_text(encoding="utf-8-sig")
            if path.suffix == ".json":
                values = list(walk_values(json.loads(text)))
            elif path.suffix == ".jsonl":
                values = [
                    pair
                    for line in text.splitlines()
                    if line
                    for pair in walk_values(json.loads(line))
                ]
            elif path.suffix == ".csv":
                values = [
                    pair
                    for row in csv.DictReader(text.splitlines())
                    for pair in row.items()
                ]
            elif path.suffix == ".yaml":
                from _budget import _load_yaml

                values = list(walk_values(_load_yaml(path)))
            else:
                values = [("split_path", v) for v in text.splitlines() if v]
            for key, value in values:
                if value.startswith(("https://", "http://")):
                    continue
                is_reference = value.startswith(
                    ("datasets/", "scripts/", "docs/")
                ) or key in (
                    "rel_path",
                    "annotation_path",
                    "container_path",
                    "local_path_relative",
                    "split_path",
                )
                if not is_reference:
                    continue
                count += 1
                try:
                    # Fontes futuras declaradas podem ainda não ter pasta física.
                    validate_path(
                        value,
                        a.check_files
                        and key
                        in (
                            "rel_path",
                            "annotation_path",
                            "split_path",
                            "container_path",
                        ),
                    )
                except (ValueError, RuntimeError) as exc:
                    issues.append(
                        {
                            "file": relative_to_project(path),
                            "field": key,
                            "value": value,
                            "problem": str(exc),
                        }
                    )
    result = {
        "passed": not issues,
        "references_checked": count,
        "issues": issues,
        "scope": "referências estruturadas de metadata/manifests/splits/reports; raw original não é reescrito",
    }
    if not a.no_report:
        preflight("relatório portabilidade", 1_000_000, 2_000_000, raise_on_block=True)
        write_json_report("portability_check.json", result)
    print(
        {"passed": result["passed"], "references_checked": count, "issues": issues[:10]}
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
