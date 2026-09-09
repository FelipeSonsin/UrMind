"""Verifica os pacotes já locais contra checksums registrados; não baixa/extrai."""

import csv
import hashlib
import json
import re

from _budget import preflight
from _core import (
    DATASETS_DIR,
    RAW_DIR,
    configure_stdout,
    file_sha256,
    is_cloud_only,
    iter_files,
    relative_to_project,
    require_local,
    write_json_report,
)


def digest(path, algorithm):
    h = hashlib.new(algorithm)
    with require_local(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    configure_stdout()
    preflight(
        "verificação de fontes locais", 8_000_000, 16_000_000, raise_on_block=True
    )
    checks = []
    registered = json.loads(
        require_local(DATASETS_DIR / "metadata" / "upstream_checksums.json").read_text(
            encoding="utf-8-sig"
        )
    )
    for entry in registered:
        if entry["dataset"] != "camber":
            continue
        path = DATASETS_DIR.parent / entry["path"]
        actual_digest = digest(path, entry["algorithm"])
        checks.append(
            {
                "path": relative_to_project(path),
                "algorithm": entry["algorithm"],
                "expected": entry["checksum"],
                "actual": actual_digest,
                "passed": actual_digest == entry["checksum"]
                and path.stat().st_size == entry["expected_bytes"],
                "verification_basis": "official_zenodo_record",
            }
        )
    source_path = RAW_DIR / "rdd2022" / "source.figshare.json"
    source = json.loads(require_local(source_path).read_text(encoding="utf-8"))
    for entry in source["files"]:
        path = RAW_DIR / "rdd2022" / entry["name"]
        released_path = DATASETS_DIR / "reports/rdd_archive_release.json"
        if (
            not path.exists()
            and entry["name"] == "RDD2022_released_through_CRDDC2022.zip"
            and released_path.exists()
        ):
            released = json.loads(
                require_local(released_path).read_text(encoding="utf-8")
            )
            if not (
                released.get("deleted")
                and released.get("passed")
                and released.get("extracted_files_crc_verified") == 85805
                and released.get("archive_official_md5") == entry["computed_md5"]
                and released.get("affected_files") == [relative_to_project(path)]
            ):
                raise ValueError("registro de liberação do ZIP inválido")
            checks.append(
                {
                    "path": relative_to_project(path),
                    "status": "authorized_archive_release",
                    "required_locally": False,
                    "passed": None,
                    "verification_basis": "historical_full_crc_validation_before_release",
                    "expected": entry["computed_md5"],
                    "algorithm": "md5",
                    "release_report": "datasets/reports/rdd_archive_release.json",
                }
            )
            print(
                entry["name"],
                "intentionally released; extracted data retained",
                flush=True,
            )
            continue
        status = (
            "missing"
            if not path.exists()
            else "cloud_only_not_read"
            if is_cloud_only(path)
            else "local"
        )
        actual = digest(path, "md5") if status == "local" else None
        checks.append(
            {
                "path": relative_to_project(path),
                "algorithm": "md5",
                "expected": entry["computed_md5"],
                "actual": actual,
                "passed": status == "local"
                and actual == entry["computed_md5"]
                and path.stat().st_size == entry["size"],
                "status": status,
                "official_download_url": entry["download_url"],
                "expected_bytes": entry["size"],
            }
        )
        print(entry["name"], checks[-1]["passed"], flush=True)
    # O ZIP externo contém sete ZIPs nacionais, não as imagens diretamente.
    # Usar a lista nominal oficial evita extrair ou materializar ZIPs internos.
    extracted = RAW_DIR / "rdd2022" / "RDD2022"
    expected = set()
    stack = []
    listing = RAW_DIR / "rdd2022" / "File_List_CRDDC_RDD2022.txt"
    for line in require_local(listing).read_text(encoding="utf-8-sig").splitlines():
        directory = re.match(r"^([| ]*)(?:\+---|\\---)(.+)$", line)
        if directory:
            depth = len(directory[1]) // 4
            stack = stack[:depth] + [directory[2]]
        elif stack:
            name = re.sub(r"^[| ]+", "", line).strip()
            if name:
                expected.add("RDD2022/" + "/".join(stack + [name]))
    actual = {
        p.relative_to(RAW_DIR / "rdd2022").as_posix(): p.stat().st_size
        for p in iter_files(extracted)
    }
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    wrong_size = []  # Lista nominal não publica tamanho individual.
    sources = list(
        csv.DictReader(
            require_local(DATASETS_DIR / "metadata" / "sources.csv").open(
                encoding="utf-8-sig", newline=""
            )
        )
    )
    for entry in sources:
        if entry["dataset_name"] not in ("univali_br", "urban_community"):
            continue
        folder = RAW_DIR / entry["dataset_name"]
        archives = sorted(folder.glob("*.zip"))
        for path in archives:
            actual_digest = digest(path, entry["checksum_algo"])
            checks.append(
                {
                    "path": relative_to_project(path),
                    "algorithm": entry["checksum_algo"],
                    "expected": entry["checksum_value"],
                    "actual": actual_digest,
                    "passed": actual_digest == entry["checksum_value"],
                    "verification_basis": "official_record_previously_registered"
                    if entry["dataset_name"] == "univali_br"
                    else "local_checksum_only_not_official",
                }
            )
    report = {
        "checksums": checks,
        "passed": all(c["passed"] for c in checks if c.get("required_locally", True))
        and not missing
        and not extra
        and not wrong_size,
        "rdd_archive_index": {
            "basis": "official_file_list_tree",
            "expected_files": len(expected),
            "actual_files": len(actual),
            "missing": missing,
            "extra": extra,
            "size_mismatches": None,
        },
        "raw_modified": False,
        "source_snapshot_sha256": file_sha256(source_path),
        "note": "ZIP não extraído nem recompresso. Lista nominal valida presença, não conteúdo. SHA256 e decodificação por imagem estão no inventário.",
    }
    write_json_report("source_integrity.json", report)
    print(
        {
            "passed": report["passed"],
            "expected_files": len(expected),
            "actual_files": len(actual),
            "missing": len(missing),
            "extra": len(extra),
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
