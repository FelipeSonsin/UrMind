"""Consent-first reference calibration. Never trains or reads scientific images.

Default: validate only. --run explicitly enables reference inference on the
validated external corpus. --activate additionally requires successful metrics,
human inspection of temporary blurred derivatives and a DEV activation AuditLog.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import re
import sys
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

LABELS = {"street_positive", "scene_negative", "face_large", "face_small"}
NEGATIVES = {"indoor", "selfie", "document", "screenshot", "food", "pet", "sky", "other"}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_corpus(directory: Path, *, root: Path = ROOT) -> list[dict[str, Any]]:
    directory = directory.resolve(strict=True)
    if directory.is_relative_to(root.resolve()) or root.resolve().is_relative_to(directory):
        raise ValueError("Calibration directory must be outside the repository")
    index = root / "datasets/manifests"
    if not index.is_dir():
        raise ValueError("Scientific manifest index unavailable")
    scientific: set[str] = set()
    # Bounded manifest-only scan: never traverse raw images, models or evaluation data.
    for path in sorted(index.iterdir()):
        if path.suffix not in {".json", ".jsonl", ".csv"} or not path.is_file():
            continue
        if getattr(path.stat(), "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
            raise ValueError("Scientific manifest is cloud-only; hydrate before calibration")
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                scientific.update(
                    re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", line.lower())
                )
    if not scientific:
        raise ValueError("Scientific hash index is empty")
    records = []
    seen: set[str] = set()
    with (directory / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"file", "label", "subtype", "author", "license_or_consent", "notes"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Invalid manifest columns")
        for number, row in enumerate(reader, 2):
            if (
                row["label"] not in LABELS
                or not row["author"].strip()
                or not row["license_or_consent"].strip()
            ):
                raise ValueError(f"Missing provenance or invalid label at manifest row {number}")
            if row["label"] == "scene_negative" and row["subtype"] not in NEGATIVES:
                raise ValueError(f"Invalid negative subtype at manifest row {number}")
            path = (directory / row["file"]).resolve(strict=True)
            if (
                not path.is_relative_to(directory)
                or not path.is_file()
                or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
            ):
                raise ValueError(f"Invalid image path at manifest row {number}")
            if getattr(path.stat(), "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
                raise ValueError("Calibration photo is cloud-only")
            checksum = digest(path)
            if checksum in seen or checksum in scientific:
                raise ValueError(f"Duplicate/scientific image at manifest row {number}")
            from app.services.storage import MAX_BYTES, validate_image

            if path.stat().st_size > MAX_BYTES:
                raise ValueError(f"Image too large at manifest row {number}")
            validate_image(path.read_bytes())
            seen.add(checksum)
            # Author, filename and consent text never enter the versioned report.
            records.append(
                {"path": path, "sha256": checksum, "label": row["label"], "subtype": row["subtype"]}
            )
    if not records:
        raise ValueError("Empty calibration manifest")
    return records


def threshold_curve(records: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [float(row["margin"]) for row in records if row["label"] == "street_positive"]
    negative = [float(row["margin"]) for row in records if row["label"] == "scene_negative"]
    if not positive or not negative or not all(math.isfinite(x) for x in positive + negative):
        raise ValueError("Both scene cohorts and finite scores are required")
    values = sorted(set(positive + negative))
    thresholds = [
        math.nextafter(values[0], -math.inf),
        *values,
        math.nextafter(values[-1], math.inf),
    ]
    curve = [
        {
            "threshold": t,
            "false_rejection": sum(x < t for x in positive) / len(positive),
            "false_acceptance": sum(x >= t for x in negative) / len(negative),
        }
        for t in thresholds
    ]
    eligible = [
        point
        for point in curve
        if point["false_rejection"] <= 0.05 and point["false_acceptance"] <= 0.15
    ]
    best = min(
        eligible or curve,
        key=lambda point: (
            point["false_rejection"] + point["false_acceptance"],
            point["false_rejection"],
        ),
    )
    return {
        "curve": curve,
        "selected": best,
        "meets_targets": bool(eligible),
        "positive_count": len(positive),
        "negative_count": len(negative),
    }


def face_metrics(records: list[dict[str, Any]], ratio: float) -> dict[str, float | None]:
    groups = {label: [row for row in records if row["label"] == label] for label in LABELS}
    controls = [row for row in groups["street_positive"] if row.get("subtype") == "no_faces"]
    return {
        "face_large_recall": sum(row["face_area"] > ratio for row in groups["face_large"])
        / len(groups["face_large"])
        if groups["face_large"]
        else None,
        "face_small_recall": sum(
            row["face_count"] > 0 and row["face_area"] <= ratio for row in groups["face_small"]
        )
        / len(groups["face_small"])
        if groups["face_small"]
        else None,
        "face_false_positive": sum(row["face_count"] > 0 for row in controls) / len(controls)
        if len(controls) >= 20
        else None,
    }


async def activate(document: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import urlparse

    from dotenv import load_dotenv

    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import DecisionRepository
    from app.schemas.core import PhotoGatePolicy
    from app.services.photo_reference import CALIBRATION, calibration_matches

    if CALIBRATION.exists():
        raise ValueError("Existing calibration must be reviewed explicitly; no automatic overwrite")
    load_dotenv(ROOT / "backend/.env", override=False)
    get_settings.cache_clear()
    settings = get_settings()
    if urlparse(
        settings.supabase_url or ""
    ).hostname != "impmeitwtusjtwjouggy.supabase.co" or "impmeitwtusjtwjouggy" not in (
        settings.database_pooler_url or ""
    ):
        raise ValueError("Calibration activation restricted to Urmind DEV")
    database = Database(settings)
    try:
        async with database.session() as session:
            repository = DecisionRepository(session)
            previous = await repository.photo_gate_policy(lock=True)
            policy = PhotoGatePolicy.model_validate(
                previous.model_dump()
                | {
                    key: document[key]
                    for key in ("scene_accept_margin", "scene_reject_margin", "dominant_face_ratio")
                }
            )
            candidate = document | {"activation_audit_id": str(uuid.uuid4())}
            if not calibration_matches(
                candidate,
                scene_sha=document["scene_sha256"],
                face_sha=document["face_sha256"],
                policy=policy,
            ):
                raise ValueError("Calibration does not meet activation gates")
            audit = await repository.add_audit(
                operation="photo_gate_calibration_activation",
                entity_type="operational_configuration",
                entity_id=uuid.uuid5(uuid.NAMESPACE_URL, "urmind:photo-gate-calibration"),
                actor="operator:calibration-cli",
                before={},
                after=document,
                event_hash=hashlib.sha256(
                    json.dumps(document, sort_keys=True).encode()
                ).hexdigest(),
            )
            candidate["activation_audit_id"] = str(audit.id)
            await repository.save_photo_gate_policy(policy)
        # A write failure leaves inference uncalibrated; never fall back to export flags.
        with CALIBRATION.open("x", encoding="utf-8") as handle:
            json.dump(candidate, handle, indent=2, allow_nan=False)
            handle.write("\n")
        return candidate
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if args.activate and not args.run:
        parser.error("--activate requires --run")
    records = validate_corpus(args.dir)
    print(
        json.dumps(
            {"validated": len(records), "counts": dict(Counter(row["label"] for row in records))}
        )
    )
    if not args.run:
        return 0
    import time

    from PIL import Image

    from app.services.photo_reference import detect_faces, scene_similarity

    measured = []
    from app.services.storage import blur_faces

    # Only generated derivatives live here. Originals are never copied or deleted.
    with tempfile.TemporaryDirectory(prefix="urmind-calibration-review-") as temporary:
        review_dir = Path(temporary).resolve()
        if review_dir.is_relative_to(ROOT):
            raise ValueError("Temporary review directory must be external")
        for index, row in enumerate(records):
            # Recheck after all manifests have passed and immediately before inference.
            if digest(row["path"]) != row["sha256"]:
                raise ValueError("Calibration input changed after validation")
            with Image.open(row["path"]) as image:
                started = time.perf_counter()
                scene = scene_similarity(image)
                faces = detect_faces(image)
                if row["label"] in {"face_large", "face_small"}:
                    clean = Image.new("RGB", image.size)
                    clean.paste(image.convert("RGB"))
                    blur_faces(clean, faces["boxes"])
                    clean.save(review_dir / f"review-{index}.jpg", format="JPEG")
                measured.append(
                    {key: row[key] for key in ("sha256", "label", "subtype")}
                    | {
                        "margin": scene["margin"],
                        "scene_sha256": scene["sha256"],
                        "scene_revision": scene["revision"],
                        "face_sha256": faces["sha256"],
                        "face_revision": faces["revision"],
                        "face_area": faces["max_area_ratio"],
                        "face_count": len(faces["boxes"]),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )
        print(f"Inspect blurred derivatives (deleted when this command finishes): {review_dir}")
        blur_reviewed = (
            input("Confirma visualmente TODOS os rostos nas derivadas? Digite SIM: ").strip()
            == "SIM"
        )
    curve = threshold_curve(measured)
    now = datetime.now(UTC)
    metrics = {
        **{key: curve["selected"][key] for key in ("false_rejection", "false_acceptance")},
        **face_metrics(measured, 0.15),
    }
    threshold = curve["selected"]["threshold"]
    document = {
        "schema": "urmind-photo-gate-calibration-v1",
        "at": now.isoformat(),
        "counts": dict(Counter(row["label"] for row in records)),
        "metrics": metrics,
        "scene_sha256": measured[0]["scene_sha256"],
        "face_sha256": measured[0]["face_sha256"],
        "scene_revision": measured[0]["scene_revision"],
        "face_revision": measured[0]["face_revision"],
        "scene_reject_margin": threshold,
        "scene_accept_margin": math.nextafter(threshold, math.inf),
        "dominant_face_ratio": 0.15,
        "blur_reviewed": blur_reviewed,
        "input_sha256": sorted(row["sha256"] for row in records),
    }
    if any(
        row["scene_sha256"] != document["scene_sha256"]
        or row["face_sha256"] != document["face_sha256"]
        for row in measured
    ):
        raise ValueError("Model changed during calibration")
    activated = False
    if args.activate:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        try:
            document = asyncio.run(activate(document))
            activated = True
        except ValueError as exc:
            print(f"NOT_ACTIVATED: {exc}")
    report = ROOT / "docs/audits" / f"PHOTO_GATE_CALIBRATION_{now:%Y-%m-%d_%H%M%S}.md"
    if report.exists():
        raise ValueError("Calibration report already exists; do not overwrite evidence")
    report.write_text(
        f"# Reference photo gate calibration\n\nACTIVATED={activated}\n\n"
        "No training performed. Face targets: 100% recall in each labeled face cohort, <=5% false positives in street photos WITHOUT faces; minimum 5 photos per face cohort. Scene minimum: 20 per cohort. Latency below is measured, not guaranteed.\n\n```json\n"
        + json.dumps(
            {"calibration": document, "scene": curve, "measurements": measured},
            indent=2,
            allow_nan=False,
        )
        + "\n```\n",
        encoding="utf-8",
    )
    print(f"REPORT={report.relative_to(ROOT)}; ACTIVATED={activated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
