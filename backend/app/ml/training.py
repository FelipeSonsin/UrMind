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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import torch
from loguru import logger
from torch.utils.data import DataLoader
from yolox.exp.yolox_base import Exp as OfficialYOLOXExp  # type: ignore[import-not-found]
from yolox.utils import LRScheduler, ModelEMA, load_ckpt  # type: ignore[import-not-found]

from app.ml.detection_dataset import (
    AuthorizedDetectionDataset,
    build_train_dataset,
    build_yolox_dataloader,
    load_authorized_manifest,
)
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
SELECTION_MANIFEST_PATH = PROJECT_ROOT / "datasets/manifests/rdd2022_subset_selection.jsonl"
TRAIN_MANIFEST_PATH = PROJECT_ROOT / "datasets/manifests/detection_train_authorized.jsonl"
VALIDATION_MANIFEST_PATH = PROJECT_ROOT / "datasets/manifests/detection_validation_authorized.jsonl"
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


# Pulos de passo por overflow AMP tolerados em sequência antes de declarar divergência.
MAX_CONSECUTIVE_AMP_SKIPS = 10
# Teto de workers da validation; ver build_loaders.
VALIDATION_MAX_WORKERS = 4


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
    # Imutável de propósito: a config é frozen e o default não pode ser dict.
    dataset: Mapping[str, Any] = MappingProxyType({})
    # V2: quais representações são medidas em VALIDATION e qual decide o BEST.
    evaluation_representations: tuple[str, ...] = ("raw",)
    selection_representation: str = "raw"
    # V2: {"monitor", "patience_evaluations", "min_delta"}; None mantém o V1.
    early_stopping: Mapping[str, Any] | None = None
    train_num_workers: int = 0
    validation_num_workers: int = 0
    persistent_workers: bool = False
    prefetch_factor: int | None = None
    pin_memory: bool = True
    validation_batch_size: int | None = None

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
                dataset=metadata.get("dataset", {}),
                evaluation_representations=tuple(
                    training.get("evaluation_representations", ("raw",))
                ),
                selection_representation=training.get("selection_representation", "raw"),
                early_stopping=training.get("early_stopping"),
                train_num_workers=training.get("train_num_workers", training["workers"]),
                validation_num_workers=training.get(
                    "validation_num_workers", min(training["workers"], VALIDATION_MAX_WORKERS)
                ),
                persistent_workers=training.get("persistent_workers", training["workers"] > 0),
                prefetch_factor=training.get(
                    "prefetch_factor", 2 if training["workers"] > 0 else None
                ),
                pin_memory=training.get("pin_memory", True),
                validation_batch_size=training.get("validation_batch_size"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("training config ausente ou malformada") from exc
        config.validate()
        return config

    def validate(self) -> None:
        # A geração V2 reutiliza a mesma arquitetura e taxonomia; só o dataset,
        # a augmentation e o cronograma mudam. O trainer continua sendo um só.
        if (
            self.model_id
            not in (
                "yolox-s-model-v1",
                "yolox-s-model-v2",
                "yolox-s-quality-rebuild",
            )
            or self.num_classes != 4
        ):
            raise ValueError("training config diverge das gerações MODEL suportadas")
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
        if self.train_num_workers < 0 or self.validation_num_workers < 0:
            raise ValueError("num_workers do DataLoader não pode ser negativo")
        if self.validation_batch_size is not None and self.validation_batch_size <= 0:
            raise ValueError("validation_batch_size deve ser positivo")
        if self.train_num_workers == 0:
            if self.persistent_workers:
                raise ValueError("persistent_workers exige train_num_workers > 0")
            if self.prefetch_factor is not None:
                raise ValueError("prefetch_factor exige train_num_workers > 0")
        elif self.prefetch_factor is None or self.prefetch_factor < 1:
            raise ValueError("prefetch_factor deve ser positivo com TRAIN workers")
        if not 0 < self.min_lr_ratio <= 1 or self.weight_decay < 0:
            raise ValueError("min_lr_ratio/weight_decay inválidos")
        if self.device != "cuda":
            raise ValueError("device oficial deve ser cuda")
        if self.local_availability_policy != "fail_on_sample_access_no_hydration":
            raise ValueError("local_availability_policy deve falhar sem hidratação")
        representations = set(self.evaluation_representations)
        if not representations or representations - {"raw", "ema"}:
            raise ValueError("evaluation_representations aceita somente raw/ema")
        if self.selection_representation not in representations:
            raise ValueError("selection_representation precisa ser uma representação avaliada")
        if "ema" in representations and not self.ema:
            raise ValueError("representação ema exige ema=true")
        if self.early_stopping is not None:
            rule = self.early_stopping
            if rule.get("monitor") != "map50_95" or int(rule.get("patience_evaluations", 0)) < 1:
                raise ValueError("early_stopping exige monitor map50_95 e patience >= 1")
            if float(rule.get("min_delta", -1)) < 0:
                raise ValueError("early_stopping.min_delta não pode ser negativo")

    def with_overrides(self, **changes: Any) -> TrainingConfig:
        updated = replace(self, **changes)
        if changes.keys() - {"batch_size", "amp", "device"}:
            raise ValueError("override não permitido fora do piloto controlado")
        if updated.batch_size <= 0:
            raise ValueError("batch_size deve ser positivo")
        return updated


@dataclass(frozen=True)
class PretrainedLoadReport:
    source_sha256: str
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


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DatasetBinding:
    """Arquivos que vinculam um contrato de modelo ao seu dataset autorizado."""

    train_manifest: Path
    validation_manifest: Path
    split_manifest: Path
    authorization_status: Path
    authorized_status: str
    approved_decision: str


V1_DATASET_BINDING = DatasetBinding(
    train_manifest=TRAIN_MANIFEST_PATH,
    validation_manifest=VALIDATION_MANIFEST_PATH,
    split_manifest=SPLIT_MANIFEST_PATH,
    authorization_status=AUTHORIZATION_STATUS_PATH,
    authorized_status="AUTHORIZED_FOR_MODEL_V1",
    approved_decision="APPROVED_FOR_MODEL_V1",
)


def dataset_binding(dataset: Mapping[str, Any] | None) -> DatasetBinding:
    """Sem seção `dataset` o binding é exatamente o do V1; com ela, tudo vem do contrato."""
    if not dataset:
        return V1_DATASET_BINDING
    try:
        manifests = dataset["manifests"]
        binding = DatasetBinding(
            train_manifest=PROJECT_ROOT / str(manifests["TRAIN"]),
            validation_manifest=PROJECT_ROOT / str(manifests["VALIDATION"]),
            split_manifest=PROJECT_ROOT / str(dataset["split_manifest"]),
            authorization_status=PROJECT_ROOT / str(dataset["authorization_status"]),
            authorized_status=str(dataset["authorized_status"]),
            approved_decision=str(dataset["approved_decision"]),
        )
    except (KeyError, TypeError) as exc:
        raise TrainingGateError("seção dataset do contrato incompleta") from exc
    if "TEST" in manifests:
        raise TrainingGateError("contrato de treino não pode declarar manifest TEST")
    return binding


# Campos acrescentados na geração V2. Ficam fora do fingerprint quando estão no
# default, para que o fingerprint dos checkpoints V1 continue idêntico.
_V2_TRAINING_DEFAULTS: dict[str, Any] = {
    "evaluation_representations": ("raw",),
    "selection_representation": "raw",
    "early_stopping": None,
    "train_num_workers": 0,
    "validation_num_workers": 0,
    "persistent_workers": False,
    "prefetch_factor": None,
    "pin_memory": True,
    "validation_batch_size": None,
}


def _effective_training(config: TrainingConfig) -> dict[str, Any]:
    effective = {
        item.name: getattr(config, item.name)
        for item in fields(config)
        if item.name not in {"output_directory", "device", "dataset"}
    }
    for name, default in _V2_TRAINING_DEFAULTS.items():
        if effective[name] == default:
            effective.pop(name)
    return json.loads(json.dumps(effective, default=dict))


def checkpoint_fingerprints(metadata: Mapping[str, Any], config: TrainingConfig) -> dict[str, str]:
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
    config_document["effective_training"] = _effective_training(config)
    binding = dataset_binding(config.dataset)
    dataset = {
        "TRAIN": _sha256(binding.train_manifest),
        "VALIDATION": _sha256(binding.validation_manifest),
        "selection": _sha256(SELECTION_MANIFEST_PATH),
        "authorization": _sha256(binding.authorization_status),
    }
    return {
        "config_fingerprint": canonical_sha256(config_document),
        "class_mapping_fingerprint": _sha256(CLASS_MAPPING_PATH),
        "dataset_fingerprint": canonical_sha256(dataset),
        "split_fingerprint": _sha256(binding.split_manifest),
    }


def manifest_for_role(role: str, dataset: Mapping[str, Any] | None = None) -> Path:
    """Resolve o manifest do papel, preferindo o declarado no contrato do modelo.

    O V1 fixava os caminhos no módulo, então treinar outra versão de dataset
    exigiria duplicar o trainer. Agora o contrato declara `dataset.manifests` e
    o trainer continua único; sem essa seção, o comportamento é exatamente o do
    V1. TEST segue inacessível por aqui: abrir holdout não é papel do trainer.
    """
    if role in ("TRAIN", "VALIDATION"):
        binding = dataset_binding(dataset)
        path = binding.train_manifest if role == "TRAIN" else binding.validation_manifest
        if not path.is_file():
            raise TrainingGateError(
                f"DATASET_SPLIT_NOT_AVAILABLE: manifest declarado ausente: {path}"
            )
        return path
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


def validate_readiness(
    metadata: Mapping[str, Any], *, contract_path: Path = MODEL_METADATA_PATH
) -> None:
    binding = dataset_binding(metadata.get("dataset"))
    for path in (
        binding.split_manifest,
        binding.authorization_status,
        binding.train_manifest,
        binding.validation_manifest,
    ):
        if not path.is_file():
            raise TrainingGateError(f"DATASET_SPLIT_NOT_AVAILABLE: {path}")
    try:
        stack = json.loads(STACK_METADATA_PATH.read_text(encoding="utf-8"))
        report = json.loads(READINESS_REPORT_PATH.read_text(encoding="utf-8"))
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        authorization = json.loads(binding.authorization_status.read_text(encoding="utf-8"))
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
            authorization["status"] == binding.authorized_status
            and authorization["decision"] == binding.approved_decision
            and authorization["binding_mismatches"] == []
            and authorization["split_manifest_sha256"] == _sha256(binding.split_manifest)
            and authorization["selection_manifest_sha256"] == _sha256(SELECTION_MANIFEST_PATH)
        ),
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise TrainingGateError(f"readiness gate bloqueado: {', '.join(failed)}")
    entries = {entry["path"]: entry for entry in registry.get("artifacts", [])}
    for path in (
        STACK_METADATA_PATH,
        contract_path,
        binding.train_manifest,
        binding.validation_manifest,
        binding.authorization_status,
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


def load_pretrained_compatible(model: torch.nn.Module, path: Path | None) -> PretrainedLoadReport:
    if path is None:
        raise ValueError("pretrained exige path explícito; download automático é proibido")
    if not path.is_file():
        raise FileNotFoundError(path)
    source_sha256 = _sha256(path)
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if _sha256(path) != source_sha256:
        raise ValueError("pretrained changed during load")
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
    missing_non_classifier = sorted(
        key for key in target if key not in compatible and not key.startswith("head.cls_preds.")
    )
    if missing_non_classifier or unexpected:
        raise ValueError(
            "pretrained backbone/head incompleto ou inesperado: "
            f"missing_non_classifier={missing_non_classifier[:8]}, "
            f"unexpected={unexpected[:8]}"
        )
    load_ckpt(model, compatible)
    missing = sorted(set(target) - set(compatible))
    return PretrainedLoadReport(
        source_sha256=source_sha256,
        loaded=tuple(sorted(compatible)),
        missing=tuple(missing),
        unexpected=tuple(sorted(unexpected)),
        incompatible=tuple(sorted(incompatible)),
    )


def _official_optimizer(model: torch.nn.Module, config: TrainingConfig) -> torch.optim.Optimizer:
    exp = OfficialYOLOXExp()
    exp.model = model
    exp.warmup_epochs = config.warmup_epochs
    exp.warmup_lr = config.warmup_lr
    exp.basic_lr_per_img = config.basic_lr_per_image
    exp.momentum = config.momentum
    exp.weight_decay = config.weight_decay
    return exp.get_optimizer(config.batch_size)


def make_grad_scaler(device: torch.device, *, enabled: bool, initial_scale: float) -> Any:
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
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        # Contrato que gerou este engine; fingerprints e resume se vinculam a ele.
        self.metadata = dict(metadata) if metadata is not None else load_model_config()
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
        self.training_metadata: dict[str, Any] = {}

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
        non_finite = [name for name, gradient in gradients if not torch.isfinite(gradient).all()]
        amp_overflow = (
            bool(non_finite) and bool(gradients) and self.config.amp and self.scaler.is_enabled()
        )
        if not gradients or (non_finite and not amp_overflow):
            self.optimizer.zero_grad(set_to_none=True)
            detail = ", ".join(non_finite[:8]) or "nenhum gradient produzido"
            raise FloatingPointError(
                f"gradient ausente ou não finito ({detail}); optimizer.step abortado"
            )
        if amp_overflow:
            # Overflow fp16 com loss finita é o caso previsto do GradScaler: o
            # scaler.step pula o passo e o update reduz a escala. Só uma sequência
            # longa de pulos indica divergência real, e aí o treino para.
            self.consecutive_amp_skips = getattr(self, "consecutive_amp_skips", 0) + 1
            if self.consecutive_amp_skips > MAX_CONSECUTIVE_AMP_SKIPS:
                self.optimizer.zero_grad(set_to_none=True)
                raise FloatingPointError(
                    f"{self.consecutive_amp_skips} passos AMP seguidos com gradient não finito "
                    f"({', '.join(non_finite[:4])}); treino divergente"
                )
            logger.warning(
                "amp_overflow_step_skipped epoch={} iteration={} scale={} tensors={}",
                epoch,
                iteration,
                self.scaler.get_scale(),
                len(non_finite),
            )
        else:
            self.consecutive_amp_skips = 0
        self.scaler.step(self.optimizer)
        self.scaler.update()
        if self.ema is not None and not amp_overflow:
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
        if progress == 1 or progress % self.config.log_interval_steps == 0:
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

    def evaluate_validation(
        self,
        validation_loader: DataLoader,
        *,
        max_batches: int | None = None,
        representation: str = "raw",
    ) -> Any:
        """Executa somente VALIDATION, sobre os pesos raw ou EMA do mesmo passo."""
        from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator

        if representation == "ema":
            if self.ema is None:
                raise TrainingGateError("representação ema solicitada sem EMA ativo")
            model = self.ema.ema
        elif representation == "raw":
            model = self.model
        else:
            raise ValueError("representation deve ser raw ou ema")
        evaluator = YOLOXEvaluator(
            model,
            validation_loader,
            EvaluationConfig.from_model_metadata(self.metadata),
            device=self.device,
            manifest_path=dataset_binding(self.config.dataset).validation_manifest,
        )
        run = evaluator.evaluate(max_batches=max_batches)
        if self.tracker is not None:
            self.tracker.log_validation(
                run.result.as_persisted(),
                global_step=self.global_step,
                prefix="validation" if representation == "raw" else "validation_ema",
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
        metadata = self.metadata
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

    def save_last(self, *, training_metadata: Mapping[str, Any] | None = None) -> Path:
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
            self.tracker.log_checkpoint_reference(self.checkpoint_path("best"), kind="best")
        return True

    def _validate_resume_payload(self, payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("checkpoint não contém mapping de estado")
        if set(payload) != CHECKPOINT_REQUIRED_KEYS:
            raise ValueError("checkpoint possui keys obrigatórias ausentes ou desconhecidas")
        metadata = self.metadata
        expected = {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "model_id": metadata["model_id"],
            "architecture": metadata["architecture"],
            "num_classes": metadata["num_classes"],
            "class_order": list(metadata["class_names"]),
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
            if not isinstance(last_lrs, list) or len(last_lrs) != len(self.optimizer.param_groups):
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
            self.training_metadata = dict(payload["training_metadata"] or {})
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


def _parameter_digest(model: torch.nn.Module) -> str:
    """Hash trainable parameters only; BatchNorm buffer changes do not count as learning."""
    digest = hashlib.sha256()
    for name, parameter in model.named_parameters():
        digest.update(name.encode("utf-8"))
        digest.update(parameter.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def build_loaders(config: TrainingConfig) -> tuple[DataLoader, DataLoader]:
    train_rows = load_authorized_manifest(
        manifest_for_role("TRAIN", config.dataset), intended_split="TRAIN"
    )
    validation_rows = load_authorized_manifest(
        manifest_for_role("VALIDATION", config.dataset), intended_split="VALIDATION"
    )
    # A augmentation é decidida pelo contrato: `enabled=false` devolve o dataset
    # determinístico do V1, e `enabled=true` monta o pipeline oficial do YOLOX.
    train_dataset = build_train_dataset(
        train_rows, input_size=config.input_size, augmentation=config.augmentation
    )
    # Validation nunca é aumentada: a métrica precisa ser comparável entre épocas.
    validation_dataset = AuthorizedDetectionDataset(validation_rows, mode="validation")
    train_loader = build_yolox_dataloader(
        train_dataset,
        batch_size=config.batch_size,
        num_workers=config.train_num_workers,
        pin_memory=config.pin_memory,
        shuffle=True,
        persistent_workers=config.persistent_workers,
        prefetch_factor=config.prefetch_factor,
    )
    # Cada worker spawn no Windows reserva ~1,7 GB de commit (torch + DLLs CUDA).
    # Workers persistentes de validation somavam 8 processos ociosos aos 8 do
    # treino e esgotaram o commit do sistema: o cudaMalloc seguinte falhou como
    # OOM com VRAM livre (E1, 2026-09-21). Validation usa poucos workers efêmeros.
    validation_loader = build_yolox_dataloader(
        validation_dataset,
        batch_size=config.validation_batch_size or config.batch_size,
        num_workers=config.validation_num_workers,
        pin_memory=config.pin_memory,
        shuffle=False,
        persistent_workers=False,
        prefetch_factor=(config.prefetch_factor if config.validation_num_workers > 0 else None),
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
    contract_path: Path = MODEL_METADATA_PATH,
) -> TrainingEngine:
    if pretrained_path is not None and resume_path is not None:
        raise ValueError("pretrained e resume são operações mutuamente exclusivas")
    document = load_model_config(contract_path) if metadata is None else dict(metadata)
    if (
        metadata is not None
        and contract_path != MODEL_METADATA_PATH
        and document != load_model_config(contract_path)
    ):
        raise TrainingGateError("metadata em memória diverge do contrato selecionado")
    validate_readiness(document, contract_path=contract_path)
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
    # A contract selected on the CLI must also select the detector head. Keep the
    # no-argument V1 path for historical checkpoints and callers.
    model = instantiate_model(
        **({"config_path": contract_path} if contract_path != MODEL_METADATA_PATH else {})
    ).to(resolved)
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
    engine = TrainingEngine(
        model, optimizer, scheduler, config, resolved, tracker=tracker, metadata=document
    )
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


def run_controlled_dry_run(*, tracking_root: Path | None = None) -> DryRunResult:
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
    validation_dataset = AuthorizedDetectionDataset(selected_validation, mode="validation")
    train_loader = build_yolox_dataloader(
        train_dataset, batch_size=1, num_workers=0, pin_memory=True, shuffle=False
    )
    validation_loader = build_yolox_dataloader(
        validation_dataset, batch_size=2, num_workers=0, pin_memory=True, shuffle=False
    )

    base_config = TrainingConfig.from_model_metadata(metadata).with_overrides(batch_size=1)
    checkpoint_root = Path(tempfile.mkdtemp(prefix="urmind-dry-run-checkpoints-", dir=PROJECT_ROOT))
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
        engine = build_training_engine(batch_size=1, iters_per_epoch=2, tracker=tracker)
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
        engine.save_best(metric, source="VALIDATION", training_metadata={"run_type": "dry_run"})
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
            "optimizer_restored": _state_digest(restored.optimizer.state_dict())
            == before["optimizer"],
            "scheduler_restored": _state_digest(restored._scheduler_state()) == before["scheduler"],
            "scaler_restored": _state_digest(restored.scaler.state_dict()) == before["scaler"],
            "ema_restored": restored.ema is not None
            and _state_digest(restored.ema.ema.state_dict()) == before["ema"],
            "step_restored": resume.epoch == before["epoch"]
            and resume.global_step == before["global_step"],
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
                "test_records_resolved": split_audit.count(split_audit.records_resolved, "TEST"),
                "test_files_opened": split_audit.count(split_audit.files_opened, "TEST"),
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
            test_records_resolved=split_audit.count(split_audit.records_resolved, "TEST"),
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
    action.add_argument("--smoke", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--pretrained", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=MODEL_METADATA_PATH,
        help="contrato do modelo (default: MODEL V1); a geração V2 usa o próprio contrato",
    )
    parser.add_argument(
        "--mlflow-run-id",
        help=(
            "com --resume reabre o run original; com --pretrained reutiliza somente "
            "uma run de startup ainda sem métricas"
        ),
    )
    return parser


class EarlyStopping:
    """Regra de parada por paciência sobre VALIDATION, persistida no checkpoint.

    O V1 rodou ~150 épocas depois do último best porque não havia regra: a
    parada dependeu de aprovação manual. Aqui o estado vive em
    `training_metadata` do `last.pt`, então um resume continua a contagem.
    """

    def __init__(
        self, rule: Mapping[str, Any] | None, state: Mapping[str, Any] | None = None
    ) -> None:
        self.rule = dict(rule) if rule else None
        restored = dict(state or {})
        self.best_value: float | None = restored.get("best_value")
        self.best_epoch: int | None = restored.get("best_epoch")
        self.evaluations_without_improvement = int(
            restored.get("evaluations_without_improvement", 0)
        )

    def update(self, value: float, epoch: int) -> bool:
        """Registra uma avaliação; devolve True quando a paciência se esgotou."""
        min_delta = float(self.rule["min_delta"]) if self.rule else 0.0
        if self.best_value is None or value > self.best_value + min_delta:
            self.best_value, self.best_epoch = value, epoch
            self.evaluations_without_improvement = 0
        else:
            self.evaluations_without_improvement += 1
        return bool(
            self.rule
            and self.evaluations_without_improvement >= int(self.rule["patience_evaluations"])
        )

    def state(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "best_value": self.best_value,
            "best_epoch": self.best_epoch,
            "evaluations_without_improvement": self.evaluations_without_improvement,
        }


def close_mosaic_if_due(train_loader: DataLoader, config: TrainingConfig, epoch: int) -> bool:
    """Fase no-aug oficial: desliga mosaico/mixup nas últimas `no_aug_epochs` épocas.

    O YOLOX chama `train_loader.close_mosaic()`, que propaga a flag pelo batch
    sampler; aqui o DataLoader é o do torch, então a flag é desligada no próprio
    `MosaicDetection`. Workers persistentes guardam cópias do dataset e não
    enxergariam a flag: quem chama precisa reconstruir o loader quando isto
    devolve True. Flip/HSV do `TrainTransform` continuam, como no upstream.
    """
    dataset: Any = train_loader.dataset
    if epoch >= config.max_epoch - config.no_aug_epochs and getattr(
        dataset, "enable_mosaic", False
    ):
        dataset.enable_mosaic = False
        logger.info("no_aug phase: mosaic/mixup desligados a partir da época {}", epoch)
        return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    from app.ml.tracking import MLflowTracker, TrackingError

    args = _parser().parse_args(argv)
    if args.dry_run:
        if args.pretrained is not None or args.resume is not None or args.batch_size is not None:
            raise ValueError("dry-run usa exclusivamente sua política autoritativa")
        print(json.dumps(run_controlled_dry_run().as_dict(), ensure_ascii=False, indent=2))
        return 0
    resume_run_id = getattr(args, "mlflow_run_id", None)
    if resume_run_id is not None and args.resume is None and args.pretrained is None:
        raise ValueError("--mlflow-run-id exige --resume ou --pretrained")
    contract_path = args.contract.resolve()
    metadata = load_model_config(contract_path)
    validate_readiness(metadata, contract_path=contract_path)
    config = TrainingConfig.from_model_metadata(metadata)
    if args.batch_size is not None:
        config = config.with_overrides(batch_size=args.batch_size)
    train_loader, validation_loader = build_loaders(config)
    tracker = MLflowTracker(metadata, config)
    tracker.start_run(
        tags={
            "split_policy": "TRAIN_OPTIMIZATION_VALIDATION_ONLY",
            "contract": contract_path.relative_to(PROJECT_ROOT).as_posix(),
            # No resume o tag `pretrained` do run original é preservado.
            **(
                {"resumed_from": f"{args.resume.as_posix()}@{_sha256(args.resume)}"}
                if args.resume is not None
                else {"pretrained": args.pretrained.as_posix() if args.pretrained else "none"}
            ),
        },
        resume_run_id=resume_run_id,
        allow_empty_pretrained_retry=(
            resume_run_id is not None and args.pretrained is not None and args.resume is None
        ),
    )
    tracker.log_effective_config()
    stop_reason = "MAX_EPOCH"
    try:
        engine = build_training_engine(
            metadata=metadata,
            pretrained_path=args.pretrained,
            resume_path=args.resume,
            batch_size=args.batch_size,
            # The bounded smoke checks optimizer mechanics. Its warmup must fit
            # within 50 steps; the main run retains the full TRAIN epoch length.
            iters_per_epoch=1 if args.smoke else len(train_loader),
            tracker=tracker,
            contract_path=contract_path,
        )
        stopper = EarlyStopping(
            config.early_stopping, engine.training_metadata.get("early_stopping")
        )
        if args.single_step:
            engine.train_step(next(iter(train_loader)), epoch=0, iteration=0)
        elif args.smoke:
            # Technical smoke starts from the selected pretrained checkpoint and
            # never becomes the initialization for the main run.
            started = time.monotonic()
            before = _parameter_digest(engine.model)
            verified = False
            for iteration, batch in enumerate(train_loader):
                if iteration >= 50 or time.monotonic() - started >= 300:
                    break
                result = engine.train_step(batch, epoch=0, iteration=iteration)
                if _parameter_digest(engine.model) != before:
                    verified = True
                    break
            if not verified:
                raise TrainingGateError("smoke terminou sem atualização verificável de parâmetros")
            smoke_path = engine.checkpoint_directory / "smoke.pt"
            smoke_metadata = {
                "purpose": "EXPERIMENTAL_SMOKE_NOT_APPROVED_FOR_SERVING",
                "optimizer_update_verified": True,
                "smoke_iterations": engine.global_step,
                "last_loss": result.total_loss,
            }
            engine._atomic_save(engine._checkpoint_payload(smoke_metadata), smoke_path)
            saved = torch.load(smoke_path, map_location="cpu", weights_only=True)
            engine._validate_resume_payload(saved)
            del saved
            tracker.log_lightweight_artifact(
                "smoke-summary.json",
                {**smoke_metadata, "checkpoint": smoke_path.as_posix()},
                artifact_path="summary",
            )
            logger.info(
                "smoke_passed iterations={} optimizer_update_verified=true checkpoint={}",
                engine.global_step,
                smoke_path,
            )
        else:
            initial_parameters = _parameter_digest(engine.model) if args.resume is None else None
            main_update_verified = False
            start_epoch = engine.current_epoch + 1 if args.resume is not None else 0
            for epoch in range(start_epoch, config.max_epoch):
                engine.prepare_epoch(epoch)
                if close_mosaic_if_due(train_loader, config, epoch):
                    train_loader = build_yolox_dataloader(
                        train_loader.dataset,
                        batch_size=train_loader.batch_size or config.batch_size,
                        num_workers=config.train_num_workers,
                        pin_memory=config.pin_memory,
                        shuffle=True,
                        persistent_workers=config.persistent_workers,
                        prefetch_factor=config.prefetch_factor,
                    )
                for iteration, batch in enumerate(train_loader):
                    result = engine.train_step(batch, epoch=epoch, iteration=iteration)
                    if initial_parameters is not None and not main_update_verified:
                        current_parameters = _parameter_digest(engine.model)
                        if current_parameters != initial_parameters:
                            main_update_verified = True
                            logger.info(
                                "main_optimizer_update_verified epoch={} iteration={} global_step={}",
                                epoch,
                                iteration,
                                engine.global_step,
                            )
                            tracker.log_lightweight_artifact(
                                "startup-update.json",
                                {
                                    "optimizer_update_verified": True,
                                    "epoch": epoch,
                                    "iteration": iteration,
                                    "global_step": engine.global_step,
                                    "learning_rate": result.learning_rate,
                                    "parameter_digest_before": initial_parameters,
                                    "parameter_digest_after": current_parameters,
                                },
                                artifact_path="run-metadata",
                            )
                        elif engine.global_step >= 50:
                            raise TrainingGateError(
                                "nenhuma atualização de parâmetros nos 50 primeiros passos"
                            )
                exhausted = False
                is_last_epoch = epoch + 1 == config.max_epoch
                if (epoch + 1) % config.validation_interval_epochs == 0 or is_last_epoch:
                    validation_metrics: dict[str, Any] = {}
                    for representation in config.evaluation_representations:
                        validation = engine.evaluate_validation(
                            validation_loader, representation=representation
                        )
                        validation_metrics[representation] = validation.result.as_persisted()
                        logger.info(
                            "validation[{}] epoch={} metrics: {}",
                            representation,
                            epoch,
                            validation_metrics[representation],
                        )
                    metric = validation_metrics[config.selection_representation]["map50_95"]
                    if metric is None:
                        raise TrainingGateError(
                            "VALIDATION map50_95 indefinido; BEST não pode ser selecionado"
                        )
                    exhausted = stopper.update(float(metric), epoch)
                    engine.training_metadata = {
                        "completed_epoch": epoch,
                        "selection_representation": config.selection_representation,
                        "validation": validation_metrics,
                        "early_stopping": stopper.state(),
                    }
                    engine.save_best(
                        metric, source="VALIDATION", training_metadata=engine.training_metadata
                    )
                if (epoch + 1) % config.checkpoint_interval_epochs == 0 or exhausted:
                    engine.save_last(
                        training_metadata={
                            **engine.training_metadata,
                            "completed_epoch": epoch,
                            "early_stopping": stopper.state(),
                        }
                    )
                if exhausted:
                    stop_reason = "EARLY_STOP_PATIENCE"
                    logger.info(
                        "early stop: {} avaliações sem melhora > min_delta; best epoch={}",
                        stopper.evaluations_without_improvement,
                        stopper.best_epoch,
                    )
                    break
            tracker.log_lightweight_artifact(
                "training_summary.json",
                {
                    "stop_reason": stop_reason,
                    "last_completed_epoch": engine.current_epoch,
                    "best_metric": engine.best_metric,
                    "selection_representation": config.selection_representation,
                    "early_stopping": stopper.state(),
                    "checkpoint_directory": engine.checkpoint_directory.as_posix(),
                },
                artifact_path="summary",
            )
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
