"""Tracking MLflow exclusivamente local para o pipeline oficial MODEL V1."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import mlflow
import torch

from app.ml.training import PROJECT_ROOT, StepResult, TrainingConfig, checkpoint_fingerprints


class TrackingError(RuntimeError):
    """Uma operação de tracking local falhou explicitamente."""


class MLflowTracker:
    """Um tracker por run; nunca armazena datasets ou bytes de checkpoints."""

    def __init__(
        self,
        metadata: Mapping[str, Any],
        config: TrainingConfig,
        *,
        tracking_root: Path | None = None,
    ) -> None:
        mlflow_config = metadata.get("mlflow")
        if not isinstance(mlflow_config, Mapping):
            raise TrackingError("config MLflow ausente")
        if mlflow_config.get("tracking_mode") != "local_sqlite":
            raise TrackingError("somente MLflow SQLite local é autorizado")
        configured = Path(str(mlflow_config["tracking_directory"]))
        if configured.is_absolute():
            raise TrackingError("tracking_directory deve ser relativo ao projeto")
        root = tracking_root or PROJECT_ROOT / configured
        self.tracking_root = root.resolve()
        self.tracking_uri = f"sqlite:///{(self.tracking_root / 'mlflow.db').as_posix()}"
        self.artifact_uri = (self.tracking_root / "artifacts").as_uri()
        self.experiment = str(mlflow_config["experiment"])
        self.metadata = dict(metadata)
        self.config = config
        self.run_id: str | None = None

    def _git_commit(self) -> str:
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise TrackingError("Git commit indisponível para reprodução") from exc

    def _code_state(self) -> dict[str, Any]:
        """Fingerprint do código realmente executado, inclusive worktree dirty."""
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            ).stdout
            patch = subprocess.run(
                [
                    "git",
                    "diff",
                    "--binary",
                    "HEAD",
                    "--",
                    ".",
                    ":(exclude)mlruns",
                    ":(exclude)models",
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise TrackingError("estado Git indisponível para reprodução") from exc
        entries = [entry for entry in status.split(b"\0") if entry]
        untracked: dict[str, str] = {}
        for entry in entries:
            if not entry.startswith(b"?? "):
                continue
            relative = entry[3:].decode("utf-8", errors="strict")
            path = (PROJECT_ROOT / relative).resolve()
            try:
                path.relative_to(PROJECT_ROOT.resolve())
            except ValueError as exc:
                raise TrackingError("arquivo untracked fora do projeto") from exc
            if path.is_file():
                untracked[relative.replace("\\", "/")] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        document = {
            "schema_version": 1,
            "git_commit": self._git_commit(),
            "dirty": bool(entries),
            "status_sha256": hashlib.sha256(status).hexdigest(),
            "tracked_patch_sha256": hashlib.sha256(patch).hexdigest(),
            "tracked_patch": patch.decode("utf-8", errors="replace"),
            "untracked_sha256": dict(sorted(untracked.items())),
        }
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        document["code_state_sha256"] = hashlib.sha256(canonical).hexdigest()
        return document

    def _parameters(self, code_state: Mapping[str, Any]) -> dict[str, Any]:
        fingerprints = checkpoint_fingerprints(self.metadata, self.config)
        device_name = (
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CUDA_UNAVAILABLE"
        )
        return {
            "model_id": self.metadata["model_id"],
            "architecture": self.metadata["architecture"],
            "yolox_commit": self.metadata["source_commit"],
            "git_commit": code_state["git_commit"],
            "git_dirty": code_state["dirty"],
            "code_state_sha256": code_state["code_state_sha256"],
            "python_version": platform.python_version(),
            "torch_version": torch.__version__,
            "cuda_runtime": torch.version.cuda or "NONE",
            "gpu": device_name,
            "seed": self.config.seed,
            "input_size": "x".join(map(str, self.config.input_size)),
            "batch_size": self.config.batch_size,
            "max_epoch": self.config.max_epoch,
            "optimizer": self.config.optimizer,
            "basic_lr_per_image": self.config.basic_lr_per_image,
            "scheduler": self.config.scheduler,
            "warmup_epochs": self.config.warmup_epochs,
            "warmup_lr": self.config.warmup_lr,
            "amp": self.config.amp,
            "ema": self.config.ema,
            "pretrained_policy": self.metadata["pretrained_policy"]["mode"],
            "class_order": ",".join(self.metadata["class_names"]),
            **fingerprints,
        }

    def _require_active(self) -> None:
        active = mlflow.active_run()
        if self.run_id is None or active is None or active.info.run_id != self.run_id:
            raise TrackingError("nenhuma run MLflow UrMind ativa")

    def start_run(
        self,
        *,
        run_name: str | None = None,
        tags: Mapping[str, str] | None = None,
        resume_run_id: str | None = None,
        allow_empty_pretrained_retry: bool = False,
    ) -> str:
        """Abre um run novo ou, num resume, reabre o run original do mesmo treino.

        Reabrir mantém o histórico de métricas e o lineage num único run; um run
        de outro experimento ou de outro contrato é recusado.
        """
        if self.run_id is not None or mlflow.active_run() is not None:
            raise TrackingError("run MLflow concorrente/nested é proibida")
        try:
            self.tracking_root.mkdir(parents=True, exist_ok=True)
            mlflow.set_tracking_uri(self.tracking_uri)
            client = mlflow.tracking.MlflowClient(tracking_uri=self.tracking_uri)
            experiment = client.get_experiment_by_name(self.experiment)
            experiment_id = (
                experiment.experiment_id
                if experiment is not None
                else client.create_experiment(self.experiment, artifact_location=self.artifact_uri)
            )
            if resume_run_id is not None:
                previous = client.get_run(resume_run_id)
                expected_contract = (tags or {}).get("contract")
                if previous.info.experiment_id != experiment_id or (
                    expected_contract is not None
                    and previous.data.tags.get("contract") != expected_contract
                ):
                    raise TrackingError(
                        "run MLflow do resume pertence a outro experimento/contrato"
                    )
                if previous.info.status == "FINISHED":
                    raise TrackingError("run MLflow já finalizado não pode ser retomado")
                if allow_empty_pretrained_retry and previous.data.metrics:
                    raise TrackingError("run MLflow com métricas não pode reiniciar de pretrained")
                active = mlflow.start_run(run_id=resume_run_id)
                mlflow.set_tags(dict(tags or {}))
            else:
                active = mlflow.start_run(
                    experiment_id=experiment_id,
                    run_name=run_name,
                    tags=dict(tags or {}),
                )
            self.run_id = active.info.run_id
            code_state = self._code_state()
            mlflow.log_params(self._parameters(code_state))
            self.log_lightweight_artifact(
                "code-state.json", code_state, artifact_path="run-metadata"
            )
            mlflow.set_tag("urmind.run_id", self.run_id)
            mlflow.set_tag("tracking.mode", "local_sqlite")
            mlflow.set_tag("data.policy", "fingerprints_only_no_dataset_artifacts")
            return self.run_id
        except TrackingError:
            if mlflow.active_run() is not None:
                mlflow.end_run(status="FAILED")
            self.run_id = None
            raise
        except Exception as exc:
            if mlflow.active_run() is not None:
                mlflow.end_run(status="FAILED")
            self.run_id = None
            raise TrackingError("falha ao iniciar run MLflow local") from exc

    def log_metrics(self, metrics: Mapping[str, float], *, step: int) -> None:
        self._require_active()
        try:
            mlflow.log_metrics({key: float(value) for key, value in metrics.items()}, step=step)
        except Exception as exc:
            raise TrackingError("falha ao registrar métricas MLflow") from exc

    def log_training_step(self, result: StepResult, *, global_step: int) -> None:
        self.log_metrics(
            {
                "train/total_loss": result.total_loss,
                "train/iou_loss": result.iou_loss,
                "train/objectness_loss": result.conf_loss,
                "train/classification_loss": result.cls_loss,
                "train/l1_loss": result.l1_loss,
                "train/learning_rate": result.learning_rate,
                "train/epoch": float(result.epoch),
            },
            step=global_step,
        )

    def log_validation(
        self, persisted: Mapping[str, Any], *, global_step: int, prefix: str = "validation"
    ) -> None:
        metrics: dict[str, float] = {}
        for key in ("precision", "recall", "map50", "map50_95"):
            value = persisted.get(key)
            if isinstance(value, (int, float)):
                metrics[f"{prefix}/{key}"] = float(value)
        per_class = persisted.get("per_class", {})
        if isinstance(per_class, Mapping):
            for class_name, values in per_class.items():
                if not isinstance(values, Mapping):
                    continue
                for key in ("precision", "recall", "ap50", "ap50_95"):
                    value = values.get(key)
                    if isinstance(value, (int, float)):
                        metrics[f"{prefix}/{class_name}/{key}"] = float(value)
        self.log_metrics(metrics, step=global_step)

    def log_lightweight_artifact(
        self, filename: str, document: Mapping[str, Any], *, artifact_path: str
    ) -> None:
        self._require_active()
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise TrackingError("artifact leve deve ser JSON com nome simples")
        encoded = json.dumps(document, sort_keys=True, default=str).encode("utf-8")
        if len(encoded) > 1_000_000:
            raise TrackingError("artifact MLflow excede limite leve de 1 MB")
        try:
            mlflow.log_dict(dict(document), f"{artifact_path}/{filename}")
        except Exception as exc:
            raise TrackingError("falha ao registrar artifact MLflow leve") from exc

    def log_checkpoint_reference(self, checkpoint: Path, *, kind: str) -> None:
        self._require_active()
        if kind not in {"last", "best"} or not checkpoint.is_file():
            raise TrackingError("checkpoint reference inválida")
        try:
            resolved = checkpoint.resolve()
            configured = Path(self.config.output_directory)
            checkpoint_root = (
                configured if configured.is_absolute() else PROJECT_ROOT / configured
            ).resolve()
            if resolved.parent != checkpoint_root:
                raise TrackingError("checkpoint está fora do diretório oficial")
            try:
                display = resolved.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                display = resolved.as_posix()
            mlflow.set_tag(f"checkpoint.{kind}.path", display)
            self.log_lightweight_artifact(
                f"checkpoint-{kind}.json",
                {"kind": kind, "path": display, "copied_to_mlflow": False},
                artifact_path="checkpoint-references",
            )
        except TrackingError:
            raise
        except Exception as exc:
            raise TrackingError("falha ao registrar checkpoint reference") from exc

    def log_effective_config(self) -> None:
        self.log_lightweight_artifact(
            "effective-config.json",
            {"training": asdict(self.config), "model_id": self.metadata["model_id"]},
            artifact_path="run-metadata",
        )

    def end_run(self, *, status: str = "FINISHED") -> None:
        self._require_active()
        try:
            mlflow.end_run(status=status)
        except Exception as exc:
            raise TrackingError("falha ao encerrar run MLflow") from exc
        finally:
            self.run_id = None
