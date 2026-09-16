"""Autoridade de instanciação do MODEL V1, sem treino ou download de pesos."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.ml.taxonomy import MODEL_V1_CANONICAL_CLASS_ORDER, MODEL_V1_CLASS_ORDER

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_METADATA_PATH = PROJECT_ROOT / "datasets/metadata/yolox_model_v1.json"
YOLOX_SOURCE_PATH = PROJECT_ROOT / "backend/third_party/YOLOX"


def load_model_config(path: Path = MODEL_METADATA_PATH) -> dict[str, Any]:
    """Lê e valida a configuração oficial, falhando fechado."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"metadata MODEL V1 ausente ou inválida: {path}") from exc
    validate_model_config(config)
    return config


def _submodule_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(YOLOX_SOURCE_PATH), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("submódulo YOLOX oficial indisponível") from exc


def validate_model_config(config: dict[str, Any]) -> None:
    """Garante arquitetura, proveniência, taxonomia e readiness do MODEL V1."""
    required = {
        "schema_version", "model_id", "architecture", "source_repository",
        "source_commit", "license", "num_classes", "class_names",
        "canonical_class_names", "input_size", "test_size", "preprocessing",
        "target_format", "cuda_capability", "pretrained_policy", "resume_policy",
        "readiness", "training", "evaluation", "checkpointing", "mlflow", "dry_run",
        "architecture_parameters",
    }
    if set(config) != required:
        raise ValueError("metadata MODEL V1 possui campos ausentes ou desconhecidos")
    if config["schema_version"] != 1 or config["model_id"] != "yolox-s-model-v1":
        raise ValueError("identidade/schema do MODEL V1 inválido")
    if config["architecture"] != "YOLOX-s":
        raise ValueError("architecture deve ser exatamente YOLOX-s")
    if config["num_classes"] != len(MODEL_V1_CLASS_ORDER):
        raise ValueError("num_classes diverge da taxonomia V1")
    if tuple(config["class_names"]) != MODEL_V1_CLASS_ORDER:
        raise ValueError("class_names diverge da ordem canônica V1")
    if tuple(config["canonical_class_names"]) != MODEL_V1_CANONICAL_CLASS_ORDER:
        raise ValueError("canonical_class_names diverge da taxonomia V1")
    if config["source_repository"] != "https://github.com/Megvii-BaseDetection/YOLOX":
        raise ValueError("source_repository não é o YOLOX oficial")
    if config["source_commit"] != _submodule_commit():
        raise ValueError("source_commit diverge do submódulo YOLOX")
    if config["license"] != "Apache-2.0":
        raise ValueError("licença do YOLOX divergente")
    expected_parameters = {
        "depth": 0.33,
        "width": 0.5,
        "in_channels": [256, 512, 1024],
        "activation": "silu",
    }
    if config["architecture_parameters"] != expected_parameters:
        raise ValueError("parâmetros não correspondem ao YOLOX-s oficial")
    for field in ("input_size", "test_size"):
        value = config[field]
        if not isinstance(value, list) or len(value) != 2 or any(
            not isinstance(item, int) or item <= 0 or item % 32 for item in value
        ):
            raise ValueError(f"{field} deve conter dois múltiplos positivos de 32")
    if config["pretrained_policy"]["mode"] != "pretrained_official_optional":
        raise ValueError("pretrained_policy inválida para o MODEL V1")
    if config["resume_policy"] != "urmind_checkpoint_full_state_fail_closed":
        raise ValueError("resume_policy deve exigir estado completo e compatibilidade")
    if config["checkpointing"] != {
        "enabled": True,
        "save_last": True,
        "save_best": True,
        "monitor": "map50_95",
        "mode": "max",
        "schema_version": 1,
    }:
        raise ValueError("checkpointing config divergente do contrato MODEL V1")
    if config["mlflow"] != {
        "tracking_mode": "local_sqlite",
        "tracking_directory": "mlruns",
        "experiment": "urmind-yolox-model-v1",
        "artifact_policy": "lightweight_metadata_and_references_only",
    }:
        raise ValueError("MLflow deve usar exclusivamente SQLite local")
    if config["dry_run"] != {
        "train_steps": 2,
        "validation_max_batches": 1,
        "batch_size": 1,
        "test_access": "forbidden",
        "artifact_policy": "temporary_checkpoints_and_lightweight_mlflow_only",
    }:
        raise ValueError("dry_run config divergente do contrato MODEL V1")
    if config["readiness"] != {
        "model_stack_ready": True,
        "data_model_interface_ready": True,
        "yolox_model_v1_ready": True,
        "training_engine_ready": True,
        "evaluator_ready": True,
        "checkpointing_ready": True,
        "mlflow_ready": True,
        "dvc_ready": True,
        "dry_run_ready": True,
        "system_ready_for_training": False,
    }:
        raise ValueError("readiness do MODEL V1 inconsistente")


_CONFIG = load_model_config()
YOLOX_SOURCE: str = _CONFIG["source_repository"]
YOLOX_COMMIT: str = _CONFIG["source_commit"]
YOLOX_MODEL_NAME: str = _CONFIG["architecture"]
MODEL_V1_NUM_CLASSES: int = _CONFIG["num_classes"]
YOLOX_INPUT_SIZE: tuple[int, int] = tuple(_CONFIG["input_size"])
YOLOX_MAX_LABELS: int = _CONFIG["target_format"]["max_labels"]
YOLOX_INTERFACE_BATCH_SIZE = 2
YOLOX_WINDOWS_NUM_WORKERS = 0


def instantiate_model(*, config_path: Path = MODEL_METADATA_PATH) -> Any:
    """Instancia o YOLOX-s oficial com head V1 e sem carregar pesos."""
    config = load_model_config(config_path)
    from yolox.models import YOLOPAFPN, YOLOX, YOLOXHead  # type: ignore[import-not-found]

    params = config["architecture_parameters"]
    backbone = YOLOPAFPN(
        params["depth"],
        params["width"],
        in_channels=params["in_channels"],
        act=params["activation"],
    )
    head = YOLOXHead(
        config["num_classes"],
        params["width"],
        in_channels=params["in_channels"],
        act=params["activation"],
    )
    model = YOLOX(backbone, head)
    if model.head.num_classes != config["num_classes"]:
        raise ValueError("head instanciada diverge da configuração oficial")
    return model


def validate_yolox_batch(model: Any, images: Any, targets: Any) -> None:
    """Valida a fronteira tensorial do YOLOX sem executar forward ou loss."""
    import torch

    if not isinstance(images, torch.Tensor) or images.dtype != torch.float32:
        raise TypeError("images deve ser torch.float32")
    if images.ndim != 4 or images.shape[1] != 3:
        raise ValueError("images deve ter layout BCHW com três canais")
    if not isinstance(targets, torch.Tensor) or targets.dtype != torch.float32:
        raise TypeError("targets deve ser torch.float32")
    if targets.ndim != 3 or targets.shape[0] != images.shape[0] or targets.shape[2] != 5:
        raise ValueError("targets deve ter layout BxMAX_LABELSx5")
    if images.device != targets.device:
        raise ValueError("images e targets devem estar no mesmo device")
    if not torch.isfinite(images).all() or images.min() < 0 or images.max() > 255:
        raise ValueError("images deve conter pixels finitos no intervalo 0..255")
    if not torch.isfinite(targets).all():
        raise ValueError("targets contém valor não finito")
    active = (targets[..., 3] > 0) | (targets[..., 4] > 0)
    if torch.any(active & ((targets[..., 3] <= 0) | (targets[..., 4] <= 0))):
        raise ValueError("target ativo deve ter width e height positivos")
    classes = targets[..., 0][active]
    if torch.any(classes != classes.floor()) or torch.any(classes < 0) or torch.any(
        classes >= MODEL_V1_NUM_CLASSES
    ):
        raise ValueError("class index fora da taxonomia V1")
    if torch.any(targets[~active] != 0):
        raise ValueError("padding de target deve ser inteiramente zero")
    height, width = images.shape[2:]
    if torch.any(targets[..., 1][active] < 0) or torch.any(targets[..., 1][active] > width):
        raise ValueError("target cx fora da imagem")
    if torch.any(targets[..., 2][active] < 0) or torch.any(targets[..., 2][active] > height):
        raise ValueError("target cy fora da imagem")
    if model.head.num_classes != MODEL_V1_NUM_CLASSES:
        raise ValueError("num_classes do modelo diverge da taxonomia V1")
    if images.shape[2] % 32 or images.shape[3] % 32:
        raise ValueError("altura e largura devem ser múltiplas de 32")
