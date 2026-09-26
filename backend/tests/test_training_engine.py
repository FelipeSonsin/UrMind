"""Contratos do único training engine UrMind; nenhum teste deste arquivo usa TEST."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from app.ml.yolox_model import MODEL_METADATA_PATH


def _config(path: Path = MODEL_METADATA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_training_config_has_one_authoritative_source() -> None:
    from app.ml.training import TrainingConfig

    config = TrainingConfig.from_model_metadata(_config())
    assert config.model_id == "yolox-s-model-v1"
    assert config.num_classes == 4
    assert config.input_size == (640, 640)
    assert config.batch_size > 0
    assert config.optimizer == "SGD"
    assert config.scheduler == "yoloxwarmcos"
    assert config.workers == 0
    assert config.seed == 20260908
    assert config.augmentation["enabled"] is False


def test_smoke_weight_digest_ignores_buffers_but_detects_parameter_change() -> None:
    from app.ml.training import _parameter_digest

    model = torch.nn.BatchNorm2d(2)
    before = _parameter_digest(model)
    model.running_mean.add_(1)
    assert _parameter_digest(model) == before
    with torch.no_grad():
        model.weight.add_(1)
    assert _parameter_digest(model) != before


def test_invalid_training_config_fails_closed() -> None:
    from app.ml.training import TrainingConfig

    metadata = _config()
    metadata["training"]["scheduler"] = "cos"
    with pytest.raises(ValueError, match="scheduler"):
        TrainingConfig.from_model_metadata(metadata)


def test_readiness_is_validated_before_optimizer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ml import training

    metadata = _config()
    metadata["readiness"]["data_model_interface_ready"] = False
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("optimizer não pode ser criado")

    monkeypatch.setattr(training, "_official_optimizer", forbidden)
    with pytest.raises(training.TrainingGateError):
        training.build_training_engine(metadata=metadata, device="cpu")
    assert called is False


def test_evaluator_readiness_is_required_before_optimizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ml import training

    metadata = _config()
    metadata["readiness"]["evaluator_ready"] = False
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("optimizer não pode ser criado")

    monkeypatch.setattr(training, "_official_optimizer", forbidden)
    with pytest.raises(training.TrainingGateError, match="EVALUATOR_READY"):
        training.build_training_engine(metadata=metadata, device="cpu")
    assert called is False


def test_checkpointing_readiness_is_required_before_optimizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ml import training

    metadata = _config()
    metadata["readiness"]["checkpointing_ready"] = False
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("optimizer não pode ser criado")

    monkeypatch.setattr(training, "_official_optimizer", forbidden)
    with pytest.raises(training.TrainingGateError, match="CHECKPOINTING_READY"):
        training.build_training_engine(metadata=metadata, device="cpu")
    assert called is False


def test_pretrained_and_resume_are_mutually_exclusive() -> None:
    from app.ml import training

    with pytest.raises(ValueError, match="mutuamente exclusivas"):
        training.build_training_engine(
            metadata=_config(),
            device="cpu",
            pretrained_path=Path("pretrained.pt"),
            resume_path=Path("last.pt"),
        )


def test_trainer_instantiation_reuses_official_optimizer_and_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yolox.utils import LRScheduler

    from app.ml import training

    model = torch.nn.Sequential(torch.nn.BatchNorm2d(1), torch.nn.Conv2d(1, 1, 1))
    monkeypatch.setattr(training, "validate_readiness", lambda metadata, **kwargs: None)
    monkeypatch.setattr(training, "instantiate_model", lambda: model)
    monkeypatch.setattr(training.OfficialYOLOXExp, "get_model", lambda exp: exp.model)
    engine = training.build_training_engine(metadata=_config(), device="cpu")
    assert isinstance(engine.optimizer, torch.optim.SGD)
    assert isinstance(engine.scheduler, LRScheduler)
    assert engine.scheduler.lr_func.func.__name__ == "yolox_warm_cos_lr"


def test_selected_contract_is_used_for_model_head(monkeypatch, tmp_path: Path) -> None:
    from app.ml import training

    selected = tmp_path / "selected_model.json"
    selected.write_text(json.dumps(_config()), encoding="utf8")
    model = torch.nn.Sequential(torch.nn.BatchNorm2d(1), torch.nn.Conv2d(1, 1, 1))
    paths = []
    monkeypatch.setattr(training, "validate_readiness", lambda metadata, **kwargs: None)
    monkeypatch.setattr(
        training,
        "instantiate_model",
        lambda **kwargs: (paths.append(kwargs["config_path"]), model)[1],
    )
    monkeypatch.setattr(training.OfficialYOLOXExp, "get_model", lambda exp: exp.model)
    training.build_training_engine(metadata=_config(), device="cpu", contract_path=selected)
    assert paths == [selected]


def test_selected_contract_rejects_metadata_mismatch(tmp_path: Path) -> None:
    from app.ml import training

    selected = tmp_path / "selected_model.json"
    selected.write_text(json.dumps(_config()), encoding="utf8")
    metadata = _config()
    metadata["num_classes"] = 5
    with pytest.raises(training.TrainingGateError, match="diverge"):
        training.build_training_engine(metadata=metadata, device="cpu", contract_path=selected)


def test_cuda_request_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ml import training

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(training.TrainingGateError, match="CUDA"):
        training.resolve_device("cuda")


def test_test_split_is_rejected_without_resolving_path() -> None:
    from app.ml.training import manifest_for_role

    assert manifest_for_role("TRAIN").name == "detection_train_authorized.jsonl"
    assert manifest_for_role("VALIDATION").name == "detection_validation_authorized.jsonl"
    with pytest.raises(ValueError, match="TEST permanece selado"):
        manifest_for_role("TEST")


def test_dry_run_policy_is_bounded_and_test_is_forbidden() -> None:
    metadata = _config()
    assert metadata["dry_run"] == {
        "train_steps": 2,
        "validation_max_batches": 1,
        "batch_size": 1,
        "test_access": "forbidden",
        "artifact_policy": "temporary_checkpoints_and_lightweight_mlflow_only",
    }
    assert metadata["readiness"]["dry_run_ready"] is True
    assert metadata["readiness"]["system_ready_for_training"] is False


def test_dry_run_split_audit_fails_closed_before_test_resolution() -> None:
    from app.ml.training import SplitAccessAudit, TrainingGateError

    audit = SplitAccessAudit.empty()
    with pytest.raises(TrainingGateError, match="somente TRAIN e VALIDATION"):
        audit.load("TEST")
    assert audit.files_opened.get("TEST", 0) == 0
    assert audit.records_resolved.get("TEST", 0) == 0


def test_cli_dry_run_rejects_runtime_overrides() -> None:
    from app.ml.training import main

    with pytest.raises(ValueError, match="política autoritativa"):
        main(["--dry-run", "--batch-size", "2"])


class _FakeHead:
    num_classes = 4
    use_l1 = False


class _FakeModel(torch.nn.Module):
    def __init__(self, *, finite: bool = True):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.tensor(2.0))
        self.unused = torch.nn.Parameter(torch.tensor(0.0))
        self.head = _FakeHead()
        self.finite = finite

    def forward(self, images, targets):
        total = self.weight * images.mean()
        if not self.finite:
            total = total * torch.tensor(float("nan"))
        return {
            "total_loss": total,
            "iou_loss": total * 0.4,
            "conf_loss": total * 0.3,
            "cls_loss": total * 0.3,
            "l1_loss": total * 0,
            "num_fg": torch.tensor(1.0),
        }


def _engine(model: torch.nn.Module, *, amp: bool = False):
    from yolox.utils import LRScheduler

    from app.ml.training import TrainingConfig, TrainingEngine

    config = TrainingConfig.from_model_metadata(_config())
    config = config.with_overrides(amp=amp)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    scheduler = LRScheduler(
        "yoloxwarmcos",
        0.01,
        2,
        config.max_epoch,
        warmup_epochs=config.warmup_epochs,
        warmup_lr_start=config.warmup_lr,
        no_aug_epochs=config.no_aug_epochs,
        min_lr_ratio=config.min_lr_ratio,
    )
    return TrainingEngine(model, optimizer, scheduler, config, torch.device("cpu"))


def test_train_step_uses_train_mode_official_loss_backward_and_optimizer() -> None:
    model = _FakeModel()
    engine = _engine(model)
    before = model.weight.detach().clone()
    batch = (
        torch.ones((1, 3, 32, 32)),
        torch.zeros((1, 1, 5)),
        torch.tensor([[32, 32]]),
        torch.tensor([0]),
    )
    result = engine.train_step(batch, epoch=0, iteration=0)
    assert model.training is True
    assert result.total_loss == pytest.approx(2.0)
    assert result.iou_loss == pytest.approx(0.8)
    assert model.weight.grad is not None
    assert not torch.equal(before, model.weight.detach())


def test_train_step_logs_through_injected_tracker_only() -> None:
    calls: list[tuple[object, int]] = []

    class Tracker:
        def log_training_step(self, result, *, global_step):
            calls.append((result, global_step))

    model = _FakeModel()
    engine = _engine(model)
    engine.tracker = Tracker()
    batch = (
        torch.ones((1, 3, 32, 32)),
        torch.zeros((1, 1, 5)),
        torch.tensor([[32, 32]]),
        torch.tensor([0]),
    )
    result = engine.train_step(batch, epoch=0, iteration=0)
    assert calls == [(result, 1)]
    engine.train_step(batch, epoch=0, iteration=1)
    assert calls == [(result, 1)]  # log_interval_steps from the contract is respected.


def test_non_finite_loss_fails_before_optimizer_step() -> None:
    model = _FakeModel(finite=False)
    engine = _engine(model)
    before = model.weight.detach().clone()
    batch = (torch.ones((1, 3, 32, 32)), torch.zeros((1, 1, 5)), (), ())
    with pytest.raises(FloatingPointError):
        engine.train_step(batch, epoch=0, iteration=0)
    assert torch.equal(before, model.weight.detach())


def test_missing_gradients_fail_before_optimizer_step() -> None:
    class Disconnected(_FakeModel):
        def forward(self, images, targets):
            loss = torch.tensor(1.0, requires_grad=True)
            return {
                key: loss
                for key in ("total_loss", "iou_loss", "conf_loss", "cls_loss", "l1_loss", "num_fg")
            }

    model = Disconnected()
    engine = _engine(model)
    before = model.weight.detach().clone()
    batch = (torch.ones((1, 3, 32, 32)), torch.zeros((1, 1, 5)), (), ())
    with pytest.raises(FloatingPointError, match="gradient"):
        engine.train_step(batch, epoch=0, iteration=0)
    assert torch.equal(before, model.weight.detach())


def test_entrypoint_readiness_precedes_manifest_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ml import training

    monkeypatch.setattr(training, "load_model_config", _config)
    monkeypatch.setattr(
        training,
        "_parser",
        lambda: type(
            "P",
            (),
            {
                "parse_args": lambda self, argv: type(
                    "A",
                    (),
                    {
                        "batch_size": None,
                        "pretrained": None,
                        "resume": None,
                        "single_step": True,
                        "dry_run": False,
                        "run": False,
                        "contract": training.MODEL_METADATA_PATH,
                    },
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        training,
        "validate_readiness",
        lambda metadata, **kwargs: (_ for _ in ()).throw(training.TrainingGateError("blocked")),
    )
    monkeypatch.setattr(
        training,
        "build_loaders",
        lambda config: (_ for _ in ()).throw(AssertionError("manifest acessado antes do gate")),
    )
    with pytest.raises(training.TrainingGateError, match="blocked"):
        training.main([])


def test_scheduler_has_warmup_and_normal_progression() -> None:
    engine = _engine(_FakeModel())
    start = engine.scheduler.update_lr(0)
    warm = engine.scheduler.update_lr(1)
    normal = engine.scheduler.update_lr(engine.config.warmup_epochs * 2 + 1)
    assert start == engine.config.warmup_lr
    assert warm > start
    assert normal > warm


def test_no_aug_phase_enables_official_l1_loss_flag() -> None:
    model = _FakeModel()
    engine = _engine(model)
    engine.prepare_epoch(engine.config.max_epoch - engine.config.no_aug_epochs - 1)
    assert model.head.use_l1 is False
    engine.prepare_epoch(engine.config.max_epoch - engine.config.no_aug_epochs)
    assert model.head.use_l1 is True


def test_amp_configuration_uses_grad_scaler(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ml import training

    calls: list[tuple[str, bool, float]] = []

    class Scaler:
        def __init__(self, device, enabled, init_scale):
            calls.append((device, enabled, init_scale))

    monkeypatch.setattr(training.torch.amp, "GradScaler", Scaler)
    training.make_grad_scaler(torch.device("cuda"), enabled=True, initial_scale=128.0)
    assert calls == [("cuda", True, 128.0)]


def test_pretrained_requires_explicit_existing_path(tmp_path: Path) -> None:
    from app.ml.training import load_pretrained_compatible

    with pytest.raises(ValueError, match="path explícito"):
        load_pretrained_compatible(_FakeModel(), None)
    with pytest.raises(FileNotFoundError):
        load_pretrained_compatible(_FakeModel(), tmp_path / "missing.pth")


def test_pretrained_reports_and_skips_incompatible_head(tmp_path: Path) -> None:
    from app.ml.training import load_pretrained_compatible

    model = _FakeModel()
    checkpoint = tmp_path / "official.pth"
    torch.save(
        {
            "model": {
                "weight": torch.tensor(7.0),
                "unused": torch.tensor(0.0),
                "head.cls_preds.0.weight": torch.ones(80),
            }
        },
        checkpoint,
    )
    report = load_pretrained_compatible(model, checkpoint)
    assert model.weight.item() == 7.0
    assert report.loaded == ("unused", "weight")
    assert report.incompatible == ("head.cls_preds.0.weight",)
    assert report.missing == ()
    assert len(report.source_sha256) == 64


def test_pretrained_rejects_missing_non_classifier_weights(tmp_path: Path) -> None:
    from app.ml.training import load_pretrained_compatible

    model = _FakeModel()
    original = model.weight.detach().clone()
    checkpoint = tmp_path / "incomplete.pth"
    torch.save({"model": {"unused": torch.tensor(1.0)}}, checkpoint)
    with pytest.raises(ValueError, match="incompleto"):
        load_pretrained_compatible(model, checkpoint)
    assert torch.equal(model.weight, original)


def test_windows_safe_module_entrypoint_is_guarded() -> None:
    source = (Path(__file__).parents[1] / "app/ml/training.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "detection_test_authorized" not in source
    assert "COCOEvaluator" not in source
    assert "def save_last" in source
    assert "def resume" in source
    assert "MLflowTracker" in source
    assert "tensorboard" not in source.lower()
    assert "wandb" not in source.lower()


class _OverflowGrad(torch.autograd.Function):
    """Loss finita com gradient infinito: o overflow fp16 que o GradScaler absorve."""

    @staticmethod
    def forward(ctx, value):
        return value.clone()

    @staticmethod
    def backward(ctx, grad):
        return grad * float("inf")


class _OverflowModel(_FakeModel):
    def forward(self, images, targets):
        outputs = super().forward(images, targets)
        total = _OverflowGrad.apply(outputs["total_loss"])
        return {**outputs, "total_loss": total}


def _amp_engine(model):
    engine = _engine(model, amp=True)
    engine.scaler = torch.amp.GradScaler("cpu", enabled=True, init_scale=128.0)
    return engine


def test_amp_overflow_pula_o_passo_sem_abortar_o_treino() -> None:
    model = _OverflowModel()
    engine = _amp_engine(model)
    before = model.weight.detach().clone()
    scale = engine.scaler.get_scale()
    batch = (torch.ones((1, 3, 32, 32)), torch.zeros((1, 1, 5)), (), ())
    engine.train_step(batch, epoch=0, iteration=0)
    assert torch.equal(before, model.weight.detach())  # passo pulado, peso intacto
    assert engine.scaler.get_scale() < scale  # escala reduzida pelo GradScaler


def test_overflow_amp_em_sequencia_declara_divergencia() -> None:
    from app.ml.training import MAX_CONSECUTIVE_AMP_SKIPS

    model = _OverflowModel()
    engine = _amp_engine(model)
    batch = (torch.ones((1, 3, 32, 32)), torch.zeros((1, 1, 5)), (), ())
    for iteration in range(MAX_CONSECUTIVE_AMP_SKIPS):
        engine.train_step(batch, epoch=0, iteration=iteration)
    with pytest.raises(FloatingPointError, match="divergente"):
        engine.train_step(batch, epoch=0, iteration=MAX_CONSECUTIVE_AMP_SKIPS)


def test_gradient_infinito_sem_amp_continua_abortando() -> None:
    model = _OverflowModel()
    engine = _engine(model, amp=False)
    batch = (torch.ones((1, 3, 32, 32)), torch.zeros((1, 1, 5)), (), ())
    with pytest.raises(FloatingPointError, match="gradient"):
        engine.train_step(batch, epoch=0, iteration=0)


# --- PHASE 3: geração V2 reaproveita o mesmo trainer -------------------------


@pytest.fixture
def screening_metadata(tmp_path: Path) -> dict:
    """Independent disposable contract; never reads a removed experiment/split."""
    metadata = _config()
    metadata["training"].update(
        {
            "max_epoch": 10,
            "no_aug_epochs": 2,
            "ema": True,
            "evaluation_representations": ["raw", "ema"],
            "selection_representation": "ema",
        }
    )
    metadata["training"]["augmentation"].update({"degrees": 0.0, "shear": 0.0})
    train = tmp_path / "train.jsonl"
    validation = tmp_path / "validation.jsonl"
    train.write_text("", encoding="utf-8")
    validation.write_text("", encoding="utf-8")
    metadata["dataset"] = {
        "manifests": {"TRAIN": str(train), "VALIDATION": str(validation)},
        "split_manifest": str(tmp_path / "split.json"),
        "authorization_status": str(tmp_path / "authorization.json"),
        "authorized_status": "AUTHORIZED_FIXTURE",
        "approved_decision": "APPROVED_FIXTURE",
    }
    return metadata


def test_v1_config_fingerprint_is_unchanged_by_v2_fields() -> None:
    """Os campos V2 no default não podem alterar a fórmula do fingerprint V1.

    Referência: a fórmula anterior à PHASE 3 (`asdict` menos output_directory e
    device). O `best.pt` do V1 já divergia do contrato V1 corrente antes desta
    fase (drift pré-existente, registrado em DEFERRED_FINDINGS), por isso a
    comparação é contra a fórmula, não contra o checkpoint.
    """
    from dataclasses import asdict

    from app.ml.training import (
        _V2_TRAINING_DEFAULTS,
        TrainingConfig,
        canonical_sha256,
        checkpoint_fingerprints,
    )

    metadata = _config()
    config = TrainingConfig.from_model_metadata(metadata)
    legacy = {
        key: value
        for key, value in asdict(replace_dataset(config)).items()
        if key not in {"output_directory", "device", "dataset", *_V2_TRAINING_DEFAULTS}
    }
    fields = (
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
    document = {field: metadata[field] for field in fields}
    document["effective_training"] = legacy
    assert checkpoint_fingerprints(metadata, config)["config_fingerprint"] == canonical_sha256(
        document
    )


def replace_dataset(config):
    from dataclasses import replace

    # asdict não copia MappingProxyType; o legado não tinha esse campo.
    return replace(config, dataset={})


@pytest.mark.parametrize(
    ("augmentation_enabled", "worker_count"),
    [(False, 0), (True, 1), (True, 0)],
    ids=["plain", "augmented", "low_memory"],
)
def test_explicit_contract_binds_its_own_dataset_and_never_test(
    screening_metadata, augmentation_enabled, worker_count
) -> None:
    from app.ml.training import TrainingConfig, dataset_binding, manifest_for_role

    metadata = screening_metadata
    metadata["training"]["augmentation"]["enabled"] = augmentation_enabled
    metadata["training"]["train_num_workers"] = worker_count
    metadata["training"]["prefetch_factor"] = 1 if worker_count else None
    config = TrainingConfig.from_model_metadata(metadata)
    binding = dataset_binding(config.dataset)
    assert binding.train_manifest.name == "train.jsonl"
    assert binding.validation_manifest.name == "validation.jsonl"
    assert binding.authorized_status == "AUTHORIZED_FIXTURE"
    assert config.train_num_workers == worker_count
    assert manifest_for_role("TRAIN", config.dataset) == binding.train_manifest
    with pytest.raises(ValueError, match="TEST"):
        manifest_for_role("TEST", config.dataset)
    assert config.augmentation.get("degrees", 0.0) == 0.0
    assert config.augmentation.get("shear", 0.0) == 0.0


def test_dataset_section_declaring_test_manifest_is_rejected(screening_metadata) -> None:
    from app.ml.training import TrainingGateError, dataset_binding

    dataset = screening_metadata["dataset"]
    dataset["manifests"]["TEST"] = "forbidden-test.jsonl"
    with pytest.raises(TrainingGateError, match="TEST"):
        dataset_binding(dataset)


def test_ema_representation_requires_ema(screening_metadata) -> None:
    from app.ml.training import TrainingConfig

    metadata = screening_metadata
    metadata["training"]["ema"] = False
    with pytest.raises(ValueError, match="ema"):
        TrainingConfig.from_model_metadata(metadata)


def test_early_stopping_counts_patience_with_min_delta_and_restores() -> None:
    from app.ml.training import EarlyStopping

    rule = {"monitor": "map50_95", "patience_evaluations": 2, "min_delta": 0.01}
    stopper = EarlyStopping(rule)
    assert stopper.update(0.10, 1) is False
    assert stopper.update(0.105, 3) is False  # melhora abaixo de min_delta
    restored = EarlyStopping(rule, stopper.state())
    assert restored.evaluations_without_improvement == 1
    assert restored.best_epoch == 1
    assert restored.update(0.109, 5) is True
    assert EarlyStopping(None).update(0.0, 0) is False


def test_close_mosaic_turns_off_once_in_no_aug_phase(screening_metadata) -> None:
    from types import SimpleNamespace

    from app.ml.training import TrainingConfig, close_mosaic_if_due

    config = TrainingConfig.from_model_metadata(screening_metadata)
    loader = SimpleNamespace(dataset=SimpleNamespace(enable_mosaic=True))
    first_no_aug = config.max_epoch - config.no_aug_epochs
    assert close_mosaic_if_due(loader, config, first_no_aug - 1) is False  # type: ignore[arg-type]
    assert close_mosaic_if_due(loader, config, first_no_aug) is True  # type: ignore[arg-type]
    assert loader.dataset.enable_mosaic is False
    assert close_mosaic_if_due(loader, config, first_no_aug + 1) is False  # type: ignore[arg-type]


def test_validation_loader_uses_few_ephemeral_workers(
    monkeypatch: pytest.MonkeyPatch, screening_metadata
) -> None:
    """Regressão do OOM do E1: validation não pode manter workers persistentes."""
    from app.ml import training

    config = training.TrainingConfig.from_model_metadata(screening_metadata)
    monkeypatch.setattr(training, "load_authorized_manifest", lambda path, intended_split: [])
    monkeypatch.setattr(training, "build_train_dataset", lambda rows, **kwargs: [0])
    monkeypatch.setattr(training, "AuthorizedDetectionDataset", lambda rows, mode: [0])
    _, validation_loader = training.build_loaders(config)
    assert validation_loader.num_workers == min(config.workers, training.VALIDATION_MAX_WORKERS)
    assert validation_loader.persistent_workers is False


def test_explicit_loader_contract_controls_train_and_validation(
    monkeypatch: pytest.MonkeyPatch,
    screening_metadata,
) -> None:
    """The memory-efficient contract must govern both canonical loaders."""
    from app.ml import training

    metadata = screening_metadata
    metadata["training"].update(
        {
            "train_num_workers": 1,
            "validation_num_workers": 0,
            "persistent_workers": False,
            "prefetch_factor": 1,
            "pin_memory": False,
            "validation_batch_size": 1,
        }
    )
    config = training.TrainingConfig.from_model_metadata(metadata)
    monkeypatch.setattr(training, "load_authorized_manifest", lambda path, intended_split: [])
    monkeypatch.setattr(training, "build_train_dataset", lambda rows, **kwargs: [0])
    monkeypatch.setattr(training, "AuthorizedDetectionDataset", lambda rows, mode: [0])

    train_loader, validation_loader = training.build_loaders(config)

    assert train_loader.num_workers == 1
    assert train_loader.persistent_workers is False
    assert train_loader.prefetch_factor == 1
    assert train_loader.pin_memory is False
    assert validation_loader.num_workers == 0
    assert validation_loader.persistent_workers is False
    assert validation_loader.prefetch_factor is None
    assert validation_loader.pin_memory is False
    assert validation_loader.batch_size == 1
    assert train_loader.batch_size == config.batch_size


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"train_num_workers": 0, "persistent_workers": True}, "persistent_workers"),
        ({"train_num_workers": 0, "prefetch_factor": 1}, "prefetch_factor"),
        ({"train_num_workers": 1, "prefetch_factor": 0}, "prefetch_factor"),
    ],
)
def test_invalid_loader_contract_fails_closed(
    updates: dict, message: str, screening_metadata
) -> None:
    from app.ml.training import TrainingConfig

    metadata = screening_metadata
    metadata["training"].update(
        {
            "train_num_workers": 1,
            "validation_num_workers": 0,
            "persistent_workers": False,
            "prefetch_factor": 1,
            "pin_memory": False,
            **updates,
        }
    )
    with pytest.raises(ValueError, match=message):
        TrainingConfig.from_model_metadata(metadata)


@pytest.mark.parametrize("role", ["TRAIN", "VALIDATION"])
def test_missing_declared_manifest_has_explicit_status(screening_metadata, role):
    from app.ml.training import TrainingGateError, manifest_for_role

    Path(screening_metadata["dataset"]["manifests"][role]).unlink()
    with pytest.raises(TrainingGateError, match="DATASET_SPLIT_NOT_AVAILABLE"):
        manifest_for_role(role, screening_metadata["dataset"])


def test_missing_declared_split_blocks_readiness(screening_metadata):
    from app.ml.training import TrainingGateError, validate_readiness

    with pytest.raises(TrainingGateError, match="DATASET_SPLIT_NOT_AVAILABLE"):
        validate_readiness(screening_metadata)
