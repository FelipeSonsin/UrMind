"""Auditoria RDD2022 v2: raw somente leitura; inventário verificável por imagem."""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime

from _budget import preflight
from _core import (
    DATASETS_DIR,
    RAW_DIR,
    configure_stdout,
    file_sha256,
    is_cloud_only,
    iter_files,
    measure_dir,
    relative_to_project,
    require_local,
    write_json_report,
    write_text_safe,
)
from _taxonomy import load_class_mapping

RDD_ROOT = RAW_DIR / "rdd2022" / "RDD2022"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
INVENTORY = DATASETS_DIR / "manifests" / "rdd2022_inventory.jsonl"


def inspect_xml(path, mapping):
    result = {
        "annotation_path": relative_to_project(path),
        "annotation_sha256": None,
        "original_classes": {},
        "classes": {},
        "n_objects_original": 0,
        "n_objects": 0,
        "annotation_errors": [],
        "width": None,
        "height": None,
        "filename": None,
        "difficult_objects": 0,
        "small_objects": 0,
    }
    if is_cloud_only(path):
        result["annotation_errors"].append("cloud_only_not_read")
        return result
    result["annotation_sha256"] = file_sha256(path)
    try:
        root = ET.parse(require_local(path)).getroot()
    except ET.ParseError:
        result["annotation_errors"].append("malformed_xml")
        return result
    if root.tag != "annotation":
        result["annotation_errors"].append("invalid_root")
    result["filename"] = root.findtext("filename")
    try:
        width, height = [
            float(root.findtext("size/" + key, "nan")) for key in ("width", "height")
        ]
        if not all(
            math.isfinite(v) and v > 0 and v.is_integer() for v in (width, height)
        ):
            raise ValueError()
        result["width"], result["height"] = int(width), int(height)
    except ValueError:
        width = height = 0
        result["annotation_errors"].append("invalid_dimensions")
    originals, canonical = Counter(), Counter()
    for index, obj in enumerate(root.findall("object")):
        label = (obj.findtext("name") or "").strip()
        originals[label] += 1
        if label not in mapping["accepted"] and label not in mapping["rejected"]:
            result["annotation_errors"].append(f"object_{index}:unmapped_label:{label}")
        if label == "D0w0":
            result["annotation_errors"].append(f"object_{index}:ambiguous_label:D0w0")
        try:
            box = [
                float(obj.findtext("bndbox/" + key, "nan"))
                for key in ("xmin", "ymin", "xmax", "ymax")
            ]
            x0, y0, x1, y1 = box
            if not all(math.isfinite(v) for v in box):
                raise ValueError()
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError()
        except ValueError:
            result["annotation_errors"].append(f"object_{index}:invalid_bounding_box")
            continue
        mapped = mapping["accepted"].get(label)
        if mapped:
            canonical[mapped] += 1
            result["small_objects"] += int(
                (x1 - x0) * (y1 - y0) / (width * height) < 0.01
            )
            result["difficult_objects"] += int(obj.findtext("difficult", "0") == "1")
    result.update(
        original_classes=dict(sorted(originals.items())),
        classes=dict(sorted(canonical.items())),
        n_objects_original=sum(originals.values()),
        n_objects=sum(canonical.values()),
    )
    return result


def inspect_image(path):
    from PIL import Image, ImageStat

    result = {"sha256": None, "image_errors": [], "dhash128": None}
    if is_cloud_only(path):
        result["image_errors"].append("cloud_only_not_read")
        return result
    result["sha256"] = file_sha256(path)
    try:
        with Image.open(require_local(path)) as im:
            im.load()  # Decodificação completa; detecta truncamento, sem alterar o original.
            result["image_width"], result["image_height"] = im.size
            gray = im.convert("L")
            stat = ImageStat.Stat(gray)
            result["gray_stddev"] = round(stat.stddev[0], 4)
            horizontal = list(gray.resize((9, 8), Image.Resampling.BILINEAR).getdata())
            vertical = list(gray.resize((8, 9), Image.Resampling.BILINEAR).getdata())
            bits = 0
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | int(
                        horizontal[y * 9 + x] > horizontal[y * 9 + x + 1]
                    )
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | int(
                        vertical[y * 8 + x] > vertical[(y + 1) * 8 + x]
                    )
            result["dhash128"] = f"{bits:032x}"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        result["image_errors"].append(type(exc).__name__)
    return result


def summarize(rows):
    classes, images, originals = Counter(), Counter(), Counter()
    for row in rows:
        classes.update(row.get("classes", {}))
        images.update(row.get("classes", {}).keys())
        originals.update(row.get("original_classes", {}))
    total = sum(classes.values())
    return {
        "images": len(rows),
        "objects": total,
        "class_counts": dict(sorted(classes.items())),
        "images_per_class": dict(sorted(images.items())),
        "original_class_counts": dict(sorted(originals.items())),
        "class_distribution_pct": {
            k: round(100 * v / total, 4) for k, v in sorted(classes.items())
        }
        if total
        else {},
        "negative_images": sum(r.get("is_negative", False) for r in rows),
        "out_of_scope_only_images": sum(
            r.get("annotation_status") == "out_of_scope_only" for r in rows
        ),
        "invalid_annotations": sum(bool(r.get("annotation_errors")) for r in rows),
        "invalid_images": sum(bool(r.get("image_errors")) for r in rows),
        "images_without_annotation": sum(not r.get("annotation_path") for r in rows),
        "size_bytes": sum(r["size_bytes"] for r in rows),
    }


def read_inventory():
    return [
        json.loads(line)
        for line in require_local(INVENTORY)
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line
    ]


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    if not RDD_ROOT.is_dir():
        raise RuntimeError("RDD2022 extraído ausente")
    budget = preflight(
        "auditoria + inventário RDD2022 (limite conservador do lote)",
        128_000_000,
        256_000_000,
        raise_on_block=True,
    )
    print(budget.render(), flush=True)
    mapping = load_class_mapping("rdd2022")
    rows, orphan_annotations = [], []
    country_storage = {}
    for country in sorted(p for p in RDD_ROOT.iterdir() if p.is_dir()):
        country_storage[country.name] = measure_dir(country).as_dict()
        for split in ("train", "test"):
            image_dir = country / split / "images"
            annotation_dir = country / split / "annotations" / "xmls"
            xmls = (
                {p.stem: p for p in iter_files(annotation_dir, {".xml"})}
                if annotation_dir.exists()
                else {}
            )
            used_xml = set()
            for path in (
                iter_files(image_dir, IMAGE_SUFFIXES) if image_dir.exists() else []
            ):
                row = {
                    "rel_path": relative_to_project(path),
                    "country": country.name,
                    "geographic_country": "China"
                    if country.name.startswith("China_")
                    else country.name,
                    "source_split": split,
                    "group": "country:"
                    + ("China" if country.name.startswith("China_") else country.name),
                    "group_basis": "country; route/session unavailable; conservative domain holdout",
                    "size_bytes": path.stat().st_size,
                    "annotation_path": None,
                    "classes": {},
                    "original_classes": {},
                    "n_objects": 0,
                    "n_objects_original": None,
                    "annotation_errors": [],
                    "is_negative": False,
                }
                xml = xmls.get(path.stem)
                if xml:
                    used_xml.add(path.stem)
                    row.update(inspect_xml(xml, mapping))
                    if row["filename"] and row["filename"] != path.name:
                        row["annotation_errors"].append("filename_does_not_match_image")
                row.update(inspect_image(path))
                if (
                    xml
                    and row.get("width")
                    and row.get("image_width")
                    and (row["width"], row["height"])
                    != (
                        row["image_width"],
                        row["image_height"],
                    )
                ):
                    row["annotation_errors"].append("xml_image_dimensions_mismatch")
                row["is_negative"] = bool(
                    xml
                    and not row["annotation_errors"]
                    and row["n_objects_original"] == 0
                )
                row["annotation_status"] = (
                    "missing"
                    if not xml
                    else "invalid"
                    if row["annotation_errors"]
                    else "negative"
                    if row["is_negative"]
                    else "positive"
                    if row["n_objects"]
                    else "out_of_scope_only"
                )
                rows.append(row)
                if len(rows) % 2000 == 0:
                    print(f"{len(rows)} imagens auditadas", flush=True)
            for stem in sorted(set(xmls) - used_xml):
                orphan_annotations.append(inspect_xml(xmls[stem], mapping))
        print(f"{country.name}: concluído", flush=True)
    rows.sort(key=lambda r: r["rel_path"])
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "algorithm_version": 2,
        "inventory": relative_to_project(INVENTORY),
        "totals": summarize(rows),
        "by_country": {
            name: {
                **summarize([r for r in rows if r["country"] == name]),
                "storage": storage,
            }
            for name, storage in country_storage.items()
        },
        "by_source_split": {
            s: summarize([r for r in rows if r["source_split"] == s])
            for s in ("train", "test")
        },
        "missing_image_files": len(orphan_annotations),
        "orphan_annotations": orphan_annotations,
        "definitions": {
            "negative": "XML válido com zero objetos originais; nunca UNKNOWN",
            "out_of_scope_only": "objetos originais presentes, nenhum objeto V1 válido; não negativo",
            "invalid_annotations": "quantidade de XMLs associados a imagens com ao menos um erro",
            "objects": "caixas V1 geometricamente válidas; imagem pode exigir revisão por outro objeto",
            "unlabeled_test": "test oficial sem XML não é automaticamente arquivo perdido",
        },
        "preflight": budget.as_dict(),
    }
    if not args.no_report:
        write_text_safe(
            INVENTORY,
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
        )
        report["inventory_sha256"] = file_sha256(INVENTORY)
        write_json_report("rdd2022_audit.json", report)
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
