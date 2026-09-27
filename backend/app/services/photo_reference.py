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
CALIBRATION = ROOT / "datasets/metadata/photo_gate_calibration.json"
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


def calibrated_policy(policy: Any) -> dict[str, Any] | None:
    """Absent, stale, malformed or below-target evidence never activates references."""
    try:
        document = json.loads(CALIBRATION.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return None
        _, face = reference_artifact("yunet_face_privacy")
        _, scene = reference_artifact("openclip_scene_reference")
        manifest_path = ROOT / scene["derived_manifest"]
        info = manifest_path.stat()
        _verify_hash(
            str(manifest_path), info.st_mtime_ns, info.st_size, scene["derived_manifest_sha256"]
        )
        export = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            document.get("scene_revision") != scene["revision"]
            or document.get("face_revision") != face["revision"]
        ):
            return None
        if not calibration_matches(
            document, scene_sha=export["onnx_sha256"], face_sha=face["sha256"], policy=policy
        ):
            return None
        return document
    except (OSError, ValueError, TypeError, KeyError, ReferenceUnavailable):
        return None


def calibration_matches(
    document: dict[str, Any], *, scene_sha: str, face_sha: str, policy: Any
) -> bool:
    import uuid

    try:
        if not isinstance(document.get("activation_audit_id"), str):
            return False
        uuid.UUID(document["activation_audit_id"])
        metrics, counts = document["metrics"], document["counts"]
        return bool(
            document["schema"] == "urmind-photo-gate-calibration-v1"
            and document["scene_sha256"] == scene_sha
            and document["face_sha256"] == face_sha
            and document["blur_reviewed"] is True
            and counts["street_positive"] >= 20
            and counts["scene_negative"] >= 20
            and counts["face_large"] >= 5
            and counts["face_small"] >= 5
            and 0 <= metrics["false_rejection"] <= 0.05
            and 0 <= metrics["false_acceptance"] <= 0.15
            and metrics["face_large_recall"] == 1
            and metrics["face_small_recall"] == 1
            and 0 <= metrics["face_false_positive"] <= 0.05
            and document["scene_reject_margin"] == policy.scene_reject_margin
            and document["scene_accept_margin"] == policy.scene_accept_margin
            and document["dominant_face_ratio"] == policy.dominant_face_ratio
        )
    except (ValueError, TypeError, KeyError):
        return False


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
    embedding, manifest, entry = _scene_embedding(image)
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
        "calibrated": False,  # Export parity is not operational calibration.
        "sha256": manifest["onnx_sha256"],
        "revision": entry["revision"],
    }


def _scene_embedding(image: Image.Image) -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    """Embedding normalizado da imagem pelo encoder OpenCLIP registrado (hashes conferidos)."""
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
    return np.asarray(embedding, dtype=np.float32), manifest, entry


# --- Sinal auxiliar de categorias urbanas (zero-shot, sem treino) -------------------
#
# Para árvore caída, galho, entulho, lixo, bueiro aberto, boca de lobo
# obstruída e alagamento, o encoder OpenCLIP
# registrado compara a foto com descrições em texto. É SUGESTÃO para a revisão
# humana (sem caixa, sem calibração, sem Detection, sem Event, sem publicação): o
# revisor escolhe a classe e só a Review confirmada chega ao mapa.

URBAN_AUX_PROMPTS: dict[str, tuple[str, ...]] = {
    "URMIND_FALLEN_TREE": (
        "a photo of a fallen tree lying across a street",
        "a photo of an uprooted tree blocking a road",
    ),
    "URMIND_FALLEN_BRANCH": (
        "a photo of a broken tree branch lying on a street",
        "a photo of fallen tree branches on a sidewalk",
    ),
    "URMIND_ROAD_DEBRIS": (
        "a photo of rubble and construction debris on a road",
        "a photo of an object left in the middle of a street",
    ),
    "URMIND_ILLEGAL_DUMPING": (
        "a photo of a pile of garbage bags dumped on a sidewalk",
        "a photo of trash and discarded furniture piled on a street corner",
    ),
    "URMIND_OPEN_MANHOLE": (
        "a photo of an open manhole without a cover in the street",
        "a photo of an uncovered manhole hole in a road",
    ),
    "URMIND_BLOCKED_DRAIN": (
        "a photo of a storm drain clogged with trash and leaves",
        "a photo of a street gutter inlet blocked by debris",
    ),
    "URMIND_FLOODED_ROAD": (
        "a photo of a flooded street covered by water",
        "a photo of cars driving through a flooded road",
    ),
}
URBAN_AUX_BACKGROUND: tuple[str, ...] = (
    "a photo of a clean city street",
    "a photo of an asphalt road",
    "a photo of a sidewalk",
    "a photo of a street with parked cars",
    "a photo of a cracked asphalt road",
    "a photo of a pothole in the road",
    "a photo of a tree on a sidewalk",
    "a photo of a street manhole cover",
)
URBAN_AUX_MANIFEST = ROOT / "datasets/metadata/urban_aux_reference_export.json"
URBAN_AUX_MANIFEST_SHA256 = "ae8193f88f8efe6e6af879409f19dfae932179cda18b77dea232750fe65dd7b7"
"""Fixado depois do export; vazio = sinal auxiliar indisponível (nunca adivinhado)."""
URBAN_AUX_LOGIT_SCALE = 100.0
URBAN_AUX_MIN_PROBABILITY = 0.5
"""Corte padrão NÃO calibrado: só decide o que vira sugestão para o revisor."""


def urban_aux_scores(
    embedding: np.ndarray,
    vectors: np.ndarray,
    prompts: dict[str, tuple[str, ...]],
    background: tuple[str, ...],
) -> dict[str, Any]:
    """Zero-shot: melhor prompt de cada categoria contra o melhor prompt de fundo.

    Probabilidade = softmax (escala CLIP) entre as categorias e o fundo; margem =
    similaridade da categoria menos a do fundo. Sugere a categoria só se ela vence o
    fundo e passa do corte padrão. Nada disso é medida de acerto.
    """
    count = sum(len(items) for items in prompts.values()) + len(background)
    if vectors.ndim != 2 or vectors.shape[0] != count or embedding.shape[-1] != vectors.shape[1]:
        raise ReferenceUnavailable("urban_aux_embeddings_invalid")
    similarities = (embedding.reshape(1, -1) @ vectors.T)[0]
    if not np.isfinite(similarities).all():
        raise ReferenceUnavailable("urban_aux_scores_invalid")
    best: dict[str, float] = {}
    offset = 0
    for code, items in prompts.items():
        best[code] = float(similarities[offset : offset + len(items)].max())
        offset += len(items)
    base = float(similarities[offset:].max())
    logits = URBAN_AUX_LOGIT_SCALE * np.asarray([*best.values(), base], dtype=np.float64)
    weights = np.exp(logits - logits.max())
    probabilities = weights / weights.sum()
    scores = {
        code: {
            "similarity": round(value, 4),
            "margin": round(value - base, 4),
            "probability": round(float(probabilities[index]), 4),
        }
        for index, (code, value) in enumerate(best.items())
    }
    ranked = sorted(
        (
            (score["probability"], code)
            for code, score in scores.items()
            if score["margin"] > 0 and score["probability"] >= URBAN_AUX_MIN_PROBABILITY
        ),
        reverse=True,
    )
    suggestions = [{"code": code, "probability": probability} for probability, code in ranked]
    return {
        "status": "UNCALIBRATED_ADVISORY",
        "method": "openclip_zero_shot_scene",
        "suggestions": suggestions,
        "scores": scores,
        "background_similarity": round(base, 4),
    }


def validate_urban_aux_manifest(
    manifest: dict[str, Any], entry: dict[str, Any], *, onnx_sha256: str
) -> np.ndarray:
    """Vetores só valem para os mesmos pesos, o mesmo ONNX e os mesmos textos do código."""
    if (
        manifest.get("schema") != "urmind-urban-aux-reference-v1"
        or manifest.get("status") != "UNCALIBRATED_ADVISORY"
        or manifest.get("source_sha256") != entry["sha256"]
        or manifest.get("source_revision") != entry["revision"]
        or manifest.get("onnx_sha256") != onnx_sha256
        or manifest.get("prompts") != {k: list(v) for k, v in URBAN_AUX_PROMPTS.items()}
        or manifest.get("background") != list(URBAN_AUX_BACKGROUND)
    ):
        raise ReferenceUnavailable("urban_aux_manifest_mismatch")
    vectors = np.asarray(manifest.get("text_embeddings"), dtype=np.float32)
    count = sum(len(v) for v in URBAN_AUX_PROMPTS.values()) + len(URBAN_AUX_BACKGROUND)
    if vectors.shape != (count, 512) or not np.isfinite(vectors).all():
        raise ReferenceUnavailable("urban_aux_embeddings_invalid")
    return vectors


def urban_auxiliary(image: Image.Image) -> dict[str, Any]:
    if not URBAN_AUX_MANIFEST_SHA256 or not URBAN_AUX_MANIFEST.is_file():
        raise ReferenceUnavailable("urban_aux_not_exported")
    info = URBAN_AUX_MANIFEST.stat()
    _verify_hash(str(URBAN_AUX_MANIFEST), info.st_mtime_ns, info.st_size, URBAN_AUX_MANIFEST_SHA256)
    embedding, scene, entry = _scene_embedding(image)
    manifest = json.loads(URBAN_AUX_MANIFEST.read_text(encoding="utf-8"))
    vectors = validate_urban_aux_manifest(manifest, entry, onnx_sha256=scene["onnx_sha256"])
    result = urban_aux_scores(embedding, vectors, URBAN_AUX_PROMPTS, URBAN_AUX_BACKGROUND)
    return {
        **result,
        "onnx_sha256": scene["onnx_sha256"],
        "manifest_sha256": URBAN_AUX_MANIFEST_SHA256,
        "min_probability": URBAN_AUX_MIN_PROBABILITY,
    }


def urban_auxiliary_safe(image_bytes: bytes) -> dict[str, Any]:
    """Para o Worker: nunca interrompe a análise; indisponível vira estado declarado."""
    from io import BytesIO

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            return urban_auxiliary(image)
    except ReferenceUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except (OSError, ValueError):
        return {"status": "unavailable", "reason": "image_unreadable"}


def export_urban_aux_embeddings() -> dict[str, Any]:
    """Vetores de texto das categorias auxiliares com os pesos OpenCLIP registrados.

    Sem treino, sem dataset, sem otimizador, sem download: só `encode_text` dos pesos
    locais já verificados por hash. Não sobrescreve um manifesto existente.
    """
    import tempfile
    from datetime import UTC, datetime

    import open_clip  # type: ignore[import-untyped]
    import torch

    if URBAN_AUX_MANIFEST.exists():
        raise ValueError("manifesto auxiliar já existe; não sobrescrevo")
    path, entry = reference_artifact("openclip_scene_reference")
    scene = json.loads((ROOT / entry["derived_manifest"]).read_text(encoding="utf-8"))
    model = open_clip.create_model("ViT-B-32", pretrained=None, device="cpu")
    open_clip.load_checkpoint(model, str(path), strict=True)
    model.eval().requires_grad_(False)
    prompts = [text for items in URBAN_AUX_PROMPTS.values() for text in items]
    prompts += list(URBAN_AUX_BACKGROUND)
    with torch.inference_mode():
        vectors = model.encode_text(open_clip.get_tokenizer("ViT-B-32")(prompts), normalize=True)
    document = {
        "schema": "urmind-urban-aux-reference-v1",
        "status": "UNCALIBRATED_ADVISORY",
        "purpose": "sugestão de categorias urbanas para revisão humana; não é Detection",
        "training": "none",
        "source_sha256": entry["sha256"],
        "source_revision": entry["revision"],
        "onnx_sha256": scene["onnx_sha256"],
        "prompts": {code: list(items) for code, items in URBAN_AUX_PROMPTS.items()},
        "background": list(URBAN_AUX_BACKGROUND),
        "text_embeddings": [[round(float(v), 7) for v in row] for row in vectors.tolist()],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=URBAN_AUX_MANIFEST.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(document, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    temporary.replace(URBAN_AUX_MANIFEST)
    digest = hashlib.sha256(URBAN_AUX_MANIFEST.read_bytes()).hexdigest()
    return {"path": URBAN_AUX_MANIFEST.relative_to(ROOT).as_posix(), "sha256": digest}


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["export-urban-aux"]:
        raise SystemExit("uso: python -m app.services.photo_reference export-urban-aux")
    print(json.dumps(export_urban_aux_embeddings()))
