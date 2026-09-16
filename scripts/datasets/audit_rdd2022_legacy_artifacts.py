"""Registra, sem hidratação, por que relatórios históricos não podem ser migrados."""

from __future__ import annotations

import json

from _core import DATASETS_DIR, file_sha256, require_local, write_json_report

ARTIFACTS = {
    "datasets/reports/rdd2022_reduction.json": ["script", "drop_reasons"],
}


def main() -> int:
    entries = []
    for relative, missing in ARTIFACTS.items():
        path = DATASETS_DIR.parent / relative
        json.loads(require_local(path).read_text(encoding="utf-8-sig"))
        entries.append(
            {
                "artifact": relative,
                "artifact_sha256": file_sha256(path),
                "status": "BLOCKED_BY_CLOUD_ONLY",
                "missing_contract_fields": missing,
                "required_evidence": (
                    "reexecução do producer oficial contra o estado físico exato da "
                    "operação histórica; parte do raw RDD2022 está cloud-only"
                ),
                "producer": (
                    "scripts/datasets/reduce_rdd2022.py"
                    if relative.endswith("reduction.json")
                    else "scripts/datasets/reconcile_after_reduction.py"
                ),
                "migration_performed": False,
            }
        )
    write_json_report(
        "rdd2022_legacy_artifact_status.json",
        {
            "dataset_id": "rdd2022",
            "status": "BLOCKED_BY_CLOUD_ONLY",
            "artifacts": entries,
            "regularized": ["datasets/reports/rdd2022_reconciliation.json"],
            "training_impact": (
                "não autoriza nem desautoriza registros; o loader exige a cadeia "
                "selection/split/annotation/autorização vigente"
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
