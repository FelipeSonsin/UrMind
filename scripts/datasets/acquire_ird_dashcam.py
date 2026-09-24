"""Acquire a bounded, source-verified IRD Dashcam candidate without storing its ZIP.

The selection uses only source IDs and annotations, never model output. It is
not an authorized evaluation split. The complete official ZIP is streamed and
MD5-checked while only selected, SHA256-checked original JPEG bytes are kept.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import struct
import urllib.request
import zipfile
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from _budget import preflight
from _core import DATASETS_DIR, file_sha256, measure_dir, write_json_report

SOURCE_URL = (
    "https://zenodo.org/records/21370736/files/"
    "IRD-Dataset_v1.0.0_images_Dashcam.zip?download=1"
)
SOURCE_MD5 = "eade1f27a43cff48276fb489b0572f7d"
SOURCE_BYTES = 2_505_474_989
RAW = DATASETS_DIR / "raw" / "ird_dashcam"
CHECKSUMS = RAW / "checksums_sha256.txt"
LABELS = RAW / "IRD-Dataset_v1.0.0_labels_bbox.zip"
GPS = RAW / "IRD-Dataset_v1.0.0_gps_metadata.zip"
CAP_BYTES = 7_000_000_000
BUFFER = 1 << 20


def _source_hashes() -> dict[str, str]:
    if file_sha256(RAW / "LICENSE.txt") != "fa5b81783ab6863597302242bf6be042513f6ccc25d1b0ba687e3b9f2b3576dc":
        raise ValueError("license diverges from published checksum")
    if file_sha256(RAW / "data.yaml") != "d225ac8c0a2dada4b2638f08c97f68ced2c6a3153e2dbd3b6d238035699d1f6e":
        raise ValueError("data.yaml diverges from published checksum")
    if hashlib.md5(CHECKSUMS.read_bytes()).hexdigest() != "9a7ad41cf135f80a7ac5e6fe10b9c1f0":
        raise ValueError("checksum list diverges from Zenodo record")
    if hashlib.md5(LABELS.read_bytes()).hexdigest() != "f4cf1fa9c98405cd3a58fcbd0aab3a8e":
        raise ValueError("label archive diverges from Zenodo record")
    if hashlib.md5(GPS.read_bytes()).hexdigest() != "5b2f22cee9cbdeb85b07be461200b4a1":
        raise ValueError("GPS archive diverges from Zenodo record")
    result = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        digest, path = line.split("  ", 1)
        result[path] = digest
    return result


def _selection(hashes: dict[str, str]) -> tuple[set[str], dict[str, list[int]], dict[str, str]]:
    labels: dict[str, list[int]] = {}
    with zipfile.ZipFile(LABELS) as archive:
        if archive.testzip() is not None:
            raise ValueError("label ZIP CRC failure")
        for index in range(1, 1007):
            name = f"Dash_{index:04d}"
            member = f"labels_bbox/{name}.txt"
            raw = archive.read(member)
            if hashlib.sha256(raw).hexdigest() != hashes.get(member):
                raise ValueError(f"label checksum mismatch: {member}")
            ids = [int(line.split()[0]) for line in raw.decode("utf-8").splitlines() if line.strip()]
            if any(value not in range(5) for value in ids):
                raise ValueError(f"unknown source class: {member}")
            labels[name] = ids
    with zipfile.ZipFile(GPS) as archive:
        if archive.testzip() is not None:
            raise ValueError("GPS ZIP CRC failure")
        member = "gps_metadata/IRD_Dataset_GPS_Metadata.csv"
        raw_gps = archive.read(member)
        if hashlib.sha256(raw_gps).hexdigest() != hashes.get(member):
            raise ValueError("GPS checksum mismatch")
        rows = list(csv.DictReader(io.StringIO(raw_gps.decode("utf-8-sig"))))
    gps = {row["image_name"].removesuffix(".jpg"): row for row in rows if row["source"] == "Dash"}
    if set(gps) != set(labels):
        raise ValueError("GPS/label membership mismatch")
    selected = {
        name
        for name, ids in labels.items()
        if (index := int(name.split("_")[1])) % 4 == 0
        or 2 in ids
        or 3 in ids
        or (not ids and index % 2 == 0)
    }
    return selected, labels, gps


class _RemoteZipIndex(io.RawIOBase):
    """Seekable metadata reader; selected images use one sequential full stream."""

    def __init__(self) -> None:
        self.position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self.position = (0 if whence == os.SEEK_SET else self.position if whence == os.SEEK_CUR else SOURCE_BYTES) + offset
        return self.position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = SOURCE_BYTES - self.position
        if size == 0 or self.position >= SOURCE_BYTES:
            return b""
        end = min(self.position + size, SOURCE_BYTES) - 1
        request = urllib.request.Request(SOURCE_URL, headers={"Range": f"bytes={self.position}-{end}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            expected = f"bytes {self.position}-{end}/{SOURCE_BYTES}"
            if response.status != 206 or response.headers.get("Content-Range") != expected:
                raise ValueError("source did not honor exact ZIP metadata range")
            data = response.read()
        if len(data) != end - self.position + 1:
            raise ValueError("incomplete ZIP metadata range")
        self.position += len(data)
        return data


class _Stream:
    def __init__(self, response) -> None:
        self.response = response
        self.position = 0
        self.md5 = hashlib.md5()  # Zenodo publishes MD5 for the complete archive.

    def read(self, size: int) -> bytes:
        data = self.response.read(size)
        if len(data) != size:
            raise OSError("official archive stream ended early")
        self.md5.update(data)
        self.position += size
        return data

    def skip_to(self, offset: int) -> None:
        if offset < self.position:
            raise ValueError("ZIP entries overlap or are unordered")
        while self.position < offset:
            self.read(min(BUFFER, offset - self.position))


def _extract_member(stream: _Stream, info: zipfile.ZipInfo, destination: Path, expected: str) -> None:
    header = stream.read(30)
    signature, _, flags, method, _, _, _, _, _, name_size, extra_size = struct.unpack(
        "<IHHHHHIIIHH", header
    )
    if signature != 0x04034B50 or method != zipfile.ZIP_DEFLATED or flags & 1:
        raise ValueError("unsupported or encrypted official ZIP entry")
    if stream.read(name_size).decode("utf-8") != info.filename:
        raise ValueError("ZIP entry name differs from central directory")
    stream.read(extra_size)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".jpg.part")
    digest = hashlib.sha256()
    crc = 0
    count = 0
    decompressor = zlib.decompressobj(-15)
    with partial.open("xb") as output:
        remaining = info.compress_size
        while remaining:
            data = decompressor.decompress(stream.read(min(BUFFER, remaining)))
            remaining -= min(BUFFER, remaining)
            output.write(data)
            digest.update(data)
            crc = zlib.crc32(data, crc)
            count += len(data)
        data = decompressor.flush()
        output.write(data)
        digest.update(data)
        crc = zlib.crc32(data, crc)
        count += len(data)
        output.flush()
        os.fsync(output.fileno())
    if count != info.file_size or crc != info.CRC or digest.hexdigest() != expected:
        raise ValueError(f"image integrity mismatch: {info.filename}")
    partial.replace(destination)


def acquire() -> dict:
    hashes = _source_hashes()
    selected, labels, gps = _selection(hashes)
    with zipfile.ZipFile(_RemoteZipIndex()) as archive:
        entries = sorted(archive.infolist(), key=lambda info: info.header_offset)
    images = {Path(info.filename).stem: info for info in entries if info.filename.endswith(".jpg")}
    if set(images) != set(labels) or len(images) != 1006:
        raise ValueError("Dashcam image index differs from official labels")
    expected_bytes = sum(images[name].file_size for name in selected)
    remaining_bytes = 0
    for name in selected:
        path = RAW / "images" / f"{name}.jpg"
        expected = hashes.get(f"images/{name}.jpg")
        if expected is None:
            raise ValueError(f"published image hash missing: {name}")
        if path.exists():
            if path.stat().st_size != images[name].file_size or file_sha256(path) != expected:
                raise ValueError(f"existing image differs: {path}")
        else:
            remaining_bytes += images[name].file_size
    if measure_dir(RAW).logical_size + remaining_bytes > CAP_BYTES:
        raise ValueError("IRD exceeds 7 GB dataset cap")
    preflight("IRD Dashcam candidate acquisition", remaining_bytes + 10_000_000, dataset_id="ird_dashcam", raise_on_block=True)
    print(f"CANDIDATE_HOLDOUT selected={len(selected)} bytes={expected_bytes} remaining={remaining_bytes}", flush=True)
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "UrMind-Dataset-Audit/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        if response.status != 200 or int(response.headers.get("Content-Length", -1)) != SOURCE_BYTES:
            raise ValueError("official archive size/status differs")
        stream = _Stream(response)
        for index, info in enumerate(entries):
            stream.skip_to(info.header_offset)
            name = Path(info.filename).stem
            if name in selected:
                path = RAW / info.filename
                expected = hashes.get(info.filename)
                if expected is None:
                    raise ValueError(f"published image hash missing: {info.filename}")
                if path.exists():
                    if file_sha256(path) != expected:
                        raise ValueError(f"existing image differs: {path}")
                    stream.skip_to(entries[index + 1].header_offset if index + 1 < len(entries) else SOURCE_BYTES)
                else:
                    _extract_member(stream, info, path, expected)
            if stream.position > SOURCE_BYTES:
                raise ValueError("archive exceeds published size")
        stream.skip_to(SOURCE_BYTES)
    if stream.md5.hexdigest() != SOURCE_MD5:
        raise ValueError("full official archive MD5 differs from Zenodo")
    counts = Counter(value for name in selected for value in labels[name])
    report = {
        "status": "CANDIDATE_HOLDOUT",
        "authorized_for_test": False,
        "source": "https://doi.org/10.5281/zenodo.21370736",
        "source_version": "1.0.0",
        "source_archive_url": SOURCE_URL,
        "source_archive_md5": SOURCE_MD5,
        "source_archive_bytes": SOURCE_BYTES,
        "selection_rule": "Dash index divisible by 4 OR label contains D20/D40 OR empty label with even index",
        "selected_images": len(selected),
        "selected_image_bytes": expected_bytes,
        "source_class_instances": {str(key): counts[key] for key in range(5)},
        "empty_labels": sum(not labels[name] for name in selected),
        "checksums_sha256": file_sha256(CHECKSUMS),
        "labels_zip_sha256": file_sha256(LABELS),
        "gps_zip_sha256": file_sha256(GPS),
        "images": [
            {
                "image": f"images/{name}.jpg",
                "sha256": hashes[f"images/{name}.jpg"],
                "bytes": images[name].file_size,
                "label": f"labels_bbox/{name}.txt",
                "source_class_ids": labels[name],
                "gps_image_name": gps[name]["image_name"],
            }
            for name in sorted(selected)
        ],
        "acquired_at": datetime.now(UTC).isoformat(),
    }
    path = write_json_report("ird_dashcam_candidate_acquisition.json", report)
    print(f"candidate_report={path}", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="actually acquire selected images")
    args = parser.parse_args()
    if args.run:
        acquire()
    else:
        hashes = _source_hashes()
        selected, labels, _ = _selection(hashes)
        counts = Counter(value for name in selected for value in labels[name])
        print(json.dumps({"status": "DRY_RUN", "selected": len(selected), "classes": counts}))


if __name__ == "__main__":
    main()
