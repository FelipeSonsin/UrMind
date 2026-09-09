"""Baixa somente arquivos registrados: dry-run padrão, limite global e SHA-256."""

import argparse
import hashlib
import http.client
import json
import os
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from _budget import preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    RAW_DIR,
    assert_inside_project,
    configure_stdout,
    file_sha256,
    free_disk_bytes,
    require_local,
    write_json_report,
)

PLAN = DATASETS_DIR / "metadata/acquisition_plan.json"


def download_ranges(entry, resume, workers=4):
    if not 1 <= workers <= 16:
        raise ValueError("workers deve estar entre 1 e 16")
    destination = target(entry["path"])
    temporary = target(entry["path"] + ".part")
    if temporary.exists() and not resume:
        raise ValueError("parcial presente; use --resume para mesma versão fixada")
    digest, received = hashlib.sha256(), 0
    if temporary.exists():
        with require_local(temporary).open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                received += len(chunk)
                digest.update(chunk)
    if received > entry["size_bytes"]:
        raise ValueError("parcial maior que arquivo esperado")
    remaining = entry["size_bytes"] - received
    preflight(
        "transferência por blocos",
        remaining + 8192,
        remaining + 8192,
        raise_on_block=True,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    chunk_size = 4 * 1024 * 1024

    def fetch(start):
        end = min(start + chunk_size, entry["size_bytes"]) - 1
        expected = f"bytes {start}-{end}/{entry['size_bytes']}"
        for attempt in range(5):
            try:
                request = urllib.request.Request(
                    entry["url"],
                    headers={
                        "Range": f"bytes={start}-{end}",
                        "User-Agent": "UrMind-Dataset-Audit/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    if (
                        response.status != 206
                        or response.headers.get("Content-Range") != expected
                    ):
                        raise ValueError("servidor não confirmou o intervalo exato")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise OSError("bloco interrompido")
                    return data
            except (OSError, TimeoutError, http.client.IncompleteRead) as exc:
                print(
                    "RETRY",
                    destination.name,
                    start,
                    attempt + 1,
                    type(exc).__name__,
                    flush=True,
                )
                if attempt == 4:
                    raise
                time.sleep(min(2**attempt, 8))

    offsets = iter(range(received, entry["size_bytes"], chunk_size))
    progress = time.monotonic()
    with (
        ThreadPoolExecutor(max_workers=workers) as pool,
        temporary.open("ab" if temporary.exists() else "xb") as output,
    ):
        pending = []
        for _ in range(workers):
            offset = next(offsets, None)
            if offset is not None:
                pending.append(pool.submit(fetch, offset))
        while pending:
            data = pending.pop(0).result()
            if free_disk_bytes() - len(data) < 10_000_000_000:
                raise ValueError("reserva de disco insuficiente")
            output.write(data)
            digest.update(data)
            received += len(data)
            offset = next(offsets, None)
            if offset is not None:
                pending.append(pool.submit(fetch, offset))
            if time.monotonic() - progress > 15:
                print(
                    entry["dataset"],
                    destination.name,
                    received,
                    "/",
                    entry["size_bytes"],
                    flush=True,
                )
                progress = time.monotonic()
        output.flush()
        os.fsync(output.fileno())
    if received != entry["size_bytes"] or (
        entry.get("sha256") and digest.hexdigest() != entry["sha256"]
    ):
        raise ValueError("SHA/tamanho final diverge; parcial preservado")
    verification = "downloaded_sha256_verified"
    if not entry.get("sha256"):
        if entry.get("verification") != "official_bdd_zip_crc_local_sha256":
            raise ValueError("checksum oficial ausente sem protocolo registrado")
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("CRC do ZIP diverge; parcial preservado")
        verification = "downloaded_zip_crc_verified_local_sha256_recorded"
    if destination.exists():
        raise FileExistsError(entry["path"])
    temporary.rename(destination)
    return {
        "path": entry["path"],
        "status": verification,
        "sha256": digest.hexdigest(),
        "size_bytes": received,
        "transport": f"bounded_http_ranges_4MiB_{workers}workers",
    }


def download_hub(entry):
    cache = assert_inside_project(DATASETS_DIR / "downloads/hf-cache")
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_XET_CACHE"] = str(cache / "xet")
    os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
    os.environ["HF_XET_SHARD_CACHE_SIZE_LIMIT"] = "0"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
    from huggingface_hub import hf_hub_download

    pieces = urlsplit(entry["url"]).path.strip("/").split("/")
    if (
        len(pieces) < 6
        or pieces[0] != "datasets"
        or pieces[3] != "resolve"
        or len(pieces[4]) != 40
    ):
        raise ValueError("URL Hub precisa de revisão imutável")
    local_dir = target(
        f"datasets/raw/{entry['dataset']}/acquired_20260908/placeholder"
    ).parent
    preflight(
        "arquivo Hub sem cache de conteúdo",
        entry["size_bytes"] + 100_000_000,
        entry["size_bytes"] + 100_000_000,
        raise_on_block=True,
    )
    downloaded = hf_hub_download(
        repo_id="/".join(pieces[1:3]),
        repo_type="dataset",
        revision=pieces[4],
        filename="/".join(pieces[5:]),
        local_dir=local_dir,
        token=False,
    )
    destination = target(entry["path"])
    if (
        Path(downloaded).resolve() != destination
        or destination.stat().st_size != entry["size_bytes"]
    ):
        raise ValueError("destino/tamanho inesperado do Hub")
    actual = file_sha256(destination)
    if actual != entry["sha256"]:
        raise ValueError("SHA-256 oficial não confere")
    return {
        "path": entry["path"],
        "status": "downloaded_sha256_verified",
        "sha256": actual,
        "size_bytes": entry["size_bytes"],
        "transport": "huggingface_hub_xet",
    }


def target(relative):
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in relative or ":" in relative:
        raise ValueError("destino deve ser relativo à raiz")
    path = PROJECT_ROOT / p
    for parent in (path, *path.parents):
        if parent == PROJECT_ROOT:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("symlink/junction recusado")
    path = assert_inside_project(path)
    if RAW_DIR not in path.parents or "acquired_20260908" not in path.parts:
        raise ValueError("somente área de aquisição nova autorizada")
    return path


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dataset", choices=("project_sidewalk", "rampnet"))
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=4)
    parser.add_argument(
        "--transport", choices=("http", "hub", "ranges"), default="ranges"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="retoma .part da mesma versão imutável; valida SHA completo",
    )
    args = parser.parse_args()
    plan = json.loads(require_local(PLAN).read_text(encoding="utf-8"))
    if plan["authorization"] != "user_explicit_download_20260908":
        raise ValueError("plano sem autorização registrada")
    entries = plan["files"]
    if args.dataset:
        entries = [e for e in entries if e["dataset"] == args.dataset]
    pending = []
    for entry in entries:
        p = target(entry["path"])
        if p.exists():
            if (
                p.stat().st_size != entry["size_bytes"]
                or file_sha256(p) != entry["sha256"]
            ):
                raise ValueError(f"arquivo existente diferente: {entry['path']}")
        else:
            pending.append(entry)
    total = 0
    for entry in pending:
        part = target(entry["path"] + ".part")
        partial_size = require_local(part).stat().st_size if part.exists() else 0
        if partial_size > entry["size_bytes"]:
            raise ValueError("parcial maior que o arquivo registrado")
        total += entry["size_bytes"] - (
            partial_size if args.transport in ("http", "ranges") else 0
        )
    concurrent_remaining = 0
    concurrent_entries = [e for e in plan["files"] if e not in entries]
    bdd_plan = DATASETS_DIR / "metadata/bdd100k_acquisition_plan.json"
    if bdd_plan.exists():
        concurrent_entries += json.loads(
            require_local(bdd_plan).read_text(encoding="utf-8")
        )["files"]
    for entry in concurrent_entries:
        final = target(entry["path"])
        if not final.exists():
            part = target(entry["path"] + ".part")
            present = require_local(part).stat().st_size if part.exists() else 0
            concurrent_remaining += max(0, entry["size_bytes"] - present)
    peak = total + concurrent_remaining + 100_000_000
    budget = preflight("aquisição oficial registrada", peak, peak, raise_on_block=True)
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry-run",
                "files": len(pending),
                "download_bytes": total,
                "other_registered_pending_bytes": concurrent_remaining,
                "peak_bytes": peak,
                "budget": budget.as_dict(),
                "affected": [e["path"] for e in pending],
            },
            indent=2,
        ),
        flush=True,
    )
    if not args.execute:
        return
    completed = []
    for entry in entries:
        destination = target(entry["path"])
        if destination.exists():
            completed.append({"path": entry["path"], "status": "already_verified"})
            continue
        if (
            urlsplit(entry["url"]).scheme != "https"
            or urlsplit(entry["url"]).hostname != "huggingface.co"
        ):
            raise ValueError("origem fora do host oficial registrado")
        if args.transport == "hub":
            completed.append(download_hub(entry))
            print("VERIFIED", entry["path"], flush=True)
            continue
        if args.transport == "ranges":
            completed.append(download_ranges(entry, args.resume, args.workers))
            print("VERIFIED", entry["path"], flush=True)
            continue
        temporary = target(entry["path"] + ".part")
        if temporary.exists() and not args.resume:
            raise ValueError("download parcial presente; revisar antes de retomar")
        digest, received = hashlib.sha256(), 0
        if temporary.exists():
            with require_local(temporary).open("rb") as partial:
                for chunk in iter(lambda: partial.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
                    received += len(chunk)
        preflight(
            "arquivo de aquisição",
            entry["size_bytes"] - received + 8192,
            entry["size_bytes"] - received + 8192,
            raise_on_block=True,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "UrMind-Dataset-Audit/1.0"}
        if received:
            headers["Range"] = f"bytes={received}-"
        request = urllib.request.Request(entry["url"], headers=headers)
        progress = time.monotonic()
        with urllib.request.urlopen(request, timeout=60) as response:
            if received and (
                response.status != 206
                or not response.headers.get("Content-Range", "").startswith(
                    f"bytes {received}-"
                )
            ):
                raise ValueError("servidor não confirmou retomada por Range")
            length = response.headers.get("Content-Length")
            if length and int(length) != entry["size_bytes"] - received:
                raise ValueError("tamanho remoto diverge do registro")
            with temporary.open("ab" if temporary.exists() else "xb") as output:
                while chunk := response.read(4 * 1024 * 1024):
                    if received + len(chunk) > entry["size_bytes"]:
                        raise ValueError("download excede tamanho autorizado")
                    if free_disk_bytes() - len(chunk) < 10_000_000_000:
                        raise ValueError("reserva de disco insuficiente")
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if time.monotonic() - progress > 15:
                        print(
                            entry["dataset"],
                            destination.name,
                            received,
                            "bytes",
                            flush=True,
                        )
                        progress = time.monotonic()
                output.flush()
                os.fsync(output.fileno())
        if received != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError(
                "integridade do download falhou; parcial preservado para revisão"
            )
        if destination.exists():
            raise FileExistsError(entry["path"])
        temporary.rename(destination)
        completed.append(
            {
                "path": entry["path"],
                "status": "downloaded_sha256_verified",
                "size_bytes": received,
                "sha256": digest.hexdigest(),
            }
        )
        print("VERIFIED", entry["path"], flush=True)
    write_json_report(
        f"acquisition_result_{args.dataset or 'all'}.json",
        {
            "passed": True,
            "scope_dataset": args.dataset or "all_registered_files",
            "files": completed,
            "plan_sha256": file_sha256(PLAN),
            "existing_raw_modified": False,
        },
    )


if __name__ == "__main__":
    main()
