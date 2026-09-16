"""Contratos do evaluator oficial do YOLOX MODEL V1."""

from __future__ import annotations

import json

import pytest
import torch
from torch.utils.data import DataLoader

from app.ml.detection_dataset import AuthorizedDetectionDataset
from app.ml.yolox_model import MODEL_METADATA_PATH


def _metadata() -> dict:
    return json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))


def _validation_dataset(*, targets: list[torch.Tensor]) -> AuthorizedDetectionDataset:
    class FixtureDataset(AuthorizedDetectionDataset):
        def __getitem__(self, index: int):
            return (
                torch.zeros((3, 640, 640), dtype=torch.float32),
                targets[index],
                (640, 640),
                index,
            )

    dataset = object.__new__(FixtureDataset)
    dataset.samples = [
        {
            "split": "VALIDATION",
            "image_path": f"datasets/raw/fixture-{index}.jpg",
            "source_fingerprint": f"{index + 1:064x}",
            "annotation_fingerprint": f"{index + 101:064x}",
            "boxes": [],
        }
        for index in range(len(targets))
    ]
    dataset.input_dim = (640, 640)
    dataset.max_labels = targets[0].shape[0]
    dataset.mode = "validation"
    return dataset


def _raw_prediction(rows: list[list[float]]) -> torch.Tensor:
    return torch.tensor([rows], dtype=torch.float32)


def test_evaluation_config_uses_only_authoritative_model_metadata() -> None:
    from app.ml.evaluator import EvaluationConfig

    config = EvaluationConfig.from_model_metadata(_metadata())

    assert config.confidence_threshold == 0.01
    assert config.nms_threshold == 0.65
    assert config.iou_threshold == 0.5
    assert config.num_classes == 4
    assert config.class_names == ("D00", "D10", "D20", "D40")


@pytest.mark.parametrize("role", ["TRAIN", "TEST", "EXTERNAL_TEST"])
def test_evaluator_fails_closed_for_every_non_validation_role(role: str) -> None:
    from app.ml.evaluator import manifest_for_evaluation

    with pytest.raises(ValueError, match="somente VALIDATION"):
        manifest_for_evaluation(role)


def test_evaluator_resolves_only_authorized_validation_manifest() -> None:
    from app.ml.evaluator import manifest_for_evaluation

    assert manifest_for_evaluation("VALIDATION").name == "detection_validation_authorized.jsonl"


def test_official_postprocess_suppresses_same_class_overlap() -> None:
    from app.ml.evaluator import EvaluationConfig, postprocess_batch

    config = EvaluationConfig.from_model_metadata(_metadata())
    raw = _raw_prediction(
        [
            [100, 100, 40, 40, 0.9, 0.9, 0.0, 0.0, 0.0],
            [101, 101, 40, 40, 0.8, 0.9, 0.0, 0.0, 0.0],
        ]
    )

    result = postprocess_batch(raw, config)

    assert result[0] is not None
    assert len(result[0]) == 1


def test_official_postprocess_keeps_overlapping_different_classes() -> None:
    from app.ml.evaluator import EvaluationConfig, postprocess_batch

    config = EvaluationConfig.from_model_metadata(_metadata())
    raw = _raw_prediction(
        [
            [100, 100, 40, 40, 0.9, 0.9, 0.0, 0.0, 0.0],
            [100, 100, 40, 40, 0.9, 0.0, 0.9, 0.0, 0.0],
        ]
    )

    result = postprocess_batch(raw, config)

    assert result[0] is not None
    assert len(result[0]) == 2


def test_postprocess_handles_empty_low_confidence_and_boundary() -> None:
    from app.ml.evaluator import EvaluationConfig, postprocess_batch

    config = EvaluationConfig.from_model_metadata(_metadata())
    empty = torch.empty((1, 0, 9), dtype=torch.float32)
    assert postprocess_batch(empty, config) == [None]

    below = _raw_prediction([[100, 100, 40, 40, 0.1, 0.099, 0, 0, 0]])
    assert postprocess_batch(below, config) == [None]

    boundary = _raw_prediction(
        [
            [100, 100, 40, 40, 0.1, 0.1, 0, 0, 0],
            [200, 200, 20, 20, 0.0, 0, 0, 0, 0],
        ]
    )
    kept = postprocess_batch(boundary, config)
    assert kept[0] is not None and len(kept[0]) == 1


def test_prediction_representation_preserves_batch_image_identity_and_scores() -> None:
    from app.ml.evaluator import EvaluationConfig, detections_to_predictions, postprocess_batch

    config = EvaluationConfig.from_model_metadata(_metadata())
    raw = torch.tensor(
        [
            [
                [100, 100, 40, 40, 0.8, 0, 0, 0, 0.5],
                [300, 300, 10, 10, 0, 0, 0, 0, 0],
            ],
            [
                [200, 200, 20, 20, 0.9, 0, 0.7, 0, 0],
                [300, 300, 10, 10, 0, 0, 0, 0, 0],
            ],
        ],
        dtype=torch.float32,
    )
    dataset = _validation_dataset(targets=[torch.zeros((2, 5)), torch.zeros((2, 5))])
    detections = postprocess_batch(raw, config)

    predictions = detections_to_predictions(
        detections, [(640, 640), (320, 640)], [0, 1], dataset, config
    )

    assert [item.image_id for item in predictions] == [
        "datasets/raw/fixture-0.jpg",
        "datasets/raw/fixture-1.jpg",
    ]
    assert predictions[0].class_id == 3
    assert predictions[0].class_name == "D40"
    assert predictions[0].objectness == pytest.approx(0.8)
    assert predictions[0].class_confidence == pytest.approx(0.5)
    assert predictions[0].confidence == pytest.approx(0.4)
    assert predictions[1].box.x1 == pytest.approx(190.0)


def test_ground_truth_comes_from_dataset_batch_and_preserves_fingerprints() -> None:
    from app.ml.evaluator import targets_to_ground_truths

    targets = torch.zeros((2, 3, 5), dtype=torch.float32)
    targets[0, 0] = torch.tensor([0, 100, 100, 20, 40])
    targets[0, 1] = torch.tensor([3, 200, 200, 50, 50])
    dataset = _validation_dataset(targets=[targets[0], targets[1]])

    truths = targets_to_ground_truths(
        targets, [(640, 640), (640, 640)], [0, 1], dataset
    )

    assert len(truths) == 2
    assert [truth.class_name for truth in truths] == ["D00", "D40"]
    assert truths[0].class_id == 0
    assert truths[0].source_fingerprint == f"{1:064x}"
    assert truths[0].annotation_fingerprint == f"{101:064x}"


class _NoDetectionModel(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.weight = torch.nn.Parameter(torch.tensor(1.0))
        self.observed_eval = False
        self.observed_inference = False

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        self.observed_eval = not self.training
        self.observed_inference = not torch.is_grad_enabled()
        return torch.zeros((images.shape[0], 1, 9), device=images.device)


def test_evaluator_uses_eval_inference_mode_keeps_negatives_and_restores_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ml import evaluator
    from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator

    positive = torch.zeros((2, 5), dtype=torch.float32)
    positive[0] = torch.tensor([3, 100, 100, 20, 20])
    negative = torch.zeros((2, 5), dtype=torch.float32)
    dataset = _validation_dataset(targets=[positive, negative])
    loader = DataLoader(dataset, batch_size=2, shuffle=False)
    model = _NoDetectionModel().train()
    monkeypatch.setattr(evaluator, "assert_authorized_validation_dataset", lambda dataset: None)

    run = YOLOXEvaluator(
        model, loader, EvaluationConfig.from_model_metadata(_metadata()), device="cpu"
    ).evaluate()

    assert model.observed_eval is True
    assert model.observed_inference is True
    assert model.training is True
    assert run.samples == 2
    assert run.positive_images == 1
    assert run.negative_images == 1
    assert run.ground_truths[0].class_name == "D40"
    assert run.result.per_class["D40"].recall == 0.0


def test_evaluator_rejects_non_authorized_dataset_instance() -> None:
    from app.ml.evaluator import EvaluationConfig, EvaluationGateError, YOLOXEvaluator

    loader = DataLoader([torch.tensor(1)])
    with pytest.raises(EvaluationGateError, match="AuthorizedDetectionDataset"):
        YOLOXEvaluator(
            _NoDetectionModel(), loader, EvaluationConfig.from_model_metadata(_metadata())
        )


def test_evaluator_rejects_validation_rows_not_bound_to_authorized_manifest() -> None:
    from app.ml.evaluator import EvaluationConfig, EvaluationGateError, YOLOXEvaluator

    dataset = _validation_dataset(targets=[torch.zeros((2, 5))])
    loader = DataLoader(dataset, batch_size=1)
    with pytest.raises(EvaluationGateError, match="manifesto VALIDATION autorizado"):
        YOLOXEvaluator(
            _NoDetectionModel(), loader, EvaluationConfig.from_model_metadata(_metadata()), device="cpu"
        )


def test_training_engine_exposes_validation_evaluator_without_test_path() -> None:
    source = MODEL_METADATA_PATH.parents[2] / "backend/app/ml/training.py"
    text = source.read_text(encoding="utf-8")

    assert "evaluate_validation" in text
    assert 'manifest_for_evaluation("TEST")' not in text
