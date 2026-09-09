"""Valida dados locais auxiliares; não baixa, converte, treina ou altera raw."""

import argparse
import csv
import json
import math
import shutil
import subprocess
from collections import Counter
from xml.etree import ElementTree as ET

from _budget import preflight
from _core import (
    DATASETS_DIR,
    RAW_DIR,
    configure_stdout,
    file_sha256,
    iter_files,
    measure_dir,
    relative_to_project,
    require_local,
    write_json_report,
)
from PIL import Image


def image_size(path):
    with Image.open(require_local(path)) as image:
        image.load()
        return image.size


def univali():
    files = list(iter_files(RAW_DIR / "univali_br" / "v1"))
    originals = [p for p in files if p.name.endswith("_RAW.jpg")]
    issues, masks = [], Counter()
    paired = set()
    for path in originals:
        try:
            size = image_size(path)
            for label in ("CRACK", "LANE", "POTHOLE"):
                mask = path.with_name(
                    path.name.removesuffix("_RAW.jpg") + f"_{label}.png"
                )
                paired.add(mask)
                if not mask.exists():
                    issues.append(
                        {"path": relative_to_project(mask), "issue": "missing_mask"}
                    )
                    continue
                if image_size(mask) != size:
                    issues.append(
                        {
                            "path": relative_to_project(mask),
                            "issue": "dimension_mismatch",
                        }
                    )
                masks[label] += 1
        except (OSError, ValueError) as exc:
            issues.append(
                {"path": relative_to_project(path), "issue": type(exc).__name__}
            )
    orphan = [
        relative_to_project(p)
        for p in files
        if p.suffix.lower() == ".png" and p not in paired
    ]
    return {
        "images": len(originals),
        "decoded_masks": dict(masks),
        "issues": issues,
        "orphan_masks": orphan,
        "format_valid": bool(originals) and not issues and not orphan,
        "use": "segmentation originals; derived bounding boxes not prepared",
        "note": "v1 is the preserved local folder name; upstream version is Mendeley v4",
    }


def urban():
    base = RAW_DIR / "urban_community" / "Data_sets" / "Data_sets"
    groups, issues = {}, []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        files = list(iter_files(folder))
        images = [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        labels = {p.stem: p for p in files if p.suffix.lower() == ".txt"}
        ids, matched = Counter(), set()
        empty, missing = 0, 0
        for path in images:
            try:
                image_size(path)
            except (OSError, ValueError) as exc:
                issues.append(
                    {"path": relative_to_project(path), "issue": type(exc).__name__}
                )
            label = labels.get(path.stem)
            if label is None:
                missing += 1
                continue
            matched.add(path.stem)
            lines = require_local(label).read_text(encoding="utf-8-sig").splitlines()
            empty += not any(line.strip() for line in lines)
            for number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    fields = line.split()
                    if len(fields) != 5:
                        raise ValueError("expected_five_columns")
                    class_id = int(fields[0])
                    x, y, w, h = map(float, fields[1:])
                    if class_id < 0 or not all(math.isfinite(v) for v in (x, y, w, h)):
                        raise ValueError("invalid_class_or_nonfinite")
                    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                        raise ValueError("invalid_normalized_coordinates")
                    if (
                        min(x - w / 2, y - h / 2) < -1e-6
                        or max(x + w / 2, y + h / 2) > 1 + 1e-6
                    ):
                        raise ValueError("box_outside_image")
                    ids[str(class_id)] += 1
                except ValueError as exc:
                    issues.append(
                        {
                            "path": relative_to_project(label),
                            "line": number,
                            "issue": str(exc),
                        }
                    )
        groups[folder.name] = {
            "images": len(images),
            "labels": len(labels),
            "missing_labels": missing,
            "empty_labels": empty,
            "valid_objects_by_original_id": dict(ids),
            "orphan_labels": sorted(set(labels) - matched),
        }
    return {
        "by_folder": groups,
        "issues": issues,
        "class_id_mapping": "inferred_not_official",
        "use": "quarantine from automatic training until class IDs and annotations reviewed",
        "format_valid": bool(groups)
        and not issues
        and all(
            not g["missing_labels"] and not g["orphan_labels"] for g in groups.values()
        ),
    }


def camber():
    base = RAW_DIR / "camber"
    csv_path = base / "detections" / "detections_50.csv"
    with require_local(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        lat, lon, timecode = map(
            float, (row["latitude"], row["longitude"], row["timecode_seconds"])
        )
        if not (
            -90 <= lat <= 90
            and -180 <= lon <= 180
            and math.isfinite(timecode)
            and timecode >= 0
        ):
            raise ValueError("invalid CAMBER coordinates/timecode")
    gpx = base / "routes" / "ride_1778861898555.gpx"
    track = ET.parse(require_local(gpx)).getroot().findall(".//{*}trkpt")
    for point in track:
        if not (
            -90 <= float(point.attrib["lat"]) <= 90
            and -180 <= float(point.attrib["lon"]) <= 180
        ):
            raise ValueError("invalid GPX coordinates")
    video = base / "videos" / "video_1778861897924_compressed.mp4"
    boxes = []
    with require_local(video).open("rb") as stream:
        total = video.stat().st_size
        while stream.tell() < total:
            start = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("truncated MP4 box")
            size = int.from_bytes(header[:4], "big")
            minimum = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise ValueError("truncated MP4 extended size")
                size, minimum = int.from_bytes(extended, "big"), 16
            elif size == 0:
                size = total - start
            if size < minimum or start + size > total:
                raise ValueError("invalid MP4 box size")
            boxes.append(header[4:8].decode("ascii", errors="replace"))
            stream.seek(start + size)
    return {
        "detections": len(rows),
        "human_confirmed": sum(bool(r["user_confirmed"].strip()) for r in rows),
        "gpx_trackpoints": len(track),
        "mp4_top_level_boxes": boxes,
        "container_valid": all(t in boxes for t in ("ftyp", "moov", "mdat")),
        "video_frames_decoded": False,
        "local_checksums": [
            {"path": relative_to_project(p), "sha256": file_sha256(p)}
            for p in (csv_path, gpx, video)
        ],
        "use": "historical/georeferenced model outputs; not human ground truth",
    }


def decode_video():
    path = require_local(RAW_DIR / "camber/videos/video_1778861897924_compressed.mp4")
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg não disponível; nenhuma instalação automática")
    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    report = {
        "path": relative_to_project(path),
        "sha256": file_sha256(path),
        "decoder": "ffmpeg",
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "scope": "full video decoding only; no inference or stored frames",
    }
    # Não persistir stderr que possa conter caminho absoluto do processo.
    if result.stderr:
        report["diagnostics"] = result.stderr.replace(
            str(path), relative_to_project(path)
        )
    write_json_report("camber_video_decode.json", report)
    if result.returncode:
        raise RuntimeError("falha na decodificação; consulte camber_video_decode.json")
    print("CAMBER video decode passed")


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video-only",
        action="store_true",
        help="decodifica vídeo via ffmpeg já instalado",
    )
    args = parser.parse_args()
    preflight(
        "auditoria complementar de dados reais",
        4_000_000,
        8_000_000,
        raise_on_block=True,
    )
    if args.video_only:
        decode_video()
        return
    result = {
        "version": 1,
        "scope": "local data and formats, not model performance",
        "raw_modified": False,
    }
    for name, function in (
        ("univali_br", univali),
        ("urban_community", urban),
        ("camber", camber),
    ):
        result[name] = function()
        print(name, "audit complete", flush=True)
    absent = {}
    for name in ("project_sidewalk", "rampnet", "bdd100k", "mapillary_msls"):
        paths = list(iter_files(RAW_DIR / name))
        content = [
            relative_to_project(p) for p in paths if p.name.lower() != "readme.md"
        ]
        absent[name] = {
            "files": [relative_to_project(p) for p in paths],
            "non_readme_files": content,
            "status": "requires_review" if content else "no_dataset_data",
        }
    result["unacquired_sources"] = absent
    result["local_sizes"] = {
        p.name: measure_dir(p).as_dict()
        for p in sorted(RAW_DIR.iterdir())
        if p.is_dir()
    }
    result["rdd_audit_sha256"] = file_sha256(
        DATASETS_DIR / "reports/rdd2022_audit.json"
    )
    result["all_requested_datasets_present"] = False
    result["identification_runtime_verified"] = False
    write_json_report("dataset_readiness.json", result)
    print(
        json.dumps(
            {
                "report": "datasets/reports/dataset_readiness.json",
                "univali_format_valid": result["univali_br"]["format_valid"],
                "urban_format_valid": result["urban_community"]["format_valid"],
                "camber_container_valid": result["camber"]["container_valid"],
            }
        )
    )


if __name__ == "__main__":
    main()
