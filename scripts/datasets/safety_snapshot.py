"""Snapshot de integridade por caminho/tamanho/mtime de raw; código protegido por SHA-256."""

import argparse
import hashlib
import json

from _core import (
    DATASETS_DIR,
    RAW_DIR,
    iter_files,
    relative_to_project,
    require_local,
    write_json_report,
)


def snapshot():
    h = hashlib.sha256()
    count = 0
    size = 0
    for path in iter_files(RAW_DIR):
        stat = path.stat()
        h.update(
            json.dumps(
                [relative_to_project(path), stat.st_size, stat.st_mtime_ns],
                ensure_ascii=False,
            ).encode()
        )
        count += 1
        size += stat.st_size
    return {
        "raw_metadata_sha256": h.hexdigest(),
        "raw_file_count": count,
        "raw_logical_bytes": size,
        "method": "ordered relative path + size + mtime_ns; read metadata only; not full content proof",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verify", action="store_true")
    a = p.parse_args()
    current = snapshot()
    baseline = DATASETS_DIR / "reports" / "raw_safety_baseline.json"
    if a.verify:
        old = json.loads(require_local(baseline).read_text(encoding="utf-8"))
        report = {"passed": old == current, "baseline": old, "current": current}
        write_json_report("raw_safety_verification.json", report)
        print(report)
        return 0 if report["passed"] else 1
    if baseline.exists():
        raise RuntimeError("baseline já existe; não sobrescrever")
    write_json_report("raw_safety_baseline.json", current)
    print(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
