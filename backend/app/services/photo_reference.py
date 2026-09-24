"""Registered reference preprocessing only; never emits urban Detections.

Acquisition is explicit in scripts/datasets/acquire_registered.py. Runtime never
downloads, hydrates a cloud placeholder, or resolves a 'latest' model.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "datasets/metadata/acquisition_plan.json"
SCENE_PROMPTS = {
    "positive": [
        "a photo of a public street",
        "a photo of an asphalt road",
        "a photo of a sidewalk",
        "a photo of a street manhole",
        "a photo of a traffic sign",
        "a photo of a tree on a sidewalk",
        "a photo of a streetlight pole",
    ],
    "negative": [
        "a photo of an indoor room",
        "a selfie of a person",
        "a close-up photo of a face",
        "a photo of a document",
        "a screenshot of a computer screen",
        "a photo of food",
        "a photo of a pet animal",
        "a photo of only the sky",
    ],
}


class ReferenceUnavailable(RuntimeError):
    pass


def reference_artifact(identifier: str) -> tuple[Path, dict[str, Any]]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    entries = [item for item in plan.get("reference_models", []) if item["id"] == identifier]
    if len(entries) != 1 or entries[0]["status"] != "REFERENCE":
        raise ReferenceUnavailable("reference_registration_missing")
    entry = entries[0]
    path = ROOT / entry["path"]
    if path.resolve().parent != (ROOT / "models/reference").resolve():
        raise ReferenceUnavailable("reference_path_invalid")
    if not path.is_file():
        raise ReferenceUnavailable("reference_not_installed")
    info = path.stat()
    if getattr(info, "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
        raise ReferenceUnavailable("reference_cloud_only")
    if info.st_size != entry["size_bytes"]:
        raise ReferenceUnavailable("reference_size_mismatch")
    _verify_hash(str(path), info.st_mtime_ns, info.st_size, entry["sha256"])
    return path, entry


@lru_cache(maxsize=4)
def _verify_hash(path: str, modified: int, size: int, expected: str) -> None:
    with Path(path).open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
            raise ReferenceUnavailable("reference_checksum_mismatch")


_face_lock = Lock()


@lru_cache(maxsize=1)
def _face_detector(path: str, checksum: str) -> Any:
    import cv2

    # OpenCV's narrow filesystem API cannot open Windows Unicode paths. Python
    # reads the verified local file; the official in-memory overload avoids a copy.
    return cv2.FaceDetectorYN.create(
        "onnx",
        np.frombuffer(Path(path).read_bytes(), dtype=np.uint8),
        np.empty(0, dtype=np.uint8),
        (640, 640),
        0.85,
        0.3,
        500,
    )


def detect_faces(image: Image.Image) -> dict[str, Any]:
    path, entry = reference_artifact("yunet_face_privacy")
    import cv2

    scaled = image.convert("RGB")
    scaled.thumbnail((640, 640))
    with _face_lock:
        detector = _face_detector(str(path), entry["sha256"])
        detector.setInputSize(scaled.size)
        _, detections = detector.detect(cv2.cvtColor(np.asarray(scaled), cv2.COLOR_RGB2BGR))
    boxes = []
    if detections is not None:
        for face in detections:
            x, y, width, height = [float(value) for value in face[:4]]
            box = [
                max(0.0, x / scaled.width),
                max(0.0, y / scaled.height),
                min(1.0, (x + width) / scaled.width),
                min(1.0, (y + height) / scaled.height),
            ]
            if box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)
    return {
        "status": "CHECKED",
        "boxes": boxes,
        "model": entry["id"],
        "revision": entry["revision"],
        "sha256": entry["sha256"],
        "max_area_ratio": max(((b[2] - b[0]) * (b[3] - b[1]) for b in boxes), default=0.0),
    }


@lru_cache(maxsize=1)
def _scene_session(path: str, checksum: str) -> Any:
    import onnxruntime as ort  # type: ignore[import-untyped]

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def scene_similarity(image: Image.Image) -> dict[str, Any]:
    _, entry = reference_artifact("openclip_scene_reference")
    manifest_path = ROOT / entry["derived_manifest"]
    if not manifest_path.is_file():
        raise ReferenceUnavailable("scene_export_not_installed")
    info = manifest_path.stat()
    if getattr(info, "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
        raise ReferenceUnavailable("scene_manifest_cloud_only")
    _verify_hash(
        str(manifest_path), info.st_mtime_ns, info.st_size, entry.get("derived_manifest_sha256", "")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest["source_sha256"] != entry["sha256"]
        or manifest["source_revision"] != entry["revision"]
        or manifest["status"] != "REFERENCE"
        or manifest["prompts"] != SCENE_PROMPTS
    ):
        raise ReferenceUnavailable("scene_manifest_mismatch")
    path = ROOT / manifest["onnx_path"]
    if path.resolve().parent != (ROOT / "models/reference").resolve() or not path.is_file():
        raise ReferenceUnavailable("scene_onnx_missing")
    info = path.stat()
    if getattr(info, "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
        raise ReferenceUnavailable("scene_cloud_only")
    _verify_hash(str(path), info.st_mtime_ns, info.st_size, manifest["onnx_sha256"])
    config = manifest["preprocess"]
    source = ImageOps.exif_transpose(image).convert("RGB")
    width, height = source.size
    size = config["size"]
    source = source.resize(
        (size, int(size * height / width))
        if width <= height
        else (int(size * width / height), size),
        Image.Resampling.BICUBIC,
    )
    left, top = round((source.width - size) / 2), round((source.height - size) / 2)
    pixels = np.asarray(source.crop((left, top, left + size, top + size)), dtype=np.float32) / 255
    pixels = (pixels - np.asarray(config["mean"], dtype=np.float32)) / np.asarray(
        config["std"], dtype=np.float32
    )
    embedding = _scene_session(str(path), manifest["onnx_sha256"]).run(
        None, {"image": pixels.transpose(2, 0, 1)[None].copy()}
    )[0]
    vectors = np.asarray(manifest["text_embeddings"], dtype=np.float32)
    if (
        vectors.shape != (sum(len(v) for v in SCENE_PROMPTS.values()), 512)
        or not np.isfinite(vectors).all()
    ):
        raise ReferenceUnavailable("scene_embeddings_invalid")
    scores = (embedding @ vectors.T)[0]
    split = len(SCENE_PROMPTS["positive"])
    positive, negative = float(scores[:split].max()), float(scores[split:].max())
    if not np.isfinite([positive, negative]).all():
        raise ReferenceUnavailable("scene_scores_invalid")
    return {
        "positive_similarity": positive,
        "negative_similarity": negative,
        "margin": positive - negative,
        "calibrated": manifest["calibrated"],
        "sha256": manifest["onnx_sha256"],
        "revision": entry["revision"],
    }
