"""Adquire somente os dois pacotes oficiais BDD10K registrados; dry-run padrão."""

import argparse
import json
import zipfile
from urllib.parse import urlsplit

from _budget import preflight
from _core import (
    DATASETS_DIR,
    configure_stdout,
    file_sha256,
    require_local,
    write_json_report,
)
from acquire_registered import download_ranges, target


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=8)
    args = parser.parse_args()
    plan_path = DATASETS_DIR / "metadata/bdd100k_acquisition_plan.json"
    plan = json.loads(require_local(plan_path).read_text(encoding="utf-8"))
    if plan["authorization"] != "user_explicit_download_20260908":
        raise ValueError("aquisição sem autorização registrada")
    expected = {
        "bdd100k_images_10k.zip": 1_103_797_977,
        "bdd100k_seg_maps.zip": 167_867_229,
    }
    if len(plan["files"]) != 2:
        raise ValueError("somente os dois pacotes BDD10K são autorizados")
    remaining = 0
    for entry in plan["files"]:
        destination = target(entry["path"])
        if (
            entry["dataset"] != "bdd100k"
            or expected.get(destination.name) != entry["size_bytes"]
            or entry["url"] != "http://128.32.162.150/bdd100k/" + destination.name
            or urlsplit(entry["url"]).query
        ):
            raise ValueError("plano diverge dos links oficiais registrados")
        if not destination.exists():
            partial = target(entry["path"] + ".part")
            size = require_local(partial).stat().st_size if partial.exists() else 0
            if size > entry["size_bytes"]:
                raise ValueError("parcial excede pacote registrado")
            remaining += entry["size_bytes"] - size
    # Inclui o restante dos dois outros downloads registrados mesmo se concorrentes.
    other_plan = json.loads(
        require_local(DATASETS_DIR / "metadata/acquisition_plan.json").read_text()
    )
    other_remaining = 0
    for entry in other_plan["files"]:
        p = target(entry["path"])
        if not p.exists():
            part = target(entry["path"] + ".part")
            present = require_local(part).stat().st_size if part.exists() else 0
            other_remaining += max(0, entry["size_bytes"] - present)
    peak = remaining + other_remaining + 100_000_000
    budget = preflight(
        "BDD10K + aquisições concorrentes registradas", peak, peak, raise_on_block=True
    )
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry-run",
                "download_bytes": remaining,
                "other_pending_bytes": other_remaining,
                "peak_bytes": peak,
                "budget": budget.as_dict(),
                "affected": [e["path"] for e in plan["files"]],
            },
            indent=2,
        ),
        flush=True,
    )
    if not args.execute:
        return
    results = []
    for entry in plan["files"]:
        p = target(entry["path"])
        if not p.exists():
            result = download_ranges(entry, args.resume, args.workers)
        else:
            if require_local(p).stat().st_size != entry["size_bytes"]:
                raise ValueError("pacote existente com tamanho divergente")
            with zipfile.ZipFile(p) as archive:
                if archive.testzip() is not None:
                    raise ValueError("CRC do pacote existente diverge")
            result = {
                "path": entry["path"],
                "size_bytes": entry["size_bytes"],
                "sha256": file_sha256(p),
                "status": "existing_crc_verified",
            }
        results.append(result)
        print("VERIFIED CRC AND LOCAL SHA256", entry["path"], flush=True)
        write_json_report(
            "acquisition_result_bdd100k.json",
            {
                "passed": len(results) == len(plan["files"]),
                "files": results,
                "plan_sha256": file_sha256(plan_path),
                "official_checksum_verified": False,
                "transport_authenticated": False,
                "checksum_limitations": plan["checksum_note"],
            },
        )


if __name__ == "__main__":
    main()
