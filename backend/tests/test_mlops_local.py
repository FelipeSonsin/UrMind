"""Contratos locais de MLflow e DVC; nenhum teste acessa TEST ou rede."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import mlflow
import pytest

from app.ml.yolox_model import MODEL_METADATA_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _metadata() -> dict:
    return json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))


def _config(tmp_path: Path):
    from app.ml.training import TrainingConfig

    return replace(
        TrainingConfig.from_model_metadata(_metadata()),
        output_directory=str(tmp_path / "checkpoints"),
        device="cpu",
    )


def test_mlflow_controlled_local_run_persists_all_contracts(tmp_path: Path) -> None:
    from app.ml.tracking import MLflowTracker

    tracker = MLflowTracker(_metadata(), _config(tmp_path), tracking_root=tmp_path / "mlruns")
    run_id = tracker.start_run(run_name="CONTROLLED_TEST", tags={"test_fixture": "true"})
    tracker.log_metrics({"synthetic_metric": 0.75}, step=1)
    tracker.log_lightweight_artifact(
        "controlled-test.json", {"fixture": True}, artifact_path="controlled"
    )
    tracker.end_run()

    assert tracker.tracking_uri.startswith("sqlite:///")
    assert "http" not in tracker.tracking_uri.lower()
    run = mlflow.tracking.MlflowClient(tracking_uri=tracker.tracking_uri).get_run(run_id)
    assert run.data.params["model_id"] == "yolox-s-model-v1"
    assert run.data.params["class_order"] == "D00,D10,D20,D40"
    assert run.data.params["config_fingerprint"]
    assert run.data.params["dataset_fingerprint"]
    assert run.data.params["split_fingerprint"]
    assert run.data.params["git_dirty"] in {"True", "False"}
    assert len(run.data.params["code_state_sha256"]) == 64
    assert run.data.metrics["synthetic_metric"] == pytest.approx(0.75)
    assert run.data.tags["test_fixture"] == "true"
    artifacts = mlflow.tracking.MlflowClient(tracking_uri=tracker.tracking_uri).list_artifacts(
        run_id, "controlled"
    )
    assert [artifact.path for artifact in artifacts] == ["controlled/controlled-test.json"]
    code_state = mlflow.tracking.MlflowClient(tracking_uri=tracker.tracking_uri).download_artifacts(
        run_id, "run-metadata/code-state.json"
    )
    persisted = json.loads(Path(code_state).read_text(encoding="utf-8"))
    assert persisted["git_commit"] == run.data.params["git_commit"]
    assert persisted["code_state_sha256"] == run.data.params["code_state_sha256"]
    assert len(persisted["tracked_patch_sha256"]) == 64
    assert isinstance(persisted["untracked_sha256"], dict)


def test_training_validation_and_checkpoint_logging_use_one_tracker(tmp_path: Path) -> None:
    from app.ml.tracking import MLflowTracker
    from app.ml.training import StepResult

    tracker = MLflowTracker(_metadata(), _config(tmp_path), tracking_root=tmp_path / "mlruns")
    run_id = tracker.start_run(run_name="INTEGRATION_TEST")
    tracker.log_training_step(
        StepResult(0, 0, 0.01, 1.0, 0.4, 0.3, 0.3, 0.0, 1.0, 0.1, 0),
        global_step=1,
    )
    tracker.log_validation(
        {
            "precision": 0.5,
            "recall": 0.4,
            "map50": 0.3,
            "map50_95": 0.2,
            "per_class": {"D00": {"precision": 0.6, "ap50": 0.4}},
        },
        global_step=1,
    )
    checkpoint = tmp_path / "checkpoints" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"fixture checkpoint reference only")
    tracker.log_checkpoint_reference(checkpoint, kind="last")
    tracker.end_run()

    run = mlflow.tracking.MlflowClient(tracking_uri=tracker.tracking_uri).get_run(run_id)
    assert run.data.metrics["train/total_loss"] == pytest.approx(1.0)
    assert run.data.metrics["validation/map50_95"] == pytest.approx(0.2)
    assert run.data.metrics["validation/D00/ap50"] == pytest.approx(0.4)
    assert run.data.tags["checkpoint.last.path"].endswith("checkpoints/last.pt")
    assert not any("test" in key.lower() for key in run.data.metrics)


def test_tracking_failure_is_explicit_and_does_not_delete_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import tracking

    tracker = tracking.MLflowTracker(
        _metadata(), _config(tmp_path), tracking_root=tmp_path / "mlruns"
    )
    tracker.start_run(run_name="FAILURE_TEST")
    checkpoint = tmp_path / "checkpoints" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"preserve")
    monkeypatch.setattr(
        tracking.mlflow,
        "set_tag",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced tracking failure")),
    )
    with pytest.raises(tracking.TrackingError, match="checkpoint reference"):
        tracker.log_checkpoint_reference(checkpoint, kind="last")
    assert checkpoint.read_bytes() == b"preserve"
    mlflow.end_run(status="FAILED")


def test_dvc_is_local_without_remote_or_tracked_dataset_copy() -> None:
    assert (PROJECT_ROOT / ".dvc/config").is_file()
    config = (PROJECT_ROOT / ".dvc/config").read_text(encoding="utf-8")
    assert "analytics = false" in config
    assert "remote" not in config.lower()
    assert not (PROJECT_ROOT / "dvc.yaml").exists()
    assert not (PROJECT_ROOT / "dvc.lock").exists()
    assert list(PROJECT_ROOT.glob("datasets/**/*.dvc")) == []
    result = subprocess.run(
        [str(PROJECT_ROOT / "backend/.venv/Scripts/dvc.exe"), "status"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "no data or pipelines tracked" in result.stdout.lower()


def test_dvc_tracks_only_small_controlled_fixture_in_temporary_repo(
    tmp_path: Path,
) -> None:
    dvc = str(PROJECT_ROOT / "backend/.venv/Scripts/dvc.exe")
    environment = {**os.environ, "DVC_NO_ANALYTICS": "true"}
    subprocess.run(
        [dvc, "init", "--no-scm"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    fixture = tmp_path / "controlled-fixture.txt"
    fixture.write_text("controlled DVC fixture\n", encoding="utf-8")
    subprocess.run(
        [dvc, "add", fixture.name],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (tmp_path / "controlled-fixture.txt.dvc").is_file()
    assert not subprocess.run(
        [dvc, "remote", "list"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    cache_bytes = sum(
        path.stat().st_size for path in (tmp_path / ".dvc/cache").rglob("*") if path.is_file()
    )
    assert cache_bytes < 1024


def test_gitignore_keeps_dvc_metadata_and_ignores_only_local_outputs() -> None:
    content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/mlruns/" in content
    assert "/models/" in content
    assert ".dvc/" not in content
    assert "*.dvc" not in content
    assert "dvc.yaml" not in content
    assert "dvc.lock" not in content
    assert not any(token in content for token in ("MLFLOW_API_KEY", "DVC_TOKEN", "DVC_SECRET"))


def test_official_config_has_local_only_mlops_and_no_credentials() -> None:
    metadata = _metadata()
    assert metadata["mlflow"] == {
        "tracking_mode": "local_sqlite",
        "tracking_directory": "mlruns",
        "experiment": "urmind-yolox-model-v1",
        "artifact_policy": "lightweight_metadata_and_references_only",
    }
    serialized = json.dumps(metadata).lower()
    assert "api_key" not in serialized
    assert "token" not in serialized
    assert "http://" not in serialized
    assert "https://" in serialized  # provenance URLs remain legitimate
    assert metadata["readiness"]["mlflow_ready"] is True
    assert metadata["readiness"]["dvc_ready"] is True
    assert metadata["readiness"]["system_ready_for_training"] is False


def test_official_environment_installer_validates_mlops_on_reuse() -> None:
    source = (PROJECT_ROOT / "scripts/setup_ml_env.ps1").read_text(encoding="utf-8")
    assert "[dev,mlops]" in source
    assert 'md.version("mlflow-skinny")' in source
    assert 'md.version("dvc")' in source
    stack = json.loads((PROJECT_ROOT / "backend/ml-stack.json").read_text(encoding="utf-8"))
    assert stack["mlflow_skinny"] == "3.16.0"
    assert stack["dvc"] == "3.67.1"


def test_resume_reopens_original_run_and_refuses_foreign_lineage(tmp_path: Path) -> None:
    """Um resume continua o mesmo run MLflow; outro contrato ou run fechado é recusado."""
    import mlflow

    from app.ml.tracking import MLflowTracker, TrackingError

    root = tmp_path / "mlruns"

    def tracker() -> MLflowTracker:
        return MLflowTracker(_metadata(), _config(tmp_path), tracking_root=root)

    first = tracker()
    run_id = first.start_run(tags={"contract": "c.json", "pretrained": "coco.pth"})
    first.log_metrics({"validation/map50_95": 0.1}, step=1)
    mlflow.end_run(status="KILLED")  # processo morto sem fechar o run

    resumed = tracker()
    assert (
        resumed.start_run(
            tags={"contract": "c.json", "resumed_from": "last.pt@x"}, resume_run_id=run_id
        )
        == run_id
    )
    resumed.end_run()
    client = mlflow.tracking.MlflowClient(tracking_uri=resumed.tracking_uri)
    run = client.get_run(run_id)
    assert run.data.tags["pretrained"] == "coco.pth"
    assert run.data.tags["resumed_from"] == "last.pt@x"
    assert run.info.status == "FINISHED"

    with pytest.raises(TrackingError, match="finalizado"):
        tracker().start_run(tags={"contract": "c.json"}, resume_run_id=run_id)
    other = tracker()
    other_id = other.start_run(tags={"contract": "c.json"})
    mlflow.end_run(status="KILLED")
    with pytest.raises(TrackingError, match="contrato"):
        tracker().start_run(tags={"contract": "outro.json"}, resume_run_id=other_id)
    assert mlflow.active_run() is None


def test_mlflow_run_id_requires_resume() -> None:
    from app.ml.training import main

    with pytest.raises(ValueError, match="--resume"):
        main(["--run", "--mlflow-run-id", "abc"])


def test_empty_pretrained_startup_run_can_be_reopened_but_run_with_metrics_cannot(
    tmp_path: Path,
) -> None:
    """A launcher retry reuses one empty run, never a partially trained run."""
    import mlflow

    from app.ml.tracking import MLflowTracker, TrackingError

    root = tmp_path / "mlruns"

    def tracker() -> MLflowTracker:
        return MLflowTracker(_metadata(), _config(tmp_path), tracking_root=root)

    first = tracker()
    empty_id = first.start_run(tags={"contract": "c.json", "pretrained": "coco.pth"})
    mlflow.end_run(status="KILLED")
    retried = tracker()
    assert (
        retried.start_run(
            tags={"contract": "c.json", "pretrained": "coco.pth"},
            resume_run_id=empty_id,
            allow_empty_pretrained_retry=True,
        )
        == empty_id
    )
    retried.log_metrics({"training/total_loss": 1.0}, step=0)
    mlflow.end_run(status="KILLED")

    with pytest.raises(TrackingError, match="métricas"):
        tracker().start_run(
            tags={"contract": "c.json", "pretrained": "coco.pth"},
            resume_run_id=empty_id,
            allow_empty_pretrained_retry=True,
        )
    assert mlflow.active_run() is None
