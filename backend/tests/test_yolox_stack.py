"""Smoke tests da stack oficial de visão do MODEL V1, sem treino nem pesos."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import pytest


def _stack_metadata() -> dict[str, object]:
    metadata_path = Path(__file__).parents[1] / "ml-stack.json"
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def test_pytorch_and_torchvision_import_with_pinned_versions() -> None:
    import torch
    import torchvision

    metadata = _stack_metadata()
    assert torch.__version__ == metadata["torch"]
    assert torchvision.__version__ == metadata["torchvision"]


def test_runtime_is_python_312() -> None:
    metadata = _stack_metadata()
    assert metadata["python"]["requires"] == ">=3.12,<3.13"
    assert sys.version_info[:2] == (3, 12)


def test_cuda_is_available_and_operational() -> None:
    import torch

    assert torch.cuda.is_available()
    assert torch.cuda.get_device_name(0)

    cpu_tensor = torch.tensor([1.0, 2.0, 3.0], device="cpu")
    result = (cpu_tensor.to("cuda") * 2).to("cpu")
    assert result.tolist() == [2.0, 4.0, 6.0]


def test_opencv_and_pycocotools_import_with_pinned_versions() -> None:
    import cv2
    import pycocotools

    metadata = _stack_metadata()
    assert version("opencv-python") == metadata["opencv_python"]
    assert cv2.__version__ == ".".join(str(metadata["opencv_python"]).split(".")[:3])
    assert version("pycocotools") == metadata["pycocotools"]
    assert Path(pycocotools.__file__).is_file()


def test_official_yolox_is_importable_from_versioned_third_party() -> None:
    import yolox

    metadata = _stack_metadata()
    assert yolox.__version__ == metadata["yolox"]["version"]
    expected_source = Path(__file__).parents[1] / "third_party" / "YOLOX" / "yolox"
    assert Path(yolox.__file__).resolve().is_relative_to(expected_source.resolve())
    with pytest.raises(PackageNotFoundError):
        distribution("yolox")


def test_yolox_s_model_instantiates_without_weights() -> None:
    from yolox.models import YOLOPAFPN, YOLOX, CSPDarknet, YOLOXHead

    from app.ml.yolox_model import instantiate_model

    model = instantiate_model()

    assert isinstance(model, YOLOX)
    assert isinstance(model.backbone, YOLOPAFPN)
    assert isinstance(model.backbone.backbone, CSPDarknet)
    for component in (
        "lateral_conv0",
        "C3_p4",
        "reduce_conv1",
        "C3_p3",
        "bu_conv2",
        "C3_n3",
        "bu_conv1",
        "C3_n4",
    ):
        assert getattr(model.backbone, component) is not None
    assert isinstance(model.head, YOLOXHead)
    assert model.head.num_classes == 4


def test_yolox_batch_contract_accepts_bchw_float_and_class_cxcywh_targets() -> None:
    import torch

    from app.ml.yolox_model import instantiate_model, validate_yolox_batch

    model = instantiate_model()
    images = torch.zeros((2, 3, 640, 640), dtype=torch.float32)
    targets = torch.zeros((2, 120, 5), dtype=torch.float32)
    targets[0, 0] = torch.tensor([3, 100, 120, 20, 30], dtype=torch.float32)

    validate_yolox_batch(model, images, targets)


@pytest.mark.parametrize(
    ("images", "targets", "error"),
    [
        ("uint8", "valid", TypeError),
        ("hwc", "valid", ValueError),
        ("valid", "wrong_target", ValueError),
        ("valid", "invalid_class", ValueError),
        ("valid", "invalid_padding", ValueError),
    ],
)
def test_yolox_batch_contract_rejects_incompatible_layouts(images, targets, error) -> None:
    import torch

    from app.ml.yolox_model import instantiate_model, validate_yolox_batch

    image_options = {
        "valid": torch.zeros((1, 3, 640, 640), dtype=torch.float32),
        "uint8": torch.zeros((1, 3, 640, 640), dtype=torch.uint8),
        "hwc": torch.zeros((1, 640, 640, 3), dtype=torch.float32),
    }
    target_options = {
        "valid": torch.zeros((1, 120, 5), dtype=torch.float32),
        "wrong_target": torch.zeros((1, 120, 4), dtype=torch.float32),
        "invalid_class": torch.tensor([[[4, 1, 1, 1, 1]]], dtype=torch.float32),
        "invalid_padding": torch.tensor([[[1, 0, 0, 0, 0]]], dtype=torch.float32),
    }
    with pytest.raises(error):
        validate_yolox_batch(instantiate_model(), image_options[images], target_options[targets])


def test_yolox_batch_transfers_to_cuda_without_forward() -> None:
    import torch

    from app.ml.yolox_model import instantiate_model, validate_yolox_batch

    if not torch.cuda.is_available():
        pytest.skip("CUDA indisponível neste host")
    images = torch.zeros((1, 3, 640, 640), dtype=torch.float32).to("cuda")
    targets = torch.zeros((1, 120, 5), dtype=torch.float32).to("cuda")

    validate_yolox_batch(instantiate_model(), images, targets)

    assert images.is_cuda and targets.is_cuda


def test_compatibility_metadata_is_authoritative_and_matches_runtime() -> None:
    import cv2
    import torch
    import torchvision
    import yolox

    from app.ml.yolox_model import MODEL_METADATA_PATH, YOLOX_COMMIT

    metadata = _stack_metadata()

    model_metadata = json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))
    assert metadata["model_metadata"] == "datasets/metadata/yolox_model_v1.json"
    assert metadata["torch"] == torch.__version__
    assert metadata["torchvision"] == torchvision.__version__
    assert metadata["pytorch_cuda_runtime"] == torch.version.cuda
    assert version("opencv-python") == metadata["opencv_python"]
    assert cv2.__version__ == ".".join(str(metadata["opencv_python"]).split(".")[:3])
    assert version("pycocotools") == metadata["pycocotools"]
    assert metadata["yolox"]["version"] == yolox.__version__
    assert model_metadata["source_commit"] == YOLOX_COMMIT
    assert metadata["readiness"]["model_stack"] == "MODEL_STACK_READY"
    assert metadata["readiness"]["data_model_interface"] is True
    assert metadata["readiness"]["training_engine"] is True
    assert metadata["readiness"]["evaluator"] is True
    assert metadata["readiness"]["checkpointing"] is True
    assert metadata["readiness"]["system_ready_for_training"] is False


def test_model_v1_metadata_is_complete_and_taxonomy_consistent() -> None:
    from app.ml.taxonomy import MODEL_V1_CANONICAL_CLASS_ORDER, MODEL_V1_CLASS_ORDER
    from app.ml.yolox_model import MODEL_METADATA_PATH, load_model_config

    config = load_model_config()
    assert config["architecture"] == "YOLOX-s"
    assert config["num_classes"] == 4
    assert tuple(config["class_names"]) == MODEL_V1_CLASS_ORDER == ("D00", "D10", "D20", "D40")
    assert tuple(config["canonical_class_names"]) == MODEL_V1_CANONICAL_CLASS_ORDER
    assert config["pretrained_policy"]["mode"] == "pretrained_official_optional"
    assert config["pretrained_policy"]["download_in_priority_5"] is False
    assert config["resume_policy"] == "urmind_checkpoint_full_state_fail_closed"
    assert MODEL_METADATA_PATH == Path(__file__).parents[2] / "datasets/metadata/yolox_model_v1.json"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("architecture", "YOLOX-m"),
        ("num_classes", 5),
        ("class_names", ["D10", "D00", "D20", "D40"]),
        ("canonical_class_names", ["URMIND_ROAD_D40"] * 4),
        ("source_commit", "0" * 40),
        ("license", "unknown"),
    ],
)
def test_invalid_model_metadata_fails_closed(tmp_path: Path, mutation: str, value: object) -> None:
    from app.ml.yolox_model import MODEL_METADATA_PATH, instantiate_model

    config = json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))
    config[mutation] = value
    invalid = tmp_path / "model.json"
    invalid.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        instantiate_model(config_path=invalid)


def test_readiness_cannot_be_promoted_by_model_metadata(tmp_path: Path) -> None:
    from app.ml.yolox_model import MODEL_METADATA_PATH, load_model_config

    config = deepcopy(json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8")))
    config["readiness"]["training_engine_ready"] = True
    config["readiness"]["system_ready_for_training"] = True
    invalid = tmp_path / "model.json"
    invalid.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="readiness"):
        load_model_config(invalid)


def test_yolox_submodule_matches_authoritative_commit() -> None:
    import subprocess

    backend = Path(__file__).parents[1]
    source = backend / "third_party" / "YOLOX"
    from app.ml.yolox_model import load_model_config

    metadata = load_model_config()
    actual_commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert actual_commit == metadata["source_commit"]
    license_text = (source / "LICENSE").read_text(encoding="utf-8").lstrip()
    assert license_text.startswith("Apache License\n")
    assert "Version 2.0, January 2004" in license_text
