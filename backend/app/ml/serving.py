"""Modelo no produto: checkpoint → ONNX → inferência (MASTER_PLAN §9).

PyTorch treina; ONNX Runtime serve. O export só vale depois de comparar as duas
saídas em imagens reais da VALIDATION, e a métrica registrada é medida de novo
sobre a VALIDATION inteira com o evaluator oficial — nunca copiada de log.

    python -m app.ml.serving export --checkpoint best
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.yolox_model import MODEL_METADATA_PATH, load_model_config

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVING_DIR = PROJECT_ROOT / "models" / "serving"
OPSET = 17
# PROVISÓRIO (§31.16): limiar de produto, igual ao mínimo que vira evento no domínio.
# O 0,01 do contrato é o de mAP, não de decisão.
SERVING_SCORE_THRESHOLD = 0.25


class ModelNotAvailableError(RuntimeError):
    """Sem ONNX verificado: o Worker grava `model_not_available`, nunca resultado falso."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_checkpoint_model(checkpoint: Path) -> tuple[Any, dict[str, Any]]:
    """YOLOX-s V1 com os pesos que o trainer validou (`model_state_dict`), em modo avaliação.

    `evaluate_validation` mede `engine.model`, não a EMA: servir a EMA publicaria um
    modelo diferente do que tem métrica registrada.
    """
    import torch

    from app.ml.yolox_model import instantiate_model

    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    metadata = load_model_config()
    if payload.get("class_order") != list(metadata["class_names"]):
        raise ValueError("checkpoint com ordem de classes diferente do contrato MODEL V1")
    model = instantiate_model()
    state = payload["model_state_dict"]
    model.load_state_dict(state, strict=True)
    model.eval()
    model.head.decode_in_inference = True
    info = {
        "epoch": payload.get("epoch"),
        "best_metric": payload.get("best_metric"),
        "checkpoint_sha256": sha256_file(checkpoint),
        "weights": "model_state_dict (os validados pelo trainer)",
        "global_step": payload.get("global_step"),
        "training_metadata": payload.get("training_metadata"),
        **{
            key: payload.get(key)
            for key in (
                "config_fingerprint",
                "dataset_fingerprint",
                "split_fingerprint",
                "class_mapping_fingerprint",
            )
        },
    }
    return model, info


def export_onnx(checkpoint: Path, destination: Path) -> dict[str, Any]:
    import torch

    model, info = load_checkpoint_model(checkpoint)
    metadata = load_model_config()
    height, width = metadata["test_size"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, 3, height, width)
    torch.onnx.export(
        model,
        (dummy,),
        str(destination),
        input_names=["images"],
        output_names=["output"],
        opset_version=OPSET,
        dynamo=False,
    )
    import onnx

    onnx.checker.check_model(str(destination))
    return {
        **info,
        "onnx_sha256": sha256_file(destination),
        "opset": OPSET,
        "input_size": [height, width],
        "class_names": metadata["canonical_class_names"],
        "model_contract_sha256": sha256_file(MODEL_METADATA_PATH),
    }


@dataclass(frozen=True)
class ServedDetection:
    urmind_class: str
    confidence: float
    bbox: dict[str, float]
    """Normalizada 0–1 em relação à imagem original (x, y, width, height)."""


def _preprocess(image_bgr: np.ndarray, input_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    from yolox.data.data_augment import preproc  # type: ignore[import-not-found]

    tensor, ratio = preproc(image_bgr, input_size)
    return tensor[None, :, :, :].astype(np.float32), float(ratio)


class OnnxDetector:
    """Inferência CPU/GPU via ONNX Runtime, com o ONNX conferido por hash."""

    def __init__(self, onnx_path: Path, expected_sha256: str) -> None:
        if not onnx_path.is_file():
            raise ModelNotAvailableError(f"ONNX ausente: {onnx_path.name}")
        if sha256_file(onnx_path) != expected_sha256:
            raise ModelNotAvailableError("ONNX diverge do checksum registrado")
        import onnxruntime as ort  # type: ignore[import-untyped]

        metadata = load_model_config()
        self.class_names: list[str] = list(metadata["canonical_class_names"])
        self.input_size: tuple[int, int] = tuple(metadata["test_size"])
        self.nms_threshold = float(metadata["evaluation"]["nms_threshold"])
        self.session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.provider = self.session.get_providers()[0]

    def raw(self, image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        tensor, ratio = _preprocess(image_bgr, self.input_size)
        (output,) = self.session.run(None, {"images": tensor})
        return output, ratio

    def detect(
        self, image_bytes: bytes, *, score_threshold: float = SERVING_SCORE_THRESHOLD
    ) -> list[ServedDetection]:
        import cv2
        from yolox.utils.demo_utils import multiclass_nms  # type: ignore[import-not-found]

        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("imagem não decodificável")
        height, width = image.shape[:2]
        output, ratio = self.raw(image)
        predictions = output[0]
        boxes = predictions[:, :4].copy()
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
        xyxy /= ratio
        scores = predictions[:, 4:5] * predictions[:, 5:]
        kept = multiclass_nms(xyxy, scores, nms_thr=self.nms_threshold, score_thr=score_threshold)
        detections: list[ServedDetection] = []
        if kept is None:
            return detections
        for x0, y0, x1, y1, score, class_index in kept:
            x0, x1 = max(0.0, float(x0)), min(float(width), float(x1))
            y0, y1 = max(0.0, float(y0)), min(float(height), float(y1))
            if x1 <= x0 or y1 <= y0:
                continue
            detections.append(
                ServedDetection(
                    urmind_class=self.class_names[int(class_index)],
                    confidence=round(float(score), 6),
                    bbox={
                        "x": x0 / width,
                        "y": y0 / height,
                        "width": (x1 - x0) / width,
                        "height": (y1 - y0) / height,
                    },
                )
            )
        return detections


def parity_check(checkpoint: Path, onnx_path: Path, image_paths: list[Path]) -> dict[str, Any]:
    """Máxima diferença absoluta PyTorch × ONNX na saída decodificada, em imagens reais."""
    import cv2
    import torch

    model, _ = load_checkpoint_model(checkpoint)
    detector = OnnxDetector(onnx_path, sha256_file(onnx_path))
    worst_box = worst_score = worst_box_rel = 0.0
    for path in image_paths:
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"imagem de VALIDATION ilegível: {path.name}")
        tensor, _ = _preprocess(image, detector.input_size)
        with torch.inference_mode():
            reference = model(torch.from_numpy(tensor)).numpy()
        onnx_output, _ = detector.raw(image)
        box_diff = np.abs(reference[..., :4] - onnx_output[..., :4])
        worst_box = max(worst_box, float(box_diff.max()))
        worst_box_rel = max(
            worst_box_rel, float((box_diff / np.maximum(np.abs(reference[..., :4]), 1.0)).max())
        )
        worst_score = max(
            worst_score, float(np.abs(reference[..., 4:] - onnx_output[..., 4:]).max())
        )
    # Caixas em pixels (0–640): tolerância relativa. Scores (0–1): tolerância absoluta.
    passed = worst_box_rel <= 1e-3 and worst_score <= 1e-4
    return {
        "images": len(image_paths),
        "max_abs_box_diff_px": worst_box,
        "max_rel_box_diff": worst_box_rel,
        "max_abs_score_diff": worst_score,
        "passed": passed,
    }


STAGE_BASELINE_EARLY = "baseline_early"
CONTRACT_NOT_MET = (
    "treino interrompido antes do contrato (max_epoch do yolox_model_v1.json); não é o modelo final"
)


def training_run_for(config_fingerprint: str | None, run_id: str | None = None) -> dict[str, Any]:
    """Parâmetros do run MLflow local que produziu o checkpoint (casado por config_fingerprint)."""
    import sqlite3

    database = PROJECT_ROOT / "mlruns" / "mlflow.db"
    if not config_fingerprint or not database.is_file():
        return {}
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "select r.run_uuid, r.start_time from runs r join params p on p.run_uuid = r.run_uuid "
            "where p.key = 'config_fingerprint' and p.value = ? and (? is null or r.run_uuid = ?) "
            "order by r.start_time desc limit 1",
            (config_fingerprint, run_id, run_id),
        ).fetchone()
        if row is None:
            return {}
        params = dict(
            connection.execute(
                "select key, value from params where run_uuid = ?", (row[0],)
            ).fetchall()
        )
    finally:
        connection.close()
    return {"mlflow_run_id": row[0], "started_at_ms": row[1], "params": params}


def validation_metrics_from_training_run(
    config_fingerprint: str, global_step: int, best_metric: float | None
) -> dict[str, Any]:
    """Métrica do evaluator oficial sobre a VALIDATION inteira, no passo exato do checkpoint.

    Não reavalia (o treino continua na mesma GPU); lê o que o próprio trainer mediu e
    exige que o mAP50-95 bata com o `best_metric` gravado dentro do checkpoint.
    """
    import sqlite3

    database = PROJECT_ROOT / "mlruns" / "mlflow.db"
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select m.run_uuid, m.key, m.value from metrics m "
            "join params p on p.run_uuid = m.run_uuid and p.key = 'config_fingerprint' "
            "where p.value = ? and m.step = ? and m.key like 'validation/%'",
            (config_fingerprint, global_step),
        ).fetchall()
    finally:
        connection.close()
    runs = {row[0] for row in rows}
    if len(runs) != 1:
        raise ValueError(
            f"validação do passo {global_step} não encontrada de forma única no MLflow"
        )
    values = {key.removeprefix("validation/"): value for _, key, value in rows}
    if best_metric is None or abs(values.get("map50_95", float("nan")) - best_metric) > 1e-6:
        raise ValueError("mAP50-95 do MLflow diverge do best_metric gravado no checkpoint")
    per_class: dict[str, dict[str, float]] = {}
    for key, value in values.items():
        if "/" in key:
            label, metric = key.split("/", 1)
            per_class.setdefault(label, {})[metric] = value
    return {
        "source": "training_run_validation (evaluator oficial, VALIDATION completa)",
        "mlflow_run_id": runs.pop(),
        "global_step": global_step,
        "precision": values.get("precision"),
        "recall": values.get("recall"),
        "map50": values.get("map50"),
        "map50_95": values.get("map50_95"),
        "per_class": per_class,
    }


def validation_images(limit: int) -> list[Path]:
    from app.ml.detection_dataset import load_authorized_manifest
    from app.ml.evaluator import manifest_for_evaluation

    rows = load_authorized_manifest(
        manifest_for_evaluation("VALIDATION"), intended_split="VALIDATION"
    )
    return [PROJECT_ROOT / row["image_path"] for row in rows[:limit]]


def evaluate_checkpoint_on_validation(checkpoint: Path, batch_size: int = 8) -> dict[str, Any]:
    """Métrica registrável: evaluator oficial sobre toda a VALIDATION autorizada."""
    from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator
    from app.ml.training import TrainingConfig, build_loaders

    metadata = load_model_config()
    model, _ = load_checkpoint_model(checkpoint)
    model.head.decode_in_inference = True
    model.cuda()
    config = TrainingConfig.from_model_metadata(metadata).with_overrides(batch_size=batch_size)
    _, validation_loader = build_loaders(config)
    evaluation_config = EvaluationConfig.from_model_metadata(metadata)
    run = YOLOXEvaluator(model, validation_loader, evaluation_config, device="cuda").evaluate()
    from app.ml.metrics import hard_cases

    return {
        "samples": run.samples,
        "positive_images": run.positive_images,
        "negative_images": run.negative_images,
        **run.result.as_persisted(),
        "hard_cases": hard_cases(
            run.predictions,
            run.ground_truths,
            score_threshold=SERVING_SCORE_THRESHOLD,
            iou_threshold=evaluation_config.iou_threshold,
        ),
    }


def benchmark_onnx(
    onnx_path: Path, checksum: str, *, images: int = 30, warmup: int = 10, runs: int = 200
) -> dict[str, Any]:
    """Latência pura do modelo (session.run) em imagens reais; pré/pós medidos à parte."""
    import os
    import platform
    import statistics
    import time

    import cv2
    import onnxruntime as ort  # type: ignore[import-untyped]

    detector = OnnxDetector(onnx_path, checksum)
    paths = validation_images(images)
    tensors = []
    for path in paths:
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"imagem de VALIDATION ilegível: {path.name}")
        tensors.append(_preprocess(image, detector.input_size)[0])
    for index in range(warmup):
        detector.session.run(None, {"images": tensors[index % len(tensors)]})
    samples = []
    for index in range(runs):
        started = time.perf_counter()
        detector.session.run(None, {"images": tensors[index % len(tensors)]})
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    end_to_end = []
    for path in paths[:20]:
        data = path.read_bytes()
        started = time.perf_counter()
        detector.detect(data)
        end_to_end.append((time.perf_counter() - started) * 1000)
    return {
        "scope": "model_only (onnxruntime session.run); upload/Storage/fila/PostGIS excluídos",
        "hardware": {
            "cpu": platform.processor() or platform.machine(),
            "logical_cores": os.cpu_count(),
            "os": platform.platform(),
        },
        "onnxruntime": ort.__version__,
        "execution_provider": detector.provider,
        "input_size": list(detector.input_size),
        "real_images": len(tensors),
        "warmup_runs": warmup,
        "timed_runs": runs,
        "latency_ms": {
            "mean": round(statistics.fmean(samples), 2),
            "p50": round(ordered[len(ordered) // 2], 2),
            "p95": round(ordered[int(len(ordered) * 0.95) - 1], 2),
        },
        "fps_approx": round(1000 / statistics.fmean(samples), 2),
        "decode_preprocess_infer_postprocess_ms": {
            "mean": round(statistics.fmean(end_to_end), 2),
            "images": len(end_to_end),
        },
    }


def log_mlflow(
    run_name: str,
    tags: dict[str, str],
    params: dict[str, Any],
    metrics: dict[str, float | None],
    document: dict[str, Any],
) -> str:
    """Run MLflow local (mesmo SQLite e experimento do treino) para avaliação/benchmark."""
    import mlflow

    metadata = load_model_config()
    mlflow.set_tracking_uri(f"sqlite:///{(PROJECT_ROOT / 'mlruns' / 'mlflow.db').as_posix()}")
    mlflow.set_experiment(metadata["mlflow"]["experiment"])
    with mlflow.start_run(run_name=run_name, tags=tags) as run:
        mlflow.log_params({key: str(value) for key, value in params.items()})
        mlflow.log_metrics(
            {key: float(value) for key, value in metrics.items() if value is not None}
        )
        mlflow.log_dict(document, f"{run_name}.json")
        return str(run.info.run_id)


async def _promoted_model() -> dict[str, Any]:
    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import InferenceRepository

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            model = await InferenceRepository(session).promoted_vision_model()
            if model is None:
                raise ValueError("nenhum modelo promovido")
            return {
                "id": model.id,
                "version": model.version,
                "checksum": model.checksum,
                "metrics": model.metrics,
            }
    finally:
        await database.close()


async def _attach_report(model_version_id: Any, key: str, value: dict[str, Any]) -> None:
    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import InferenceRepository

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            await InferenceRepository(session).merge_model_metrics(model_version_id, key, value)
            await session.commit()
    finally:
        await database.close()


def _run_async(coroutine: Any) -> Any:
    import asyncio
    import selectors
    import sys

    if sys.platform == "win32":
        return asyncio.run(
            coroutine, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coroutine)


async def register_model(manifest_path: Path, *, promote: bool) -> dict[str, Any]:
    """Grava dataset_versions + model_versions a partir de um export verificado (§9, §10)."""
    from datetime import UTC, datetime

    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import InferenceRepository

    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    metrics = record.get("validation_metrics")
    if not metrics or metrics.get("map50_95") is None:
        raise ValueError(
            "registro exige métrica de VALIDATION medida; exporte sem --skip-validation"
        )
    if not record.get("parity", {}).get("passed"):
        raise ValueError("registro exige paridade PyTorch x ONNX aprovada")
    onnx_path = PROJECT_ROOT / record["onnx_path"]
    if sha256_file(onnx_path) != record["onnx_sha256"]:
        raise ValueError("ONNX em disco diverge do manifesto de export")
    metadata = load_model_config()
    readiness = json.loads(
        (PROJECT_ROOT / "datasets/reports/pre_training_readiness.json").read_text(encoding="utf-8")
    )
    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            repository = InferenceRepository(session)
            dataset = await repository.upsert_dataset_version(
                name="rdd2022-model-v1-authorized",
                version=readiness["integrity"]["rdd_split_sha256"][:16],
                source="https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_Damage_Dataset_released_through_CRDDC_2022/21431547",
                license="CC BY 4.0",
                classes=list(metadata["canonical_class_names"]),
                split={
                    "manifests_sha256": readiness["integrity"]["manifests_sha256"],
                    "rdd_split_sha256": readiness["integrity"]["rdd_split_sha256"],
                    "counts": readiness["counts"],
                },
            )
            model = await repository.register_model(
                name=metadata["model_id"],
                kind="vision",
                version=f"{record.get('stage', STAGE_BASELINE_EARLY)}-epoch{record['epoch']}-{record['onnx_sha256'][:12]}",
                checksum=record["onnx_sha256"],
                dataset_version_id=dataset.id,
                metrics={
                    "stage": record.get("stage", STAGE_BASELINE_EARLY),
                    "stage_note": record.get("stage_note"),
                    "training": record.get("training"),
                    "code": record.get("code"),
                    "fingerprints": {
                        key: record.get(key)
                        for key in (
                            "config_fingerprint",
                            "dataset_fingerprint",
                            "split_fingerprint",
                            "class_mapping_fingerprint",
                        )
                    },
                    "metrics_not_computed": record.get("metrics_not_computed", []),
                    "validation": metrics,
                    "serving": {
                        "onnx_path": record["onnx_path"],
                        "opset": record["opset"],
                        "input_size": record["input_size"],
                        "class_names": record["class_names"],
                        "score_threshold": record["serving_score_threshold"],
                        "parity": record["parity"],
                        "checkpoint_sha256": record["checkpoint_sha256"],
                        "model_contract_sha256": record["model_contract_sha256"],
                    },
                },
                promoted_at=datetime.now(UTC) if promote else None,
            )
            if promote and model.promoted_at is None:
                model.promoted_at = datetime.now(UTC)
            await session.commit()
            return {
                "model_version_id": str(model.id),
                "version": model.version,
                "promoted": model.promoted_at is not None,
            }
    finally:
        await database.close()


def _report_command(command: str) -> int:
    from datetime import UTC, datetime

    model = _run_async(_promoted_model())
    serving = model["metrics"]["serving"]
    tags = {
        "model_version_id": str(model["id"]),
        "model_version": model["version"],
        "stage": str(model["metrics"].get("stage", "")),
    }
    flat: dict[str, float | None]
    if command == "evaluate":
        metadata = load_model_config()
        checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt"
        if sha256_file(checkpoint) != serving["checkpoint_sha256"]:
            raise SystemExit(
                "best.pt atual não é o checkpoint do modelo promovido; avaliação recusada"
            )
        report = evaluate_checkpoint_on_validation(checkpoint)
        flat = {
            f"validation/{key}": report[key]
            for key in ("map50", "map50_95", "precision", "recall", "f1")
        }
        for label, values in report["per_class"].items():
            for key in ("precision", "recall", "f1", "ap50", "ap50_95"):
                flat[f"validation/{label}/{key}"] = values[key]
        key = "evaluation_full"
    else:
        report = benchmark_onnx(PROJECT_ROOT / serving["onnx_path"], model["checksum"])
        flat = {
            "latency/mean_ms": report["latency_ms"]["mean"],
            "latency/p50_ms": report["latency_ms"]["p50"],
            "latency/p95_ms": report["latency_ms"]["p95"],
            "latency/fps": report["fps_approx"],
        }
        key = "benchmark"
    report["measured_at"] = datetime.now(UTC).isoformat()
    report["mlflow_run_id"] = log_mlflow(
        f"{key}-{model['version']}",
        tags,
        {"checkpoint_sha256": serving["checkpoint_sha256"], "onnx_sha256": model["checksum"]},
        flat,
        report,
    )
    _run_async(_attach_report(model["id"], key, report))
    summary = {k: v for k, v in report.items() if k not in ("per_class", "confusion", "hard_cases")}
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--checkpoint", choices=["best", "last"], default="best")
    export.add_argument("--parity-images", type=int, default=8)
    export.add_argument("--skip-validation", action="store_true")
    export.add_argument(
        "--recompute-validation",
        action="store_true",
        help="reavalia na GPU em vez de usar a validação do próprio run de treino",
    )
    sub.add_parser("evaluate", help="reavalia o modelo promovido na VALIDATION completa")
    sub.add_parser("benchmark", help="latência do ONNX promovido no hardware atual")
    register = sub.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--promote", action="store_true")
    args = parser.parse_args(argv)

    if args.command in ("evaluate", "benchmark"):
        return _report_command(args.command)

    if args.command == "register":
        import asyncio
        import selectors
        import sys

        coroutine = register_model(args.manifest, promote=args.promote)
        if sys.platform == "win32":
            result = asyncio.run(
                coroutine,
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            result = asyncio.run(coroutine)
        print(json.dumps(result, indent=2))
        return 0

    metadata = load_model_config()
    checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / f"{args.checkpoint}.pt"
    if not checkpoint.is_file():
        raise SystemExit(f"checkpoint {args.checkpoint} ainda não existe")
    info = json.loads(json.dumps({"checkpoint": args.checkpoint}))
    staging = SERVING_DIR / f".{metadata['model_id']}.onnx.tmp"
    exported = export_onnx(checkpoint, staging)
    final = SERVING_DIR / f"{metadata['model_id']}-{exported['onnx_sha256'][:12]}.onnx"
    staging.replace(final)
    parity = parity_check(checkpoint, final, validation_images(args.parity_images))
    if not parity["passed"]:
        final.unlink()
        raise SystemExit(f"paridade PyTorch x ONNX reprovada: {parity}")
    if args.skip_validation:
        metrics = None
    elif args.recompute_validation:
        metrics = evaluate_checkpoint_on_validation(checkpoint)
    else:
        metrics = validation_metrics_from_training_run(
            exported["config_fingerprint"], exported["global_step"], exported["best_metric"]
        )
    run = training_run_for(exported.get("config_fingerprint"), (metrics or {}).get("mlflow_run_id"))
    completed_epochs = (exported["epoch"] + 1) if exported.get("epoch") is not None else None
    max_epoch = int(metadata["training"]["max_epoch"])
    is_final = False if completed_epochs is None else completed_epochs >= max_epoch
    import subprocess
    from datetime import UTC, datetime

    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    record = {
        **info,
        "stage": "final" if is_final else STAGE_BASELINE_EARLY,
        "stage_note": None if is_final else CONTRACT_NOT_MET,
        "training": {
            "completed_epochs": completed_epochs,
            "contract_max_epoch": max_epoch,
            "run": run,
        },
        "code": {
            "git_commit": run.get("params", {}).get("git_commit"),
            "working_tree_dirty_at_export": bool(dirty),
        },
        "metrics_not_computed": ["ap75"],
        "exported_at": datetime.now(UTC).isoformat(),
        **exported,
        "onnx_path": final.relative_to(PROJECT_ROOT).as_posix(),
        "parity": parity,
        "validation_metrics": metrics,
        "serving_score_threshold": SERVING_SCORE_THRESHOLD,
    }
    manifest = final.with_suffix(".json")
    manifest.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "validation_metrics"}, indent=2))
    if metrics:
        print(json.dumps({"map50": metrics.get("map50"), "map50_95": metrics.get("map50_95")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
