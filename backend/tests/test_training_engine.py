"""Contratos do único training engine UrMind; nenhum teste deste arquivo usa TEST."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from app.ml.yolox_model import MODEL_METADATA_PATH


def _config() -> dict:
    return json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))


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
    monkeypatch.setattr(training, "validate_readiness", lambda metadata: None)
    monkeypatch.setattr(training, "instantiate_model", lambda: model)
    monkeypatch.setattr(training.OfficialYOLOXExp, "get_model", lambda exp: exp.model)
    engine = training.build_training_engine(metadata=_config(), device="cpu")
    assert isinstance(engine.optimizer, torch.optim.SGD)
    assert isinstance(engine.scheduler, LRScheduler)
    assert engine.scheduler.lr_func.func.__name__ == "yolox_warm_cos_lr"


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
            return {key: loss for key in ("total_loss", "iou_loss", "conf_loss", "cls_loss", "l1_loss", "num_fg")}

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
    monkeypatch.setattr(training, "_parser", lambda: type("P", (), {"parse_args": lambda self, argv: type("A", (), {"batch_size": None, "pretrained": None, "resume": None, "single_step": True, "dry_run": False, "run": False})()})())
    monkeypatch.setattr(
        training,
        "validate_readiness",
        lambda metadata: (_ for _ in ()).throw(training.TrainingGateError("blocked")),
    )
    monkeypatch.setattr(
        training,
        "build_loaders",
        lambda config: (_ for _ in ()).throw(AssertionError("manifest acessado antes do gate")),
    )
    with pytest.raises(training.TrainingGateError, match="blocked"):
        training.main([])


def test_validation_hook_uses_eval_and_inference_mode_then_restores() -> None:
    model = _FakeModel()
    engine = _engine(model)
    model.train()
    with engine.validation_mode():
        assert model.training is False
        assert torch.is_grad_enabled() is False
    assert model.training is True


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
    training.make_grad_scaler(
        torch.device("cuda"), enabled=True, initial_scale=128.0
    )
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
        {"model": {"weight": torch.tensor(7.0), "head.cls_preds.0.weight": torch.ones(80)}},
        checkpoint,
    )
    report = load_pretrained_compatible(model, checkpoint)
    assert model.weight.item() == 7.0
    assert report.loaded == ("weight",)
    assert report.incompatible == ("head.cls_preds.0.weight",)
    assert report.missing


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
