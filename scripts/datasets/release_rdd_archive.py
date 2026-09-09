"""Libera somente o ZIP RDD redundante após comparar cada arquivo extraído ao CRC oficial.

Dry-run padrão. --execute usa a autorização de limpeza de 2026-09-08.
Não remove imagens, annotations, diretórios nem arquivos externos.
"""

import argparse
import hashlib
import json
import zipfile
import zlib
from pathlib import PurePosixPath

from _core import (
    RAW_DIR,
    assert_inside_project,
    configure_stdout,
    iter_files,
    relative_to_project,
    require_local,
    write_json_report,
)


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    archive = require_local(RAW_DIR / "rdd2022/RDD2022_released_through_CRDDC2022.zip")
    extracted = require_local(RAW_DIR / "rdd2022/RDD2022")
    before = archive.stat()
    report = {
        "authorization": "user_cleanup_if_needed_20260908",
        "mode": "execute" if args.execute else "dry-run",
        "affected_files": [relative_to_project(archive)],
        "count": 1,
        "source": relative_to_project(archive),
        "destination": None,
        "bytes_to_release": before.st_size,
        "deleted": False,
    }
    print(json.dumps(report, indent=2), flush=True)
    md5 = hashlib.md5()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            md5.update(chunk)
    if (
        before.st_size != 13264172619
        or md5.hexdigest() != "b62bd51d2ffcfaa76c60f234f0cc2bb3"
    ):
        raise ValueError("ZIP diferente da versão oficial: remoção bloqueada")
    checked = set()
    with zipfile.ZipFile(archive) as outer:
        for package in outer.infolist():
            if not package.filename.endswith(".zip"):
                raise ValueError("entrada externa inesperada")
            with outer.open(package) as stream, zipfile.ZipFile(stream) as inner:
                for entry in inner.infolist():
                    if entry.is_dir():
                        continue
                    name = PurePosixPath(entry.filename)
                    if (
                        name.is_absolute()
                        or ".." in name.parts
                        or ":" in entry.filename
                        or "\\" in entry.filename
                    ):
                        raise ValueError("caminho ZIP não seguro")
                    path = require_local(extracted.joinpath(*name.parts))
                    if path.stat().st_size != entry.file_size:
                        raise ValueError(f"tamanho divergente: {entry.filename}")
                    crc = 0
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            crc = zlib.crc32(chunk, crc)
                    if crc != entry.CRC:
                        raise ValueError(f"CRC divergente: {entry.filename}")
                    if entry.filename in checked:
                        raise ValueError("nome repetido no arquivo ZIP")
                    checked.add(entry.filename)
            print(package.filename, "verified", len(checked), flush=True)
    actual = {p.relative_to(extracted).as_posix() for p in iter_files(extracted)}
    if checked != actual or len(checked) != 85805:
        raise ValueError("inventário extraído incompleto ou diferente; não apagar")
    report.update(
        extracted_files_crc_verified=len(checked),
        archive_official_md5=md5.hexdigest(),
        passed=True,
    )
    write_json_report("rdd_archive_release.json", report)
    if args.execute:
        # Caminho fixo e canônico conferido imediatamente antes da única exclusão.
        resolved = assert_inside_project(archive)
        if resolved != RAW_DIR / "rdd2022/RDD2022_released_through_CRDDC2022.zip":
            raise ValueError("alvo de exclusão inesperado")
        now = require_local(resolved).stat()
        if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise ValueError("arquivo mudou durante a validação")
        resolved.unlink()
        report["deleted"] = True
        write_json_report("rdd_archive_release.json", report)
        print(
            "Removed only verified redundant RDD ZIP; extracted files retained",
            flush=True,
        )


if __name__ == "__main__":
    main()
