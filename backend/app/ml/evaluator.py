"""Evaluator oficial do YOLOX MODEL V1 sobre o manifesto VALIDATION autorizado."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader
from yolox.utils import postprocess  # type: ignore[import-not-found]

from app.ml.detection_dataset import AuthorizedDetectionDataset, load_authorized_manifest
from app.ml.metrics import Box, EvaluationResult, GroundTruth, Prediction, evaluate
from app.ml.taxonomy import MODEL_V1_CLASS_ORDER

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VALIDATION_MANIFEST_PATH = (
    PROJECT_ROOT / "datasets/manifests/detection_validation_authorized.jsonl"
)


class EvaluationGateError(RuntimeError):
    """O caminho de avaliação não satisfaz o contrato de VALIDATION."""


@dataclass(frozen=True)
class EvaluationConfig:
    confidence_threshold: float
    nms_threshold: float
    iou_threshold: float
    num_classes: int
    class_names: tuple[str, ...]
    input_size: tuple[int, int]

    @classmethod
    def from_model_metadata(cls, metadata: Mapping[str, Any]) -> EvaluationConfig:
        try:
            evaluation = metadata["evaluation"]
            config = cls(
                confidence_threshold=float(evaluation["confidence_threshold"]),
                nms_threshold=float(evaluation["nms_threshold"]),
                iou_threshold=float(evaluation["iou_threshold"]),
                num_classes=int(metadata["num_classes"]),
                class_names=tuple(metadata["class_names"]),
                input_size=tuple(metadata["test_size"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("evaluation config ausente ou malformada") from exc
        if not all(
            0.0 <= threshold <= 1.0
            for threshold in (
                config.confidence_threshold,
                config.nms_threshold,
                config.iou_threshold,
            )
        ):
            raise ValueError("thresholds de avaliação devem estar entre zero e um")
        if config.num_classes != 4 or config.class_names != MODEL_V1_CLASS_ORDER:
            raise ValueError("evaluation config diverge da taxonomia MODEL V1")
        if config.input_size != (640, 640):
            raise ValueError("test_size diverge da interface MODEL V1")
        return config


@dataclass(frozen=True)
class EvaluationRun:
    result: EvaluationResult
    predictions: list[Prediction]
    ground_truths: list[GroundTruth]
    samples: int
    positive_images: int
    negative_images: int
    multi_box_images: int


def manifest_for_evaluation(role: str) -> Path:
    if role != "VALIDATION":
        raise ValueError("evaluator aceita somente VALIDATION; TEST permanece selado")
    return VALIDATION_MANIFEST_PATH


def assert_authorized_validation_dataset(dataset: AuthorizedDetectionDataset) -> None:
    """Vincula cada linha consumida ao manifesto registrado, permitindo subsets de smoke."""
    authorized_rows = load_authorized_manifest(
        manifest_for_evaluation("VALIDATION"), intended_split="VALIDATION"
    )
    authorized_by_image = {str(row["image_path"]): row for row in authorized_rows}
    if not dataset.samples or any(
        authorized_by_image.get(str(row.get("image_path"))) != row for row in dataset.samples
    ):
        raise EvaluationGateError(
            "dataset não está vinculado ao manifesto VALIDATION autorizado"
        )


def postprocess_batch(
    raw_outputs: torch.Tensor, config: EvaluationConfig
) -> list[torch.Tensor | None]:
    """Delega decode filtragem e NMS ao contrato oficial do YOLOX."""
    return postprocess(
        raw_outputs,
        config.num_classes,
        config.confidence_threshold,
        config.nms_threshold,
        class_agnostic=False,
    )


def _image_infos(info_imgs: Any) -> list[tuple[int, int]]:
    if (
        isinstance(info_imgs, (list, tuple))
        and len(info_imgs) == 2
        and all(isinstance(value, torch.Tensor) for value in info_imgs)
    ):
        return [
            (int(height), int(width))
            for height, width in zip(info_imgs[0], info_imgs[1], strict=True)
        ]
    return [(int(item[0]), int(item[1])) for item in info_imgs]


def _indices(values: Any) -> list[int]:
    if isinstance(values, torch.Tensor):
        return [int(value) for value in values]
    return [int(value) for value in values]


def _row(dataset: AuthorizedDetectionDataset, index: int) -> dict[str, Any]:
    try:
        return dataset.samples[index]
    except (IndexError, TypeError) as exc:
        raise EvaluationGateError("image index não corresponde ao dataset autorizado") from exc


def detections_to_predictions(
    detections: Sequence[torch.Tensor | None],
    info_imgs: Any,
    image_indices: Any,
    dataset: AuthorizedDetectionDataset,
    config: EvaluationConfig,
) -> list[Prediction]:
    infos = _image_infos(info_imgs)
    indices = _indices(image_indices)
    if len(detections) != len(infos) or len(infos) != len(indices):
        raise EvaluationGateError("batch perdeu associação entre output, imagem e image_id")
    predictions: list[Prediction] = []
    for output, (height, width), index in zip(detections, infos, indices, strict=True):
        if output is None:
            continue
        scale = min(config.input_size[0] / height, config.input_size[1] / width)
        row = _row(dataset, index)
        image_id = str(row["image_path"])
        for detection in output.detach().float().cpu():
            class_id = int(detection[6])
            if not 0 <= class_id < config.num_classes:
                raise EvaluationGateError("postprocess produziu class_id fora da taxonomia")
            x1, y1, x2, y2 = (float(value / scale) for value in detection[:4])
            objectness = float(detection[4])
            class_confidence = float(detection[5])
            predictions.append(
                Prediction(
                    image_id=image_id,
                    label=config.class_names[class_id],
                    box=Box(x1, y1, x2, y2),
                    score=objectness * class_confidence,
                    class_id=class_id,
                    objectness=objectness,
                    class_confidence=class_confidence,
                )
            )
    return predictions


def targets_to_ground_truths(
    targets: torch.Tensor,
    info_imgs: Any,
    image_indices: Any,
    dataset: AuthorizedDetectionDataset,
) -> list[GroundTruth]:
    infos = _image_infos(info_imgs)
    indices = _indices(image_indices)
    if targets.shape[0] != len(infos) or len(infos) != len(indices):
        raise EvaluationGateError("batch perdeu associação entre target, imagem e image_id")
    truths: list[GroundTruth] = []
    for sample_targets, (height, width), index in zip(
        targets.detach().float().cpu(), infos, indices, strict=True
    ):
        row = _row(dataset, index)
        scale = min(dataset.input_dim[0] / height, dataset.input_dim[1] / width)
        image_id = str(row["image_path"])
        active = sample_targets[(sample_targets[:, 3] > 0) & (sample_targets[:, 4] > 0)]
        for target in active:
            class_id = int(target[0])
            if not 0 <= class_id < len(MODEL_V1_CLASS_ORDER):
                raise EvaluationGateError("target contém class_id fora da taxonomia")
            cx, cy, box_width, box_height = (float(value / scale) for value in target[1:5])
            truths.append(
                GroundTruth(
                    image_id=image_id,
                    label=MODEL_V1_CLASS_ORDER[class_id],
                    box=Box(
                        cx - box_width / 2,
                        cy - box_height / 2,
                        cx + box_width / 2,
                        cy + box_height / 2,
                    ),
                    class_id=class_id,
                    source_fingerprint=row.get("source_fingerprint"),
                    annotation_fingerprint=row.get("annotation_fingerprint"),
                )
            )
    return truths


class YOLOXEvaluator:
    def __init__(
        self,
        model: torch.nn.Module,
        dataloader: DataLoader,
        config: EvaluationConfig,
        *,
        device: str | torch.device = "cuda",
    ) -> None:
        dataset = dataloader.dataset
        if not isinstance(dataset, AuthorizedDetectionDataset):
            raise EvaluationGateError("evaluator exige AuthorizedDetectionDataset")
        if dataset.mode != "validation" or any(
            row.get("split") != "VALIDATION" for row in dataset.samples
        ):
            raise EvaluationGateError("evaluator aceita somente dataset VALIDATION autorizado")
        assert_authorized_validation_dataset(dataset)
        self.model = model
        self.dataloader = dataloader
        self.dataset = dataset
        self.config = config
        self.device = torch.device(device)
        if self.device.type == "cuda" and not torch.cuda.is_available():
            raise EvaluationGateError("CUDA solicitado, mas indisponível; fallback proibido")

    def evaluate(self, *, max_batches: int | None = None) -> EvaluationRun:
        if max_batches is not None and max_batches <= 0:
            raise ValueError("max_batches deve ser positivo")
        was_training = self.model.training
        predictions: list[Prediction] = []
        truths: list[GroundTruth] = []
        sample_count = positive = negative = multi = 0
        self.model.eval()
        try:
            with torch.inference_mode():
                for batch_number, batch in enumerate(self.dataloader):
                    if max_batches is not None and batch_number >= max_batches:
                        break
                    images, targets, info_imgs, image_indices = batch
                    infos = _image_infos(info_imgs)
                    indices = _indices(image_indices)
                    outputs = self.model(images.to(self.device, non_blocking=False))
                    detections = postprocess_batch(outputs, self.config)
                    predictions.extend(
                        detections_to_predictions(
                            detections, infos, indices, self.dataset, self.config
                        )
                    )
                    batch_truths = targets_to_ground_truths(
                        targets, infos, indices, self.dataset
                    )
                    truths.extend(batch_truths)
                    counts = [
                        int(((item[:, 3] > 0) & (item[:, 4] > 0)).sum()) for item in targets
                    ]
                    sample_count += len(counts)
                    positive += sum(count > 0 for count in counts)
                    negative += sum(count == 0 for count in counts)
                    multi += sum(count > 1 for count in counts)
        finally:
            self.model.train(was_training)
        result = evaluate(
            predictions,
            truths,
            labels=list(self.config.class_names),
            score_threshold=self.config.confidence_threshold,
            iou_threshold=self.config.iou_threshold,
        )
        return EvaluationRun(
            result=result,
            predictions=predictions,
            ground_truths=truths,
            samples=sample_count,
            positive_images=positive,
            negative_images=negative,
            multi_box_images=multi,
        )
