"""Consumer fail-closed dos manifests canônicos autorizados do detector."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, overload

import numpy as np
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from yolox.data.data_augment import preproc  # type: ignore[import-not-found]
from yolox.utils import xyxy2cxcywh  # type: ignore[import-not-found]

from app.datasets.adapters import AdapterError, _require_local_artifact
from app.ml.taxonomy import MODEL_V1_CANONICAL_CLASS_ORDER, RDD2022_TO_URMIND
from app.ml.yolox_model import (
    YOLOX_INPUT_SIZE,
    YOLOX_INTERFACE_BATCH_SIZE,
    YOLOX_MAX_LABELS,
    YOLOX_WINDOWS_NUM_WORKERS,
)

CANONICAL_CLASSES = {
    class_name: class_id
    for class_id, class_name in enumerate(MODEL_V1_CANONICAL_CLASS_ORDER)
}
ORIGINAL_TO_CANONICAL = {
    source: canonical.value for source, canonical in RDD2022_TO_URMIND.items()
}
DETECTION_MANIFEST_SCHEMA_VERSION = 2
TRAIN_FORBIDDEN_SPLITS = {"TEST", "EXTERNAL_TEST"}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_REGISTRY = PROJECT_ROOT / "datasets/metadata/artifact_registry.json"


class DetectionManifestError(AdapterError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _require_local_artifact(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_authorized_manifest(path: Path, *, intended_split: str) -> list[dict]:
    path = _require_local_artifact(path)
    registry = json.loads(_require_local_artifact(ARTIFACT_REGISTRY).read_text(encoding="utf-8"))
    try:
        manifest_key = path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise DetectionManifestError("manifest fora da raiz contratual do projeto") from exc
    matches = [item for item in registry.get("artifacts", []) if item.get("path") == manifest_key]
    if len(matches) != 1:
        raise DetectionManifestError("manifest sem registro único por caminho contratual completo")
    if _sha256(path) != matches[0].get("sha256"):
        raise DetectionManifestError("manifest stale: SHA-256 diverge do contrato")
    rows = []
    verified_images: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DetectionManifestError(f"linha {number}: JSON inválido") from exc
        split = row.get("split")
        if split != intended_split:
            raise DetectionManifestError(f"linha {number}: split {split!r} não é {intended_split}")
        if row.get("dataset_id") != "rdd2022" or not row.get("source_version"):
            raise DetectionManifestError(f"linha {number}: fonte não autorizada")
        if not row.get("image_path") or not row.get("group"):
            raise DetectionManifestError(f"linha {number}: identidade/grupo ausente")
        if intended_split == "TRAIN" and split in TRAIN_FORBIDDEN_SPLITS:
            raise DetectionManifestError("split protegido não pode entrar em TRAIN")
        if row.get("schema_version") != DETECTION_MANIFEST_SCHEMA_VERSION:
            raise DetectionManifestError(f"linha {number}: schema legado ou desconhecido")
        if any(field in row for field in ("bbox", "canonical_class", "original_class")):
            raise DetectionManifestError(f"linha {number}: schema legado por box não é permitido")
        if row.get("authorization_status") != f"{intended_split}_AUTHORIZED":
            raise DetectionManifestError(f"linha {number}: registro não autorizado")
        blockers = {
            "human_pending": row.get("human_pending"),
            "quarantine": row.get("quarantine"),
            "duplicate_rejected": row.get("duplicate_rejected"),
            "blocked_source": row.get("blocked_source"),
        }
        if any(blockers.values()):
            raise DetectionManifestError(f"linha {number}: gate bloqueado: {blockers}")
        width, height = row.get("image_width"), row.get("image_height")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise DetectionManifestError(f"linha {number}: dimensões inválidas")
        boxes = row.get("boxes")
        if not isinstance(boxes, list):
            raise DetectionManifestError(f"linha {number}: boxes deve ser lista")
        box_identities: set[tuple] = set()
        for box_number, box in enumerate(boxes, 1):
            original_class = box.get("original_class") if isinstance(box, dict) else None
            if not isinstance(original_class, str) or ORIGINAL_TO_CANONICAL.get(original_class) != box.get("canonical_class"):
                raise DetectionManifestError(f"linha {number}, box {box_number}: classe ausente/inválida")
            bbox = box.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(value, (int, float)) for value in bbox):
                raise DetectionManifestError(f"linha {number}, box {box_number}: bbox inválida")
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise DetectionManifestError(f"linha {number}, box {box_number}: bbox fora da imagem")
            box_identity = (tuple(bbox), box["canonical_class"], box["original_class"])
            if box_identity in box_identities:
                raise DetectionManifestError(f"linha {number}, box {box_number}: box duplicada")
            box_identities.add(box_identity)
        fingerprints = (row.get("annotation_fingerprint"), row.get("source_fingerprint"))
        if any(not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in fingerprints):
            raise DetectionManifestError(f"linha {number}: provenance/fingerprint ausente")
        image = Path(row["image_path"])
        if not image.is_absolute():
            image = PROJECT_ROOT / image
        try:
            image.resolve().relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise DetectionManifestError(f"linha {number}: imagem fora do projeto") from exc
        image_identity = str(image.resolve())
        if image_identity in verified_images:
            raise DetectionManifestError(f"linha {number}: imagem duplicada no manifest")
        verified_images.add(image_identity)
        rows.append(row)
    return rows


DetectionSample = tuple[np.ndarray, np.ndarray, tuple[int, int], int]


class AuthorizedDetectionDataset(Dataset[DetectionSample]):
    """Adapter determinístico do manifest autorizado para o contrato oficial YOLOX."""

    def __init__(
        self,
        rows: Sequence[dict],
        *,
        input_size: tuple[int, int] = YOLOX_INPUT_SIZE,
        max_labels: int = YOLOX_MAX_LABELS,
        mode: Literal["train", "validation"] = "train",
    ):
        if mode not in ("train", "validation"):
            raise ValueError("mode deve ser train ou validation")
        if len(input_size) != 2 or min(input_size) <= 0:
            raise ValueError("input_size deve conter altura e largura positivas")
        if max_labels <= 0:
            raise ValueError("max_labels deve ser positivo")
        if any(len(row["boxes"]) > max_labels for row in rows):
            raise DetectionManifestError("imagem excede max_labels; truncamento não é permitido")
        expected_split = "TRAIN" if mode == "train" else "VALIDATION"
        if any(row.get("split") != expected_split for row in rows):
            raise DetectionManifestError(
                f"mode {mode} exige somente registros {expected_split}"
            )
        self.samples = list(rows)
        self.input_dim = input_size
        self.max_labels = max_labels
        self.mode = mode

    def __len__(self) -> int:
        return len(self.samples)

    @overload
    def __getitem__(self, index: int) -> DetectionSample: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[DetectionSample]: ...

    def __getitem__(self, index: int | slice) -> DetectionSample | Sequence[DetectionSample]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        row = self.samples[index]
        image_path = Path(row["image_path"])
        if not image_path.is_absolute():
            image_path = PROJECT_ROOT / image_path
        try:
            image_path = _require_local_artifact(image_path)
            if _sha256(image_path) != row["source_fingerprint"]:
                raise DetectionManifestError("imagem stale")
            with Image.open(image_path) as image:
                if image.size != (row["image_width"], row["image_height"]):
                    raise DetectionManifestError("dimensões da imagem divergem do manifest")
                rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        except AdapterError as exc:
            if "cloud-only" in str(exc).lower():
                raise DetectionManifestError(f"CLOUD_ONLY_SAMPLE: {image_path}") from exc
            raise
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        image_chw, ratio = preproc(bgr, self.input_dim)
        xyxy_class = np.asarray(
            [[*box["bbox"], CANONICAL_CLASSES[box["canonical_class"]]] for box in row["boxes"]],
            dtype=np.float32,
        ).reshape((-1, 5))
        targets = np.zeros((self.max_labels, 5), dtype=np.float32)
        if len(xyxy_class):
            boxes = xyxy_class[:, :4] * ratio
            boxes[:, 0::2] = boxes[:, 0::2].clip(0, self.input_dim[1])
            boxes[:, 1::2] = boxes[:, 1::2].clip(0, self.input_dim[0])
            if np.any(boxes[:, 2] <= boxes[:, 0]) or np.any(boxes[:, 3] <= boxes[:, 1]):
                raise DetectionManifestError("bbox degenerada após transformação")
            cxcywh = xyxy2cxcywh(boxes.copy())
            converted = np.column_stack((xyxy_class[:, 4], cxcywh)).astype(np.float32)
            targets[: len(converted)] = converted
        return image_chw, targets, (row["image_height"], row["image_width"]), index


def build_yolox_dataloader(
    dataset: AuthorizedDetectionDataset,
    *,
    batch_size: int = YOLOX_INTERFACE_BATCH_SIZE,
    num_workers: int = YOLOX_WINDOWS_NUM_WORKERS,
    pin_memory: bool = True,
    shuffle: bool = False,
) -> DataLoader:
    """Constrói o loader conservador; default_collate é o usado pelo YOLOX."""
    if batch_size <= 0:
        raise ValueError("batch_size deve ser positivo")
    if num_workers < 0:
        raise ValueError("num_workers não pode ser negativo")
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=pin_memory,
        persistent_workers=num_workers > 0,
        drop_last=False,
    )
