"""Contrato de checkpoint/resume do unico training engine UrMind."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import torch

from app.ml.yolox_model import MODEL_METADATA_PATH


class _Head:
    num_classes = 4
    use_l1 = False


class _Model(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.linear = torch.nn.Linear(2, 1)
        self.head = _Head()


class _StatefulScaler:
    def __init__(self, value: int) -> None:
        self.value = value

    def state_dict(self) -> dict[str, int]:
        return {"value": self.value}

    def load_state_dict(self, state: dict[str, int]) -> None:
        self.value = state["value"]


def _metadata() -> dict:
    return json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))


def _engine(tmp_path: Path):
    from yolox.utils import LRScheduler

    from app.ml.training import TrainingConfig, TrainingEngine

    model = _Model()
    config = replace(
        TrainingConfig.from_model_metadata(_metadata()),
        output_directory=str(tmp_path),
        device="cpu",
    )
    optimizer = torch.optim.SGD(model.parameters(), lr=0.02, momentum=0.9)
    scheduler = LRScheduler(
        "yoloxwarmcos",
        0.02,
        2,
        config.max_epoch,
        warmup_epochs=config.warmup_epochs,
        warmup_lr_start=config.warmup_lr,
        no_aug_epochs=config.no_aug_epochs,
        min_lr_ratio=config.min_lr_ratio,
    )
    engine = TrainingEngine(model, optimizer, scheduler, config, torch.device("cpu"))
    engine.scaler = _StatefulScaler(17)
    return engine


def _seed_state(engine) -> None:
    loss = engine.model.linear(torch.ones(1, 2)).sum()
    loss.backward()
    engine.optimizer.step()
    engine.optimizer.zero_grad(set_to_none=True)
    engine.current_epoch = 3
    engine.current_iteration = 7
    engine.global_step = 23
    engine.best_metric = 0.41
    engine.ema.updates = 11
    with torch.no_grad():
        for parameter in engine.ema.ema.parameters():
            parameter.add_(2.0)


def test_save_last_contains_complete_versioned_state(tmp_path: Path) -> None:
    from app.ml.training import CHECKPOINT_REQUIRED_KEYS

    engine = _engine(tmp_path)
    _seed_state(engine)
    path = engine.save_last(training_metadata={"run": "controlled-test"})
    payload = torch.load(path, map_location="cpu", weights_only=True)
    assert path == tmp_path / "last.pt"
    assert set(payload) == CHECKPOINT_REQUIRED_KEYS
    assert payload["checkpoint_schema_version"] == 1
    assert payload["model_id"] == "yolox-s-model-v1"
    assert payload["architecture"] == "YOLOX-s"
    assert payload["class_order"] == ["D00", "D10", "D20", "D40"]
    assert payload["epoch"] == 3
    assert payload["iteration"] == 7
    assert payload["global_step"] == 23
    assert payload["best_metric"] == pytest.approx(0.41)
    assert payload["training_metadata"] == {"run": "controlled-test"}


def test_controlled_resume_restores_all_state(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_state(engine)
    expected_model = copy.deepcopy(engine.model.state_dict())
    expected_optimizer = copy.deepcopy(engine.optimizer.state_dict())
    expected_ema = copy.deepcopy(engine.ema.ema.state_dict())
    path = engine.save_last()
    with torch.no_grad():
        for parameter in engine.model.parameters():
            parameter.zero_()
        for parameter in engine.ema.ema.parameters():
            parameter.zero_()
    engine.optimizer.param_groups[0]["lr"] = 9.0
    engine.scaler.value = 99
    engine.ema.updates = 0
    engine.current_epoch = engine.current_iteration = engine.global_step = 0
    engine.best_metric = None

    state = engine.resume(path)

    for key, value in expected_model.items():
        assert torch.equal(engine.model.state_dict()[key], value)
    for key, value in expected_ema.items():
        assert torch.equal(engine.ema.ema.state_dict()[key], value)
    assert engine.optimizer.state_dict()["param_groups"] == expected_optimizer["param_groups"]
    assert engine.optimizer.state_dict()["state"]
    assert engine.scaler.value == 17
    assert engine.ema.updates == 11
    assert (state.epoch, state.iteration, state.global_step) == (3, 7, 23)
    assert state.best_metric == pytest.approx(0.41)


def test_best_uses_validation_only_and_respects_monitor_mode(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="VALIDATION"):
        engine.save_best(0.9, source="TEST")
    assert engine.save_best(0.3, source="VALIDATION") is True
    assert engine.save_best(0.2, source="VALIDATION") is False
    assert engine.save_best(0.4, source="VALIDATION") is True
    assert (tmp_path / "best.pt").is_file()
    assert engine.best_metric == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("checkpoint_schema_version", 999, "schema"),
        ("model_id", "other-model", "model_id"),
        ("architecture", "YOLOX-m", "architecture"),
        ("num_classes", 5, "num_classes"),
        ("class_order", ["D40", "D20", "D10", "D00"], "class order"),
        ("config_fingerprint", "0" * 64, "config fingerprint"),
        ("class_mapping_fingerprint", "3" * 64, "class mapping fingerprint"),
        ("dataset_fingerprint", "1" * 64, "dataset fingerprint"),
        ("split_fingerprint", "2" * 64, "split fingerprint"),
    ],
)
def test_resume_rejects_incompatible_checkpoint_before_mutation(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    engine = _engine(tmp_path)
    path = engine.save_last()
    payload = torch.load(path, map_location="cpu", weights_only=True)
    payload[field] = value
    torch.save(payload, path)
    before = copy.deepcopy(engine.model.state_dict())
    with pytest.raises(ValueError, match=message):
        engine.resume(path)
    for key, expected in before.items():
        assert torch.equal(engine.model.state_dict()[key], expected)


def test_resume_rejects_missing_corrupt_and_incomplete_files(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(FileNotFoundError):
        engine.resume(tmp_path / "last.pt")
    corrupt = tmp_path / "last.pt"
    corrupt.write_bytes(b"not a torch checkpoint")
    with pytest.raises(ValueError, match="ilegivel|corrompido"):
        engine.resume(corrupt)
    engine.save_last()
    payload = torch.load(corrupt, map_location="cpu", weights_only=True)
    del payload["optimizer_state_dict"]
    torch.save(payload, corrupt)
    with pytest.raises(ValueError, match="keys obrigat"):
        engine.resume(corrupt)


def test_pretrained_and_resume_are_distinct(tmp_path: Path) -> None:
    from app.ml.training import load_pretrained_compatible

    engine = _engine(tmp_path)
    pretrained = tmp_path / "pretrained.pt"
    torch.save({"model": engine.model.state_dict()}, pretrained)
    with pytest.raises(ValueError, match="oficial|schema"):
        engine.resume(pretrained)
    before = copy.deepcopy(engine.optimizer.state_dict())
    load_pretrained_compatible(engine.model, pretrained)
    assert engine.optimizer.state_dict() == before


def test_atomic_save_leaves_no_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import training

    engine = _engine(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("forced write failure")

    monkeypatch.setattr(training.torch, "save", fail)
    with pytest.raises(OSError, match="forced"):
        engine.save_last()
    assert not (tmp_path / "last.pt").exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_save_preserves_previous_checkpoint_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import training

    engine = _engine(tmp_path)
    path = engine.save_last()
    previous = path.read_bytes()
    monkeypatch.setattr(
        training.torch,
        "save",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("forced")),
    )
    with pytest.raises(OSError, match="forced"):
        engine.save_last()
    assert path.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp")) == []


def test_checkpoint_paths_are_single_authoritative_pair(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine.checkpoint_path("last") == tmp_path / "last.pt"
    assert engine.checkpoint_path("best") == tmp_path / "best.pt"
    with pytest.raises(ValueError):
        engine.checkpoint_path("latest")


def test_effective_training_config_is_bound_to_resume(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    path = engine.save_last()
    incompatible = _engine(tmp_path)
    incompatible.config = replace(incompatible.config, batch_size=1)
    with pytest.raises(ValueError, match="config fingerprint"):
        incompatible.resume(path)
