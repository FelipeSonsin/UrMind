"""Único training engine e entrypoint do MODEL V1.

Reutiliza loss/forward, optimizer policy, scheduler e EMA do YOLOX oficial.
Integra evaluator e checkpoint/resume completo; TEST permanece inalcançável.
"""

from __future__ import annotations

import argparse
import configparser
import copy
import hashlib
import json
import os
import random
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import torch
from loguru import logger
from torch.utils.data import DataLoader
from yolox.exp.yolox_base import Exp as OfficialYOLOXExp  # type: ignore[import-not-found]
from yolox.utils import LRScheduler, ModelEMA, load_ckpt  # type: ignore[import-not-found]

from app.ml.detection_dataset import (
    AuthorizedDetectionDataset,
    build_yolox_dataloader,
    load_authorized_manifest,
)
from app.ml.taxonomy import MODEL_V1_CLASS_ORDER
from app.ml.yolox_model import (
    MODEL_METADATA_PATH,
    instantiate_model,
    load_model_config,
    validate_yolox_batch,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STACK_METADATA_PATH = PROJECT_ROOT / "backend/ml-stack.json"
READINESS_REPORT_PATH = PROJECT_ROOT / "datasets/reports/pre_training_readiness.json"
REGISTRY_PATH = PROJECT_ROOT / "datasets/metadata/artifact_registry.json"
AUTHORIZATION_STATUS_PATH = (
    PROJECT_ROOT / "datasets/reports/rdd2022_split_authorization_status.json"
)
SPLIT_MANIFEST_PATH = PROJECT_ROOT / "datasets/splits/rdd2022_subset_splits.json"
SELECTION_MANIFEST_PATH = (
    PROJECT_ROOT / "datasets/manifests/rdd2022_subset_selection.jsonl"
)
TRAIN_MANIFEST_PATH = PROJECT_ROOT / "datasets/manifests/detection_train_authorized.jsonl"
VALIDATION_MANIFEST_PATH = (
    PROJECT_ROOT / "datasets/manifests/detection_validation_authorized.jsonl"
)
CLASS_MAPPING_PATH = PROJECT_ROOT / "datasets/metadata/class_mapping.yaml"
DVC_CONFIG_PATH = PROJECT_ROOT / ".dvc/config"
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_REQUIRED_KEYS = {
    "checkpoint_schema_version",
    "model_state_dict",
    "ema_state_dict",
    "ema_updates",
    "optimizer_state_dict",
    "scheduler_state_dict",
    "scaler_state_dict",
    "epoch",
    "iteration",
    "global_step",
    "best_metric",
    "model_id",
    "architecture",
    "num_classes",
    "class_order",
    "config_fingerprint",
    "class_mapping_fingerprint",
    "dataset_fingerprint",
    "split_fingerprint",
    "training_metadata",
}


class TrainingGateError(RuntimeError):
    """Um contrato obrigatório bloqueou a construção do engine."""


@dataclass(frozen=True)
class TrainingConfig:
    model_id: str
    num_classes: int
    input_size: tuple[int, int]
    batch_size: int
    max_epoch: int
    optimizer: str
    basic_lr_per_image: float
    momentum: float
    weight_decay: float
    scheduler: str
    warmup_epochs: int
    warmup_lr: float
    min_lr_ratio: float
    no_aug_epochs: int
    amp: bool
    amp_initial_scale: float
    ema: bool
    workers: int
    seed: int
    augmentation: Mapping[str, Any]
    validation_interval_epochs: int
    checkpoint_interval_epochs: int
    log_interval_steps: int
    output_directory: str
    device: str
    local_availability_policy: str

    @classmethod
    def from_model_metadata(cls, metadata: Mapping[str, Any]) -> TrainingConfig:
        try:
            training = metadata["training"]
            config = cls(
                model_id=str(metadata["model_id"]),
                num_classes=int(metadata["num_classes"]),
                input_size=tuple(metadata["input_size"]),
                batch_size=training["batch_size"],
                max_epoch=training["max_epoch"],
                optimizer=training["optimizer"],
                basic_lr_per_image=training["basic_lr_per_image"],
                momentum=training["momentum"],
                weight_decay=training["weight_decay"],
                scheduler=training["scheduler"],
                warmup_epochs=training["warmup_epochs"],
                warmup_lr=training["warmup_lr"],
                min_lr_ratio=training["min_lr_ratio"],
                no_aug_epochs=training["no_aug_epochs"],
                amp=training["amp"],
                amp_initial_scale=training["amp_initial_scale"],
                ema=training["ema"],
                workers=training["workers"],
                seed=training["seed"],
                augmentation=training["augmentation"],
                validation_interval_epochs=training["validation_interval_epochs"],
                checkpoint_interval_epochs=training["checkpoint_interval_epochs"],
                log_interval_steps=training["log_interval_steps"],
                output_directory=training["output_directory"],
                device=training["device"],
                local_availability_policy=training["local_availability_policy"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("training config ausente ou malformada") from exc
        config.validate()
        return config

    def validate(self) -> None:
        if self.model_id != "yolox-s-model-v1" or self.num_classes != 4:
            raise ValueError("training config diverge do MODEL V1")
        if self.input_size != (640, 640):
            raise ValueError("input_size diverge da interface MODEL V1")
        if self.optimizer != "SGD":
            raise ValueError("optimizer deve seguir a política oficial SGD do YOLOX")
        if self.scheduler != "yoloxwarmcos":
            raise ValueError("scheduler deve ser yoloxwarmcos oficial")
        positive = (
            self.batch_size,
            self.max_epoch,
            self.basic_lr_per_image,
            self.amp_initial_scale,
            self.momentum,
            self.validation_interval_epochs,
            self.checkpoint_interval_epochs,
            self.log_interval_steps,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("parâmetros positivos da training config são inválidos")
        if self.workers < 0 or self.warmup_epochs < 0 or self.no_aug_epochs < 0:
            raise ValueError("workers/epochs de fase não podem ser negativos")
        if not 0 < self.min_lr_ratio <= 1 or self.weight_decay < 0:
            raise ValueError("min_lr_ratio/weight_decay inválidos")
        if self.device != "cuda":
            raise ValueError("device oficial deve ser cuda")
        if self.local_availability_policy != "fail_on_sample_access_no_hydration":
            raise ValueError("local_availability_policy deve falhar sem hidratação")

    def with_overrides(self, **changes: Any) -> TrainingConfig:
        updated = replace(self, **changes)
        if changes.keys() - {"batch_size", "amp", "device"}:
            raise ValueError("override não permitido fora do piloto controlado")
        if updated.batch_size <= 0:
            raise ValueError("batch_size deve ser positivo")
        return updated


@dataclass(frozen=True)
class PretrainedLoadReport:
    loaded: tuple[str, ...]
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    incompatible: tuple[str, ...]


@dataclass(frozen=True)
class StepResult:
    epoch: int
    iteration: int
    learning_rate: float
    total_loss: float
    iou_loss: float
    conf_loss: float
    cls_loss: float
    l1_loss: float
    num_fg: float
    elapsed_seconds: float
    peak_vram_bytes: int


@dataclass(frozen=True)
class ResumeState:
    epoch: int
    iteration: int
    global_step: int
    best_metric: float | None


@dataclass(frozen=True)
class DryRunResult:
    """Evidência serializável da travessia controlada do pipeline oficial."""

    train_samples: int
    train_positive: int
    train_negative: int
    train_multi_box: int
    train_batches: int
    train_steps: int
    losses: tuple[dict[str, float], ...]
    validation_samples: int
    validation_positive: int
    validation_negative: int
    validation_multi_box: int
    validation_metrics: Mapping[str, Any]
    checkpoint: Mapping[str, bool]
    mlflow_run_id: str
    test_records_resolved: int
    test_files_opened: int
    gpu: Mapping[str, Any]
    timings_seconds: Mapping[str, float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SplitAccessAudit:
    """Instrumenta a única fronteira de resolução de manifests do dry-run."""

    files_opened: dict[str, int]
    records_resolved: dict[str, int]

    @classmethod
    def empty(cls) -> SplitAccessAudit:
        return cls(files_opened={}, records_resolved={})

    def load(self, role: str) -> list[dict[str, Any]]:
        if role not in {"TRAIN", "VALIDATION"}:
            raise TrainingGateError("dry-run permite somente TRAIN e VALIDATION")
        path = manifest_for_role(role)
        self.files_opened[role] = self.files_opened.get(role, 0) + 1
        rows = load_authorized_manifest(path, intended_split=role)
        self.records_resolved[role] = self.records_resolved.get(role, 0) + len(rows)
        return rows

    def count(self, mapping: Mapping[str, int], role: str) -> int:
        return int(mapping.get(role, 0))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_fingerprints(
    metadata: Mapping[str, Any], config: TrainingConfig
) -> dict[str, str]:
    """Recalcula os bindings operacionais; readiness mutável não integra config."""
    config_fields = (
        "model_id",
        "architecture",
        "source_commit",
        "num_classes",
        "class_names",
        "canonical_class_names",
        "input_size",
        "evaluation",
        "checkpointing",
        "mlflow",
        "dry_run",
        "architecture_parameters",
    )
    config_document = {field: metadata[field] for field in config_fields}
    effective_training = asdict(config)
    effective_training.pop("output_directory")
    effective_training.pop("device")
    config_document["effective_training"] = effective_training
    dataset = {
        "TRAIN": _sha256(TRAIN_MANIFEST_PATH),
        "VALIDATION": _sha256(VALIDATION_MANIFEST_PATH),
        "selection": _sha256(SELECTION_MANIFEST_PATH),
        "authorization": _sha256(AUTHORIZATION_STATUS_PATH),
    }
    return {
        "config_fingerprint": _canonical_sha256(config_document),
        "class_mapping_fingerprint": _sha256(CLASS_MAPPING_PATH),
        "dataset_fingerprint": _canonical_sha256(dataset),
        "split_fingerprint": _sha256(SPLIT_MANIFEST_PATH),
    }


def manifest_for_role(role: str) -> Path:
    if role == "TRAIN":
        return TRAIN_MANIFEST_PATH
    if role == "VALIDATION":
        return VALIDATION_MANIFEST_PATH
    raise ValueError("TEST permanece selado na Prioridade 6")


def _dvc_local_ready() -> bool:
    parser = configparser.ConfigParser()
    try:
        parser.read(DVC_CONFIG_PATH, encoding="utf-8")
    except (OSError, configparser.Error):
        return False
    return (
        DVC_CONFIG_PATH.is_file()
        and parser.getboolean("core", "analytics", fallback=True) is False
        and not any(section.lower().startswith('remote "') for section in parser.sections())
    )


def validate_readiness(metadata: Mapping[str, Any]) -> None:
    try:
        stack = json.loads(STACK_METADATA_PATH.read_text(encoding="utf-8"))
        report = json.loads(READINESS_REPORT_PATH.read_text(encoding="utf-8"))
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        authorization = json.loads(AUTHORIZATION_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingGateError("readiness/registry ausente ou malformado") from exc
    gates = {
        "DATA_READY": stack["readiness"]["data"] is True,
        "MODEL_STACK_READY": stack["readiness"]["model_stack"] == "MODEL_STACK_READY",
        "DATA_MODEL_INTERFACE_READY": stack["readiness"]["data_model_interface"] is True,
        "MODEL_METADATA_INTERFACE_READY": (
            metadata["readiness"]["data_model_interface_ready"] is True
        ),
        "YOLOX_MODEL_V1_READY": metadata["readiness"]["yolox_model_v1_ready"] is True,
        "EVALUATOR_READY": (
            stack["readiness"]["evaluator"] is True
            and metadata["readiness"]["evaluator_ready"] is True
            and report["evaluator_ready"] is True
        ),
        "CHECKPOINTING_READY": (
            stack["readiness"]["checkpointing"] is True
            and metadata["readiness"]["checkpointing_ready"] is True
            and report["checkpointing_ready"] is True
        ),
        "MLFLOW_READY": (
            stack["readiness"]["mlflow"] is True
            and metadata["readiness"]["mlflow_ready"] is True
            and report["mlflow_ready"] is True
            and metadata["mlflow"]["tracking_mode"] == "local_sqlite"
        ),
        "DVC_READY": (
            stack["readiness"]["dvc"] is True
            and metadata["readiness"]["dvc_ready"] is True
            and report["dvc_ready"] is True
            and _dvc_local_ready()
        ),
        "AUTHORIZED_DATA": report["data_manifest_schema_ready"] is True,
        "SPLIT_AUTHORIZATION": (
            authorization["status"] == "AUTHORIZED_FOR_MODEL_V1"
            and authorization["decision"] == "APPROVED_FOR_MODEL_V1"
            and authorization["binding_mismatches"] == []
            and authorization["split_manifest_sha256"] == _sha256(SPLIT_MANIFEST_PATH)
            and authorization["selection_manifest_sha256"]
            == _sha256(SELECTION_MANIFEST_PATH)
        ),
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise TrainingGateError(f"readiness gate bloqueado: {', '.join(failed)}")
    entries = {entry["path"]: entry for entry in registry.get("artifacts", [])}
    for path in (
        STACK_METADATA_PATH,
        MODEL_METADATA_PATH,
        TRAIN_MANIFEST_PATH,
        VALIDATION_MANIFEST_PATH,
        AUTHORIZATION_STATUS_PATH,
    ):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if relative not in entries or entries[relative].get("sha256") != _sha256(path):
            raise TrainingGateError(f"artifact registry stale/ausente: {relative}")


def resolve_device(requested: str) -> torch.device:
    if requested != "cuda":
        raise TrainingGateError("somente device cuda é autorizado nesta configuração")
    if not torch.cuda.is_available():
        raise TrainingGateError("CUDA solicitado, mas indisponível; fallback CPU proibido")
    return torch.device("cuda")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def load_pretrained_compatible(
    model: torch.nn.Module, path: Path | None
) -> PretrainedLoadReport:
    if path is None:
        raise ValueError("pretrained exige path explícito; download automático é proibido")
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = torch.load(path, map_location="cpu", weights_only=True)
    source = payload.get("model", payload) if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        raise TypeError("checkpoint não contém state_dict válido")
    target = model.state_dict()
    compatible: dict[str, torch.Tensor] = {}
    incompatible: list[str] = []
    unexpected: list[str] = []
    for key, value in source.items():
        if key not in target:
            if key.startswith("head."):
                incompatible.append(key)
            else:
                unexpected.append(key)
        elif not isinstance(value, torch.Tensor) or value.shape != target[key].shape:
            incompatible.append(key)
        else:
            compatible[key] = value
    load_ckpt(model, compatible)
    missing = sorted(set(target) - set(compatible))
    return PretrainedLoadReport(
        loaded=tuple(sorted(compatible)),
        missing=tuple(missing),
        unexpected=tuple(sorted(unexpected)),
        incompatible=tuple(sorted(incompatible)),
    )


def _official_optimizer(
    model: torch.nn.Module, config: TrainingConfig
) -> torch.optim.Optimizer:
    exp = OfficialYOLOXExp()
    exp.model = model
    exp.warmup_epochs = config.warmup_epochs
    exp.warmup_lr = config.warmup_lr
    exp.basic_lr_per_img = config.basic_lr_per_image
    exp.momentum = config.momentum
    exp.weight_decay = config.weight_decay
    return exp.get_optimizer(config.batch_size)


def make_grad_scaler(
    device: torch.device, *, enabled: bool, initial_scale: float
) -> Any:
    return torch.amp.GradScaler(device.type, enabled=enabled, init_scale=initial_scale)


class TrainingEngine:
    def __init__(
        self,
        model: torch.nn.Module,
        optimizer: torch.optim.Optimizer,
        scheduler: LRScheduler,
        config: TrainingConfig,
        device: torch.device,
        tracker: Any | None = None,
    ) -> None:
        self.model = model
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.config = config
        self.device = device
        self.tracker = tracker
        self.scaler = make_grad_scaler(
            device, enabled=config.amp, initial_scale=config.amp_initial_scale
        )
        self.ema = ModelEMA(model, 0.9998) if config.ema else None
        self.current_epoch = 0
        self.current_iteration = 0
        self.global_step = 0
        self.best_metric: float | None = None

    def prepare_epoch(self, epoch: int) -> None:
        """Aplica somente a transição oficial de loss da fase no-augmentation."""
        if epoch >= self.config.max_epoch - self.config.no_aug_epochs:
            head: Any = getattr(self.model, "head", None)
            if head is None:
                raise TrainingGateError("modelo sem YOLOXHead na transição no-aug")
            head.use_l1 = True

    def train_step(
        self,
        batch: Sequence[Any],
        *,
        epoch: int,
        iteration: int,
    ) -> StepResult:
        started = time.perf_counter()
        self.prepare_epoch(epoch)
        self.model.train()
        images = batch[0].to(self.device, non_blocking=False)
        targets = batch[1].to(self.device, non_blocking=False)
        validate_yolox_batch(self.model, images, targets)
        if self.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(self.device)
        self.optimizer.zero_grad(set_to_none=True)
        with torch.amp.autocast(self.device.type, enabled=self.config.amp):
            outputs = self.model(images, targets)
            loss = outputs["total_loss"]
        if not torch.isfinite(loss).all():
            self.optimizer.zero_grad(set_to_none=True)
            raise FloatingPointError("YOLOX total_loss não finita; step abortado")
        self.scaler.scale(loss).backward()
        self.scaler.unscale_(self.optimizer)
        gradients = [
            (name, parameter.grad)
            for name, parameter in self.model.named_parameters()
            if parameter.grad is not None
        ]
        non_finite = [
            name for name, gradient in gradients if not torch.isfinite(gradient).all()
        ]
        if not gradients or non_finite:
            self.optimizer.zero_grad(set_to_none=True)
            detail = ", ".join(non_finite[:8]) or "nenhum gradient produzido"
            raise FloatingPointError(
                f"gradient ausente ou não finito ({detail}); optimizer.step abortado"
            )
        self.scaler.step(self.optimizer)
        self.scaler.update()
        if self.ema is not None:
            self.ema.update(self.model)
        progress = epoch * self.scheduler.iters_per_epoch + iteration + 1
        lr = self.scheduler.update_lr(progress)
        for group in self.optimizer.param_groups:
            group["lr"] = lr
        self.current_epoch = epoch
        self.current_iteration = iteration
        self.global_step = progress
        peak = torch.cuda.max_memory_allocated(self.device) if self.device.type == "cuda" else 0
        values = {key: _loss_float(outputs[key]) for key in _LOSS_KEYS}
        result = StepResult(
            epoch=epoch,
            iteration=iteration,
            learning_rate=lr,
            elapsed_seconds=time.perf_counter() - started,
            peak_vram_bytes=peak,
            **values,
        )
        logger.info(
            "epoch={} iteration={} lr={:.6g} total_loss={:.6g} iou_loss={:.6g} "
            "conf_loss={:.6g} cls_loss={:.6g} l1_loss={:.6g} elapsed={:.3f}s vram={}",
            epoch,
            iteration,
            lr,
            result.total_loss,
            result.iou_loss,
            result.conf_loss,
            result.cls_loss,
            result.l1_loss,
            result.elapsed_seconds,
            peak,
        )
        if self.tracker is not None:
            self.tracker.log_training_step(result, global_step=self.global_step)
        return result

    @contextmanager
    def validation_mode(self) -> Iterator[None]:
        was_training = self.model.training
        self.model.eval()
        try:
            with torch.inference_mode():
                yield
        finally:
            self.model.train(was_training)

    def evaluate_validation(
        self, validation_loader: DataLoader, *, max_batches: int | None = None
    ) -> Any:
        """Executa somente VALIDATION; checkpoint selection não pertence a esta prioridade."""
        from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator

        metadata = load_model_config()
        evaluator = YOLOXEvaluator(
            self.model,
            validation_loader,
            EvaluationConfig.from_model_metadata(metadata),
            device=self.device,
        )
        run = evaluator.evaluate(max_batches=max_batches)
        if self.tracker is not None:
            self.tracker.log_validation(
                run.result.as_persisted(), global_step=self.global_step
            )
        return run

    @property
    def checkpoint_directory(self) -> Path:
        path = Path(self.config.output_directory)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def checkpoint_path(self, kind: str) -> Path:
        if kind not in {"last", "best"}:
            raise ValueError("checkpoint deve ser last ou best")
        return self.checkpoint_directory / f"{kind}.pt"

    def _scheduler_state(self) -> dict[str, Any]:
        return {
            "lr": self.scheduler.lr,
            "iters_per_epoch": self.scheduler.iters_per_epoch,
            "total_epochs": self.scheduler.total_epochs,
            "last_lrs": [float(group["lr"]) for group in self.optimizer.param_groups],
        }

    def _checkpoint_payload(
        self, training_metadata: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        metadata = load_model_config()
        fingerprints = checkpoint_fingerprints(metadata, self.config)
        return {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "model_state_dict": self.model.state_dict(),
            "ema_state_dict": self.ema.ema.state_dict() if self.ema is not None else None,
            "ema_updates": self.ema.updates if self.ema is not None else None,
            "optimizer_state_dict": self.optimizer.state_dict(),
            "scheduler_state_dict": self._scheduler_state(),
            "scaler_state_dict": self.scaler.state_dict(),
            "epoch": self.current_epoch,
            "iteration": self.current_iteration,
            "global_step": self.global_step,
            "best_metric": self.best_metric,
            "model_id": metadata["model_id"],
            "architecture": metadata["architecture"],
            "num_classes": metadata["num_classes"],
            "class_order": list(metadata["class_names"]),
            **fingerprints,
            "training_metadata": dict(training_metadata or {}),
        }

    @staticmethod
    def _atomic_save(payload: Mapping[str, Any], destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as handle:
                torch.save(dict(payload), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def save_last(
        self, *, training_metadata: Mapping[str, Any] | None = None
    ) -> Path:
        path = self.checkpoint_path("last")
        self._atomic_save(self._checkpoint_payload(training_metadata), path)
        if self.tracker is not None:
            self.tracker.log_checkpoint_reference(path, kind="last")
        return path

    def save_best(
        self,
        metric: float,
        *,
        source: str,
        training_metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        if source != "VALIDATION":
            raise ValueError("BEST aceita exclusivamente métrica de VALIDATION")
        value = float(metric)
        if not np.isfinite(value):
            raise ValueError("métrica de VALIDATION deve ser finita")
        if self.best_metric is not None and value <= self.best_metric:
            return False
        previous = self.best_metric
        self.best_metric = value
        try:
            self._atomic_save(
                self._checkpoint_payload(training_metadata), self.checkpoint_path("best")
            )
        except Exception:
            self.best_metric = previous
            raise
        if self.tracker is not None:
            self.tracker.log_checkpoint_reference(
                self.checkpoint_path("best"), kind="best"
            )
        return True

    def _validate_resume_payload(self, payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("checkpoint não contém mapping de estado")
        if set(payload) != CHECKPOINT_REQUIRED_KEYS:
            raise ValueError("checkpoint possui keys obrigatórias ausentes ou desconhecidas")
        metadata = load_model_config()
        expected = {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "model_id": metadata["model_id"],
            "architecture": metadata["architecture"],
            "num_classes": metadata["num_classes"],
            "class_order": list(MODEL_V1_CLASS_ORDER),
            **checkpoint_fingerprints(metadata, self.config),
        }
        labels = {
            "checkpoint_schema_version": "schema",
            "model_id": "model_id",
            "architecture": "architecture",
            "num_classes": "num_classes",
            "class_order": "class order",
            "config_fingerprint": "config fingerprint",
            "class_mapping_fingerprint": "class mapping fingerprint",
            "dataset_fingerprint": "dataset fingerprint",
            "split_fingerprint": "split fingerprint",
        }
        for field, expected_value in expected.items():
            if payload[field] != expected_value:
                raise ValueError(f"checkpoint incompatível: {labels[field]}")
        for field in ("epoch", "iteration", "global_step"):
            if not isinstance(payload[field], int) or payload[field] < 0:
                raise ValueError(f"checkpoint incompatível: {field}")
        if payload["best_metric"] is not None and not isinstance(
            payload["best_metric"], (int, float)
        ):
            raise ValueError("checkpoint incompatível: best_metric")
        if self.ema is None and payload["ema_state_dict"] is not None:
            raise ValueError("checkpoint incompatível: EMA")
        if self.ema is not None and payload["ema_state_dict"] is None:
            raise ValueError("checkpoint incompatível: EMA ausente")
        return payload

    def _move_optimizer_state_to_device(self) -> None:
        for state in self.optimizer.state.values():
            for key, value in state.items():
                if isinstance(value, torch.Tensor):
                    state[key] = value.to(self.device)

    def resume(self, path: Path) -> ResumeState:
        expected_parent = self.checkpoint_directory.resolve()
        resolved = path.resolve()
        if resolved.parent != expected_parent or resolved.name not in {"last.pt", "best.pt"}:
            raise ValueError("resume exige nome e diretório oficial de checkpoint")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        try:
            raw = torch.load(resolved, map_location="cpu", weights_only=True)
        except Exception as exc:
            raise ValueError("checkpoint ilegível ou corrompido") from exc
        payload = self._validate_resume_payload(raw)
        snapshot = {
            "model": copy.deepcopy(self.model.state_dict()),
            "optimizer": copy.deepcopy(self.optimizer.state_dict()),
            "scaler": copy.deepcopy(self.scaler.state_dict()),
            "ema": copy.deepcopy(self.ema.ema.state_dict()) if self.ema is not None else None,
            "ema_updates": self.ema.updates if self.ema is not None else None,
            "epoch": self.current_epoch,
            "iteration": self.current_iteration,
            "global_step": self.global_step,
            "best_metric": self.best_metric,
        }
        try:
            self.model.load_state_dict(payload["model_state_dict"], strict=True)
            self.optimizer.load_state_dict(payload["optimizer_state_dict"])
            self._move_optimizer_state_to_device()
            scheduler_state = payload["scheduler_state_dict"]
            if (
                not isinstance(scheduler_state, Mapping)
                or scheduler_state.get("lr") != self.scheduler.lr
                or scheduler_state.get("iters_per_epoch") != self.scheduler.iters_per_epoch
                or scheduler_state.get("total_epochs") != self.scheduler.total_epochs
            ):
                raise ValueError("checkpoint incompatível: scheduler")
            last_lrs = scheduler_state.get("last_lrs")
            if not isinstance(last_lrs, list) or len(last_lrs) != len(
                self.optimizer.param_groups
            ):
                raise ValueError("checkpoint incompatível: scheduler state")
            for group, lr in zip(self.optimizer.param_groups, last_lrs, strict=True):
                group["lr"] = float(lr)
            self.scaler.load_state_dict(payload["scaler_state_dict"])
            if self.ema is not None:
                self.ema.ema.load_state_dict(payload["ema_state_dict"], strict=True)
                self.ema.ema.to(self.device)
                self.ema.updates = int(payload["ema_updates"])
            self.current_epoch = payload["epoch"]
            self.current_iteration = payload["iteration"]
            self.global_step = payload["global_step"]
            self.best_metric = payload["best_metric"]
        except Exception:
            self.model.load_state_dict(snapshot["model"], strict=True)
            self.optimizer.load_state_dict(snapshot["optimizer"])
            self.scaler.load_state_dict(snapshot["scaler"])
            if self.ema is not None:
                self.ema.ema.load_state_dict(snapshot["ema"], strict=True)
                self.ema.updates = snapshot["ema_updates"]
            self.current_epoch = snapshot["epoch"]
            self.current_iteration = snapshot["iteration"]
            self.global_step = snapshot["global_step"]
            self.best_metric = snapshot["best_metric"]
            raise
        return ResumeState(
            epoch=self.current_epoch,
            iteration=self.current_iteration,
            global_step=self.global_step,
            best_metric=self.best_metric,
        )


_LOSS_KEYS = ("total_loss", "iou_loss", "conf_loss", "cls_loss", "l1_loss", "num_fg")


def _loss_float(value: Any) -> float:
    if isinstance(value, torch.Tensor):
        return float(value.detach().float().cpu())
    return float(value)


def build_loaders(config: TrainingConfig) -> tuple[DataLoader, DataLoader]:
    train_rows = load_authorized_manifest(manifest_for_role("TRAIN"), intended_split="TRAIN")
    validation_rows = load_authorized_manifest(
        manifest_for_role("VALIDATION"), intended_split="VALIDATION"
    )
    train_dataset = AuthorizedDetectionDataset(train_rows, mode="train")
    validation_dataset = AuthorizedDetectionDataset(validation_rows, mode="validation")
    train_loader = build_yolox_dataloader(
        train_dataset,
        batch_size=config.batch_size,
        num_workers=config.workers,
        shuffle=True,
    )
    validation_loader = build_yolox_dataloader(
        validation_dataset,
        batch_size=config.batch_size,
        num_workers=config.workers,
        shuffle=False,
    )
    return train_loader, validation_loader


def build_training_engine(
    *,
    metadata: Mapping[str, Any] | None = None,
    device: str | None = None,
    pretrained_path: Path | None = None,
    resume_path: Path | None = None,
    batch_size: int | None = None,
    iters_per_epoch: int = 1,
    tracker: Any | None = None,
) -> TrainingEngine:
    if pretrained_path is not None and resume_path is not None:
        raise ValueError("pretrained e resume são operações mutuamente exclusivas")
    document = load_model_config() if metadata is None else dict(metadata)
    validate_readiness(document)
    config = TrainingConfig.from_model_metadata(document)
    overrides: dict[str, Any] = {}
    if batch_size is not None:
        overrides["batch_size"] = batch_size
    if device is not None:
        overrides["device"] = device
    if overrides:
        config = config.with_overrides(**overrides)
    resolved = resolve_device(config.device) if config.device == "cuda" else torch.device("cpu")
    seed_everything(config.seed)
    model = instantiate_model().to(resolved)
    exp = OfficialYOLOXExp()
    exp.model = model
    model = exp.get_model()
    if pretrained_path is not None:
        report = load_pretrained_compatible(model, pretrained_path)
        logger.info("pretrained load report: {}", report)
    optimizer = _official_optimizer(model, config)
    scheduler = LRScheduler(
        config.scheduler,
        config.basic_lr_per_image * config.batch_size,
        iters_per_epoch,
        config.max_epoch,
        warmup_epochs=config.warmup_epochs,
        warmup_lr_start=config.warmup_lr,
        no_aug_epochs=config.no_aug_epochs,
        min_lr_ratio=config.min_lr_ratio,
    )
    engine = TrainingEngine(model, optimizer, scheduler, config, resolved, tracker=tracker)
    if resume_path is not None:
        engine.resume(resume_path)
    return engine


def _first_usable_row(
    rows: Sequence[dict[str, Any]],
    *,
    mode: str,
    predicate: Any,
) -> dict[str, Any]:
    """Seleciona deterministicamente uma amostra autorizada e local, sem fallback."""
    for row in rows:
        if not predicate(row):
            continue
        dataset = AuthorizedDetectionDataset([row], mode=mode)  # type: ignore[arg-type]
        try:
            dataset[0]
        except Exception as exc:
            if "CLOUD_ONLY_SAMPLE" in str(exc):
                continue
            raise
        return row
    raise TrainingGateError(f"nenhuma amostra local autorizada atende ao dry-run {mode}")


def _state_digest(value: Any) -> str:
    """Digest determinístico de state_dicts para comprovar restauração exata."""
    digest = hashlib.sha256()

    def update(item: Any) -> None:
        if isinstance(item, torch.Tensor):
            tensor = item.detach().cpu().contiguous()
            digest.update(str(tensor.dtype).encode())
            digest.update(str(tuple(tensor.shape)).encode())
            digest.update(tensor.numpy().tobytes())
        elif isinstance(item, Mapping):
            for key in sorted(item, key=str):
                digest.update(str(key).encode())
                update(item[key])
        elif isinstance(item, (list, tuple)):
            for child in item:
                update(child)
        else:
            digest.update(repr(item).encode())

    update(value)
    return digest.hexdigest()


def run_controlled_dry_run(
    *, tracking_root: Path | None = None
) -> DryRunResult:
    """Atravessa 2 steps TRAIN e 1 batch VALIDATION; nunca resolve TEST."""
    from app.ml.tracking import MLflowTracker, TrackingError

    metadata = load_model_config()
    validate_readiness(metadata)
    policy = metadata["dry_run"]
    if policy != {
        "train_steps": 2,
        "validation_max_batches": 1,
        "batch_size": 1,
        "test_access": "forbidden",
        "artifact_policy": "temporary_checkpoints_and_lightweight_mlflow_only",
    }:
        raise TrainingGateError("política autoritativa de dry-run divergente")

    split_audit = SplitAccessAudit.empty()
    train_rows = split_audit.load("TRAIN")
    validation_rows = split_audit.load("VALIDATION")
    train_multi = _first_usable_row(
        train_rows, mode="train", predicate=lambda row: len(row["boxes"]) > 1
    )
    train_negative = _first_usable_row(
        train_rows, mode="train", predicate=lambda row: not row["boxes"]
    )
    validation_multi = _first_usable_row(
        validation_rows,
        mode="validation",
        predicate=lambda row: len(row["boxes"]) > 1,
    )
    validation_negative = _first_usable_row(
        validation_rows, mode="validation", predicate=lambda row: not row["boxes"]
    )
    selected_train = [train_multi, train_negative]
    selected_validation = [validation_multi, validation_negative]
    train_dataset = AuthorizedDetectionDataset(selected_train, mode="train")
    validation_dataset = AuthorizedDetectionDataset(
        selected_validation, mode="validation"
    )
    train_loader = build_yolox_dataloader(
        train_dataset, batch_size=1, num_workers=0, pin_memory=True, shuffle=False
    )
    validation_loader = build_yolox_dataloader(
        validation_dataset, batch_size=2, num_workers=0, pin_memory=True, shuffle=False
    )

    base_config = TrainingConfig.from_model_metadata(metadata).with_overrides(batch_size=1)
    checkpoint_root = Path(
        tempfile.mkdtemp(prefix="urmind-dry-run-checkpoints-", dir=PROJECT_ROOT)
    )
    dry_config = replace(
        base_config,
        output_directory=checkpoint_root.relative_to(PROJECT_ROOT).as_posix(),
    )
    tracker = MLflowTracker(metadata, dry_config, tracking_root=tracking_root)
    run_id = tracker.start_run(
        run_name="priority-10-controlled-dry-run",
        tags={
            "run_type": "dry_run",
            "split_policy": "TRAIN_OPTIMIZATION_VALIDATION_ONLY",
            "test_access": "forbidden",
            "mlflow.source.name": "backend/app/ml/training.py",
        },
    )
    tracker.log_effective_config()
    timings: dict[str, float] = {}
    checkpoint_checks: dict[str, bool] = {}
    try:
        engine = build_training_engine(
            batch_size=1, iters_per_epoch=2, tracker=tracker
        )
        engine.config = dry_config
        torch.cuda.reset_peak_memory_stats(engine.device)
        results: list[StepResult] = []
        iterator = iter(train_loader)
        for iteration in range(2):
            load_started = time.perf_counter()
            batch = next(iterator)
            timings[f"train_batch_{iteration}_load"] = time.perf_counter() - load_started
            results.append(engine.train_step(batch, epoch=0, iteration=iteration))

        validation_started = time.perf_counter()
        validation = engine.evaluate_validation(validation_loader, max_batches=1)
        timings["validation_inference"] = time.perf_counter() - validation_started
        metric = validation.result.map50_95
        if metric is None:
            raise TrainingGateError("dry-run VALIDATION produziu map50_95 indefinido")
        engine.save_best(
            metric, source="VALIDATION", training_metadata={"run_type": "dry_run"}
        )
        last_path = engine.save_last(training_metadata={"run_type": "dry_run"})
        before = {
            "model": _state_digest(engine.model.state_dict()),
            "optimizer": _state_digest(engine.optimizer.state_dict()),
            "scheduler": _state_digest(engine._scheduler_state()),
            "scaler": _state_digest(engine.scaler.state_dict()),
            "ema": _state_digest(engine.ema.ema.state_dict()) if engine.ema else "",
            "epoch": engine.current_epoch,
            "global_step": engine.global_step,
            "best_metric": engine.best_metric,
        }
        restored = build_training_engine(batch_size=1, iters_per_epoch=2)
        restored.config = dry_config
        resume = restored.resume(last_path)
        checkpoint_checks = {
            "save": last_path.is_file() and engine.checkpoint_path("best").is_file(),
            "atomic": not any(checkpoint_root.glob("*.tmp")),
            "load": resume.global_step == before["global_step"],
            "model_restored": _state_digest(restored.model.state_dict()) == before["model"],
            "optimizer_restored": _state_digest(restored.optimizer.state_dict()) == before["optimizer"],
            "scheduler_restored": _state_digest(restored._scheduler_state()) == before["scheduler"],
            "scaler_restored": _state_digest(restored.scaler.state_dict()) == before["scaler"],
            "ema_restored": restored.ema is not None
            and _state_digest(restored.ema.ema.state_dict()) == before["ema"],
            "step_restored": resume.epoch == before["epoch"] and resume.global_step == before["global_step"],
            "best_metric_restored": resume.best_metric == before["best_metric"],
            "fingerprints_validated": True,
        }
        if not all(checkpoint_checks.values()):
            raise TrainingGateError(f"resume do dry-run divergiu: {checkpoint_checks}")
        tracker.log_lightweight_artifact(
            "dry-run-summary.json",
            {
                "train_steps": 2,
                "validation_samples": validation.samples,
                "test_records_resolved": split_audit.count(
                    split_audit.records_resolved, "TEST"
                ),
                "test_files_opened": split_audit.count(
                    split_audit.files_opened, "TEST"
                ),
                "checkpoint_checks": checkpoint_checks,
            },
            artifact_path="run-metadata",
        )
        gpu = {
            "name": torch.cuda.get_device_name(engine.device),
            "allocated_bytes": torch.cuda.memory_allocated(engine.device),
            "reserved_bytes": torch.cuda.memory_reserved(engine.device),
            "peak_vram_bytes": torch.cuda.max_memory_allocated(engine.device),
        }
        result = DryRunResult(
            train_samples=2,
            train_positive=1,
            train_negative=1,
            train_multi_box=1,
            train_batches=2,
            train_steps=2,
            losses=tuple(
                {
                    "total_loss": item.total_loss,
                    "iou_loss": item.iou_loss,
                    "objectness_loss": item.conf_loss,
                    "classification_loss": item.cls_loss,
                    "l1_loss": item.l1_loss,
                    "forward_backward": item.elapsed_seconds,
                }
                for item in results
            ),
            validation_samples=validation.samples,
            validation_positive=validation.positive_images,
            validation_negative=validation.negative_images,
            validation_multi_box=validation.multi_box_images,
            validation_metrics=validation.result.as_persisted(),
            checkpoint=checkpoint_checks,
            mlflow_run_id=run_id,
            test_records_resolved=split_audit.count(
                split_audit.records_resolved, "TEST"
            ),
            test_files_opened=split_audit.count(split_audit.files_opened, "TEST"),
            gpu=gpu,
            timings_seconds=timings,
        )
    except Exception as dry_run_error:
        try:
            tracker.end_run(status="FAILED")
        except TrackingError as tracking_error:
            raise ExceptionGroup(
                "dry-run e tracking falharam independentemente",
                [dry_run_error, tracking_error],
            ) from None
        raise
    else:
        tracker.end_run()
        return result
    finally:
        shutil.rmtree(checkpoint_root, ignore_errors=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--single-step", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--pretrained", type=Path)
    parser.add_argument("--resume", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from app.ml.tracking import MLflowTracker, TrackingError

    args = _parser().parse_args(argv)
    if args.dry_run:
        if args.pretrained is not None or args.resume is not None or args.batch_size is not None:
            raise ValueError("dry-run usa exclusivamente sua política autoritativa")
        print(json.dumps(run_controlled_dry_run().as_dict(), ensure_ascii=False, indent=2))
        return 0
    metadata = load_model_config()
    validate_readiness(metadata)
    config = TrainingConfig.from_model_metadata(metadata)
    if args.batch_size is not None:
        config = config.with_overrides(batch_size=args.batch_size)
    train_loader, validation_loader = build_loaders(config)
    tracker = MLflowTracker(metadata, config)
    tracker.start_run(tags={"split_policy": "TRAIN_OPTIMIZATION_VALIDATION_ONLY"})
    tracker.log_effective_config()
    try:
        engine = build_training_engine(
            pretrained_path=args.pretrained,
            resume_path=args.resume,
            batch_size=args.batch_size,
            iters_per_epoch=len(train_loader),
            tracker=tracker,
        )
        if args.single_step:
            engine.train_step(next(iter(train_loader)), epoch=0, iteration=0)
        else:
            start_epoch = engine.current_epoch + 1 if args.resume is not None else 0
            for epoch in range(start_epoch, config.max_epoch):
                engine.prepare_epoch(epoch)
                for iteration, batch in enumerate(train_loader):
                    engine.train_step(batch, epoch=epoch, iteration=iteration)
                if (epoch + 1) % config.validation_interval_epochs == 0:
                    validation = engine.evaluate_validation(validation_loader)
                    logger.info("validation metrics: {}", validation.result.as_persisted())
                    metric = validation.result.map50_95
                    if metric is None:
                        raise TrainingGateError(
                            "VALIDATION map50_95 indefinido; BEST não pode ser selecionado"
                        )
                    engine.save_best(
                        metric,
                        source="VALIDATION",
                        training_metadata={"completed_epoch": epoch},
                    )
                if (epoch + 1) % config.checkpoint_interval_epochs == 0:
                    engine.save_last(training_metadata={"completed_epoch": epoch})
    except Exception as training_error:
        try:
            tracker.end_run(status="FAILED")
        except TrackingError as tracking_error:
            raise ExceptionGroup(
                "training e tracking falharam independentemente",
                [training_error, tracking_error],
            ) from None
        raise
    tracker.end_run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
