"""Busca externa limitada, só metadados; não move/exclui/hidrata. Não define caminhos de dataset."""

import json
from pathlib import Path

from _budget import preflight
from _core import PROJECT_ROOT, configure_stdout, write_json_report

KEYWORDS = (
    "urmind",
    "rdd2022",
    "fecart",
    "rampnet",
    "camber",
    "univali",
    "cracks-and-potholes",
    "urban-community",
)


def measure_external(path):
    total = count = 0
    errors = []
    pending = [path]
    while pending:
        p = pending.pop()
        try:
            if p.is_symlink() or (hasattr(Path, "is_junction") and p.is_junction()):
                errors.append("link_not_followed")
                continue
            if p.is_dir():
                pending.extend(p.iterdir())
            else:
                total += p.stat().st_size
                count += 1
        except OSError as exc:
            errors.append(type(exc).__name__)
    return {"logical_size": total, "file_count": count, "errors": errors}


def main():
    configure_stdout()
    preflight("relatório busca externa limitada", 100_000, 200_000, raise_on_block=True)
    home = Path.home()
    roots = [home / "Downloads", home / "Desktop", PROJECT_ROOT.parent]
    matches = []
    checked = []
    errors = []
    for root in sorted(set(roots)):
        location = (
            str(root.relative_to(home))
            if root.is_relative_to(home)
            else "project_parent"
        )
        checked.append(
            {
                "base": "user_profile" if root.is_relative_to(home) else "project",
                "relative_location": location,
            }
        )
        try:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if child.resolve() == PROJECT_ROOT:
                    continue
                if any(k in child.name.lower() for k in KEYWORDS):
                    relative = (
                        child.relative_to(home).as_posix()
                        if child.is_relative_to(home)
                        else child.name
                    )
                    matches.append(
                        {
                            "base": "user_profile"
                            if child.is_relative_to(home)
                            else "project_parent",
                            "relative_location": relative,
                            **measure_external(child),
                        }
                    )
        except OSError as exc:
            errors.append({"location": location, "error": type(exc).__name__})
    result = {
        "scope": "immediate children of Downloads/Desktop/project parent matching project/dataset names; no global exhaustive scan",
        "checked": checked,
        "matches": matches,
        "errors": errors,
        "external_files_deleted": False,
        "note": "Localizações são inventário externo, nunca referências de dataset. Somente leitura de metadados.",
    }
    write_json_report("external_locations.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
