from __future__ import annotations

import copy
import json
import math
import uuid
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.ml.serving import (
    CLOSURE_PRODUCER,
    CLOSURE_PRODUCER_VERSION,
    MODEL_METADATA_RECONCILIATION_REASON,
    _canonical_artifact_hash,
    _manifest_evidence,
    _timing_summary,
    benchmark_onnx,
    build_closure_artifact,
    build_model_metadata_reconciliation_audit,
    build_operating_point_lock,
    evaluate_quality_contract,
    open_frozen_test_once,
    validate_benchmark_report,
    validate_registration_manifest,
)
from app.ml.training import TrainingConfig, checkpoint_fingerprints
from app.ml.yolox_model import load_model_config


def test_benchmark_schema_separa_estagios_e_preserva_compatibilidade(
    monkeypatch, tmp_path: Path
) -> None:
    from app.ml import serving

    image = tmp_path / "validation.jpg"
    image.write_bytes(b"validation-image")
    clock = iter(
        [
            0.0,
            0.005,
            1.0,
            1.001,
            1.003,
            1.010,
            1.012,
            2.0,
            2.001,
            2.003,
            2.010,
            2.012,
        ]
    )

    class FakeDetector:
        provider = "CPUExecutionProvider"
        input_size = (640, 640)
        class_names = (
            "URMIND_ROAD_D00",
            "URMIND_ROAD_D10",
            "URMIND_ROAD_D20",
            "URMIND_ROAD_D40",
        )

        def __init__(self, path, checksum, **kwargs):
            self.nms_threshold = kwargs["nms_threshold"]

        def detect(self, payload, *, score_threshold):
            return []

        def decode(self, payload):
            return np.zeros((10, 20, 3), dtype=np.uint8)

        def preprocess(self, image):
            return np.zeros((1, 3, 640, 640), dtype=np.float32), 1.0

        def infer(self, tensor):
            return np.zeros((1, 1, 9), dtype=np.float32)

        def postprocess(self, output, ratio, image_shape, *, score_threshold):
            return []

    monkeypatch.setattr(serving, "OnnxDetector", FakeDetector)
    monkeypatch.setattr(serving, "validation_images", lambda limit: [image])
    monkeypatch.setattr("time.perf_counter", lambda: next(clock))

    report = benchmark_onnx(
        tmp_path / "model.onnx",
        "a" * 64,
        model_version="candidate-1",
        confidence_threshold=0.25,
        nms_threshold=0.5,
        images=1,
        warmup=1,
        runs=2,
    )

    assert report["schema_version"] == 2
    assert report["benchmark_data_role"] == "VALIDATION"
    assert report["model_version"] == "candidate-1"
    assert report["operating_point"] == {"confidence_threshold": 0.25, "nms_threshold": 0.5}
    assert report["measured_iterations"] == 2
    assert report["stages_ms"]["decode"]["mean"] == 1.0
    assert report["stages_ms"]["preprocess"]["mean"] == 2.0
    assert report["stages_ms"]["inference"]["mean"] == 7.0
    assert report["stages_ms"]["postprocess"]["mean"] == 2.0
    assert report["stages_ms"]["pipeline"]["mean"] == 12.0
    assert report["latency_ms"] == report["stages_ms"]["inference"]
    assert report["decode_preprocess_infer_postprocess_ms"]["mean"] == 12.0
    assert report["memory"]["vram_mb"] is None
    assert report["memory"]["vram_status"] == "NOT_APPLICABLE"
    assert json.loads(json.dumps(report))["schema_version"] == 2


def test_timing_summary_percentis_e_rejeita_valores_invalidos() -> None:
    summary = _timing_summary([1.0, 2.0, 3.0, 4.0])
    assert summary["count"] == 4
    assert summary["min"] <= summary["p50"] <= summary["p95"] <= summary["max"]
    for invalid in ([], [-1.0], [math.nan], [math.inf]):
        with pytest.raises(ValueError, match="finitas"):
            _timing_summary(invalid)


def test_benchmark_report_falha_fechado_para_sha_operating_point_e_metricas() -> None:
    summary = {"count": 2, "mean": 1.0, "p50": 1.0, "p95": 1.0, "min": 1.0, "max": 1.0}
    report = {
        "schema_version": 2,
        "benchmark_data_role": "VALIDATION",
        "model_version": "candidate-1",
        "onnx_sha256": "a" * 64,
        "operating_point": {"confidence_threshold": 0.25, "nms_threshold": 0.5},
        "measured_iterations": 2,
        "stages_ms": {
            **{
                stage: dict(summary)
                for stage in ("decode", "preprocess", "inference", "postprocess")
            },
            "pipeline": {
                "count": 2,
                "mean": 4.0,
                "p50": 4.0,
                "p95": 4.0,
                "min": 4.0,
                "max": 4.0,
            },
        },
        "memory": {
            "baseline_process_rss_mb": 100.0,
            "process_rss_mb": 110.0,
            "observed_peak_process_rss_mb": 120.0,
            "vram_mb": None,
            "vram_status": "NOT_APPLICABLE",
        },
        "execution_provider": "CPUExecutionProvider",
    }
    validate_benchmark_report(
        report,
        expected_sha256="a" * 64,
        expected_confidence=0.25,
        expected_nms=0.5,
    )

    for field, value, message in (
        ("onnx_sha256", "b" * 64, "SHA256"),
        ("operating_point", {"confidence_threshold": 0.30, "nms_threshold": 0.5}, "operating"),
    ):
        changed = copy.deepcopy(report)
        changed[field] = value
        with pytest.raises(ValueError, match=message):
            validate_benchmark_report(
                changed,
                expected_sha256="a" * 64,
                expected_confidence=0.25,
                expected_nms=0.5,
            )

    for invalid in (-1.0, math.nan, math.inf):
        changed = copy.deepcopy(report)
        changed["stages_ms"]["inference"]["mean"] = invalid
        with pytest.raises(ValueError, match="latência inválida"):
            validate_benchmark_report(
                changed,
                expected_sha256="a" * 64,
                expected_confidence=0.25,
                expected_nms=0.5,
            )

    changed = copy.deepcopy(report)
    changed["stages_ms"]["pipeline"]["mean"] = 9.0
    with pytest.raises(ValueError, match="soma dos estágios"):
        validate_benchmark_report(
            changed,
            expected_sha256="a" * 64,
            expected_confidence=0.25,
            expected_nms=0.5,
        )


def _metrics(*, role: str) -> dict:
    supports = {
        "VALIDATION": {"D00": 4104, "D10": 2359, "D20": 934, "D40": 321},
        "TEST": {"D00": 7738, "D10": 3694, "D20": 995, "D40": 332},
    }[role]
    return {
        "samples": 3858 if role == "VALIDATION" else 7634,
        "score_threshold": 0.2,
        "evaluation_nms_threshold": 0.65,
        "precision": 0.2,
        "recall": 0.3,
        "f1": 0.24,
        "map50": 0.1,
        "map50_95": 0.04,
        "per_class": {
            label: {
                "support": support,
                "true_positives": 1,
                "false_positives": 2,
                "false_negatives": 3,
                "precision": 0.2,
                "recall": 0.3,
                "f1": 0.24,
                "ap50": 0.1,
                "ap50_95": 0.04,
            }
            for label, support in supports.items()
        },
    }


def _fixture() -> tuple[dict, dict, dict, dict]:
    metadata = load_model_config()
    config = TrainingConfig.from_model_metadata(metadata).with_overrides(batch_size=8)
    fingerprints = checkpoint_fingerprints(metadata, config)
    run = {
        "mlflow_run_id": "training-run-1",
        "status": "FINISHED",
        "latest_train_epoch": 299.0,
        "params": {
            "model_id": metadata["model_id"],
            "architecture": metadata["architecture"],
            "input_size": "640x640",
            "class_order": "D00,D10,D20,D40",
            "batch_size": "8",
            "max_epoch": "300",
            **fingerprints,
        },
    }
    record = {
        "stage": "final",
        "checkpoint": "best",
        "epoch": 29,
        "global_step": 89370,
        "best_metric": 0.04,
        "checkpoint_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "model_contract_sha256": _contract_sha256(),
        **fingerprints,
        "onnx_path": "models/serving/model.onnx",
        "opset": 17,
        "input_size": [640, 640],
        "class_names": list(metadata["canonical_class_names"]),
        "validation_metrics": {"map50_95": 0.04},
        "parity": {"passed": True},
        "training": {"completed_epochs": 300, "contract_max_epoch": 300},
        "serving_score_threshold": 0.2,
    }
    validation_manifest = Path(_manifest_evidence("VALIDATION")["path"])
    test_manifest = Path(_manifest_evidence("TEST")["path"])
    project_root = Path(__file__).resolve().parents[2]
    lock = build_operating_point_lock(
        record,
        training_run_id=run["mlflow_run_id"],
        contract_path=project_root / "datasets/metadata/yolox_model_v1.json",
        validation_manifest=project_root / validation_manifest,
        test_manifest=project_root / test_manifest,
        representation="raw",
        confidence_threshold=0.2,
        nms_threshold=0.65,
        validation_metrics=_metrics(role="VALIDATION"),
        quality_contract={
            "schema_version": 1,
            "source_role": "VALIDATION",
            "decision": "APPROVED",
            "passed": True,
            "criteria": {"fixture": True},
        },
        generated_at="2026-09-20T11:00:00+00:00",
    )
    record["quality_classification"] = "APPROVED"
    record["operating_point_lock"] = lock
    record["benchmark"] = {
        "passed": True,
        "onnx_sha256": record["onnx_sha256"],
        "confidence_threshold": 0.2,
        "nms_threshold": 0.65,
    }
    closure = build_closure_artifact(
        record,
        training_run=run,
        completion_reason="MAX_EPOCH_REACHED",
        selection_metrics=_metrics(role="VALIDATION"),
        operating_metrics=_metrics(role="VALIDATION"),
        test_metrics=_metrics(role="TEST"),
        confidence_threshold=0.2,
        nms_threshold=0.65,
        generated_at="2026-09-20T12:00:00+00:00",
    )
    closure_run = _closure_run(closure, run)
    return record, closure, run, closure_run


def _contract_sha256() -> str:
    from app.ml.serving import sha256_file
    from app.ml.yolox_model import MODEL_METADATA_PATH

    return sha256_file(MODEL_METADATA_PATH)


def _closure_run(closure: dict, training_run: dict) -> dict:
    manifests = closure["dataset"]["manifests"]
    return {
        "mlflow_run_id": "closure-run-1",
        "status": "FINISHED",
        "artifact_closure_sha256": closure["closure_manifest_sha256"],
        "params": {
            "producer": CLOSURE_PRODUCER,
            "producer_version": str(CLOSURE_PRODUCER_VERSION),
            "closure_manifest_sha256": closure["closure_manifest_sha256"],
            "training_run_id": training_run["mlflow_run_id"],
            "checkpoint_sha256": closure["checkpoint"]["sha256"],
            "validation_manifest_sha256": manifests["VALIDATION"]["sha256"],
            "test_manifest_sha256": manifests["TEST"]["sha256"],
            "dataset_fingerprint": closure["dataset"]["fingerprint"],
        },
    }


def _rehash(closure: dict) -> None:
    closure["closure_manifest_sha256"] = _canonical_artifact_hash(closure)


def _validate(record: dict, closure: dict, run: dict, closure_run: dict) -> None:
    validate_registration_manifest(
        record,
        promote=True,
        closure=closure,
        training_run=run,
        closure_run=closure_run,
    )


def test_canonical_closure_happy_path_passes() -> None:
    _validate(*_fixture())


def test_registered_model_evidence_requires_real_manifest(tmp_path: Path, monkeypatch) -> None:
    from app.ml import serving

    monkeypatch.setattr(serving, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(serving, "SERVING_DIR", tmp_path / "models" / "serving")
    model = SimpleNamespace(metrics={})
    with pytest.raises(TypeError, match="promotion manifest ausente do ModelVersion"):
        serving.validate_registered_model_evidence(model, SimpleNamespace())

    model = SimpleNamespace(
        metrics={
            "promotion_manifest": {
                "path": "models/serving/missing.json",
                "sha256": "a" * 64,
            }
        }
    )

    with pytest.raises(ValueError, match="promotion manifest ausente ou alterado"):
        serving.validate_registered_model_evidence(model, SimpleNamespace())


def test_registered_model_evidence_binds_real_artifacts_and_dataset(
    tmp_path: Path, monkeypatch
) -> None:
    from app.ml import serving

    record, closure, _, _ = _fixture()
    serving_dir = tmp_path / "models" / "serving"
    serving_dir.mkdir(parents=True)
    checkpoint_dir = tmp_path / "models" / "checkpoints" / "candidate"
    checkpoint_dir.mkdir(parents=True)
    checkpoint = checkpoint_dir / "best.pt"
    checkpoint.write_bytes(b"checkpoint")
    onnx = serving_dir / "model.onnx"
    onnx.write_bytes(b"onnx")
    record["checkpoint_sha256"] = serving.sha256_file(checkpoint)
    record["onnx_sha256"] = serving.sha256_file(onnx)
    record["model_contract_path"] = "datasets/metadata/model.json"
    closure_path = serving_dir / "closure.json"
    closure_path.write_text(json.dumps(closure), encoding="utf-8")
    closure_reference = {
        "path": "models/serving/closure.json",
        "sha256": serving.sha256_file(closure_path),
        "mlflow_run_id": "closure-run-1",
    }
    record["closure_artifact"] = closure_reference
    manifest_path = serving_dir / "promotion.json"
    manifest_path.write_text(json.dumps(record), encoding="utf-8")
    metadata = {
        "model_id": "candidate",
        "dataset": {"dataset_version_name": "candidate-dataset"},
        "canonical_class_names": record["class_names"],
        "training": {"output_directory": "models/checkpoints/candidate"},
    }
    dataset = SimpleNamespace(
        id=uuid.uuid4(),
        name="candidate-dataset",
        version=closure["dataset"]["version"][:16],
        split={
            "dataset_fingerprint": closure["dataset"]["fingerprint"],
            "split_fingerprint": closure["dataset"]["version"],
            "manifests": closure["dataset"]["manifests"],
        },
        classes=record["class_names"],
    )
    model = SimpleNamespace(
        kind="vision",
        name="candidate",
        version=f"final-epoch{record['epoch']}-{record['onnx_sha256'][:12]}",
        checksum=record["onnx_sha256"],
        dataset_version_id=dataset.id,
        metrics={
            "promotion_manifest": {
                "path": "models/serving/promotion.json",
                "sha256": serving.sha256_file(manifest_path),
            },
            "quality_classification": record["quality_classification"],
            "frozen_test_quality": record.get("frozen_test_quality"),
            "closure_artifact": closure_reference,
            "benchmark": record["benchmark"],
            "operating_point": closure["operating_point"],
            "final_test": closure["final_test_report"],
            "fingerprints": {
                "dataset_fingerprint": closure["dataset"]["fingerprint"],
                "split_fingerprint": closure["dataset"]["version"],
            },
            "serving": {
                "onnx_path": record["onnx_path"],
                "checkpoint_sha256": record["checkpoint_sha256"],
                "model_contract_sha256": record["model_contract_sha256"],
                "score_threshold": record["serving_score_threshold"],
                "nms_threshold": closure["operating_point"]["nms_threshold"],
            },
        },
    )
    monkeypatch.setattr(serving, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(serving, "SERVING_DIR", serving_dir)
    monkeypatch.setattr(serving, "load_model_config", lambda _path: metadata)
    monkeypatch.setattr(serving, "training_run_for", lambda *_: {})
    monkeypatch.setattr(serving, "closure_run_for", lambda *_: {})
    validated = []
    monkeypatch.setattr(
        serving, "validate_registration_manifest", lambda *_args, **_kwargs: validated.append(True)
    )

    serving.validate_registered_model_evidence(model, dataset)
    assert validated
    checkpoint.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checkpoint ausente ou alterado"):
        serving.validate_registered_model_evidence(model, dataset)
    checkpoint.write_bytes(b"checkpoint")
    onnx.write_bytes(b"changed")
    with pytest.raises(ValueError, match="ONNX ausente ou alterado"):
        serving.validate_registered_model_evidence(model, dataset)
    onnx.write_bytes(b"onnx")
    closure_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="closure ausente ou alterado"):
        serving.validate_registered_model_evidence(model, dataset)
    closure_path.write_text(json.dumps(closure), encoding="utf-8")
    dataset.version = "wrong"
    with pytest.raises(ValueError, match="DatasetVersion diverge"):
        serving.validate_registered_model_evidence(model, dataset)


def _metadata_reconciliation_fixture() -> tuple[SimpleNamespace, SimpleNamespace, dict, dict]:
    record, closure, _, _ = _fixture()
    dataset_id = uuid.uuid4()
    closure_reference = {
        "path": "models/serving/closure.json",
        "sha256": "c" * 64,
        "mlflow_run_id": "closure-run-1",
    }
    metrics = {
        "checkpoint_selection": copy.deepcopy(closure["selection_evidence"]),
        "operating_point": copy.deepcopy(closure["operating_point"]),
        "final_test": copy.deepcopy(closure["final_test_report"]),
        "validation": copy.deepcopy(closure["selection_evidence"]["metrics"]),
        "closure_artifact": copy.deepcopy(closure_reference),
        "taxonomy": {
            "schema_version": closure["taxonomy"]["version"],
            "class_names": copy.deepcopy(closure["taxonomy"]["class_names"]),
            "canonical_class_names": copy.deepcopy(closure["taxonomy"]["canonical_class_names"]),
        },
        "fingerprints": {
            "dataset_fingerprint": closure["dataset"]["fingerprint"],
            "split_fingerprint": closure["dataset"]["version"],
            "class_mapping_fingerprint": closure["taxonomy"]["fingerprint"],
        },
        "serving": {
            "onnx_path": record["onnx_path"],
            "score_threshold": closure["operating_point"]["confidence_threshold"],
            "nms_threshold": closure["operating_point"]["nms_threshold"],
            "checkpoint_sha256": closure["checkpoint"]["sha256"],
        },
        "benchmark": {"onnx_sha256": record["onnx_sha256"]},
    }
    model = SimpleNamespace(
        id=uuid.uuid4(),
        checksum=record["onnx_sha256"],
        dataset_version_id=dataset_id,
        metrics=metrics,
        promoted_at=None,
    )
    dataset = SimpleNamespace(
        id=dataset_id,
        classes=copy.deepcopy(closure["taxonomy"]["canonical_class_names"]),
        split={"rdd_split_sha256": closure["dataset"]["version"]},
    )
    return model, dataset, closure, closure_reference


def _build_reconciliation(
    model: SimpleNamespace,
    dataset: SimpleNamespace,
    closure: dict,
    closure_reference: dict,
    *,
    previous_nms: float = 0.5,
    reason: str = MODEL_METADATA_RECONCILIATION_REASON,
) -> dict:
    return build_model_metadata_reconciliation_audit(
        model,
        dataset,
        closure,
        closure_reference=closure_reference,
        closure_file_sha256="c" * 64,
        checkpoint_file_sha256="a" * 64,
        onnx_file_sha256="b" * 64,
        previous_nms=previous_nms,
        expected_previous_nms=0.75,
        reason=reason,
    )


def test_metadata_reconciliation_descreve_before_after_e_regularizacao_posterior() -> None:
    model, dataset, closure, reference = _metadata_reconciliation_fixture()
    event = _build_reconciliation(model, dataset, closure, reference, previous_nms=0.75)

    assert event["entity_type"] == "ModelVersion"
    assert event["entity_id"] == str(model.id)
    assert event["before"]["serving"]["nms_threshold"] == 0.75
    assert event["after"]["serving"]["nms_threshold"] == 0.65
    assert event["after"]["reconciliation"] == {
        "reason": MODEL_METADATA_RECONCILIATION_REASON,
        "source": "canonical_model_closure",
        "closure_path": reference["path"],
        "recorded_after_correction": True,
    }
    assert len(event["event_hash"]) == 64
    assert model.metrics["serving"]["nms_threshold"] == 0.65
    assert model.promoted_at is None


def test_metadata_reconciliation_aceita_f1_derivado_ausente_no_registro_legado() -> None:
    model, dataset, closure, reference = _metadata_reconciliation_fixture()
    model.metrics["validation"].pop("f1")
    for per_class in model.metrics["validation"]["per_class"].values():
        per_class.pop("f1")

    event = _build_reconciliation(model, dataset, closure, reference, previous_nms=0.75)

    assert event["after"]["serving"]["nms_threshold"] == 0.65


@pytest.mark.parametrize(
    ("attack", "match"),
    [
        ("checkpoint", "artifact científico"),
        ("onnx", "artifact científico"),
        ("benchmark", "outro ONNX"),
        ("metrics", "métricas/evidências"),
        ("dataset", "DatasetVersion"),
        ("promote", "não pode promover"),
        ("confidence", "operating point"),
        ("nms", "operating point"),
        ("taxonomy", "taxonomia"),
        ("closure", "closure autoritativo"),
        ("unproven_before", "origem documentada"),
        ("reason", "motivo canônico"),
    ],
)
def test_metadata_reconciliation_rejeita_mutacao_cientifica(attack: str, match: str) -> None:
    model, dataset, closure, reference = _metadata_reconciliation_fixture()
    reason = MODEL_METADATA_RECONCILIATION_REASON
    if attack == "checkpoint":
        model.metrics["serving"]["checkpoint_sha256"] = "d" * 64
    elif attack == "onnx":
        model.checksum = "d" * 64
    elif attack == "benchmark":
        model.metrics.pop("benchmark")
    elif attack == "metrics":
        model.metrics["final_test"]["metrics"]["map50"] = 0.9
    elif attack == "dataset":
        model.dataset_version_id = uuid.uuid4()
    elif attack == "promote":
        model.promoted_at = "2026-09-20T00:00:00+00:00"
    elif attack == "confidence":
        model.metrics["serving"]["score_threshold"] = 0.9
    elif attack == "nms":
        model.metrics["serving"]["nms_threshold"] = 0.9
    elif attack == "taxonomy":
        model.metrics["taxonomy"]["class_names"] = ["D00"]
    elif attack == "closure":
        reference["sha256"] = "d" * 64
    elif attack == "unproven_before":
        with pytest.raises(ValueError, match=match):
            build_model_metadata_reconciliation_audit(
                model,
                dataset,
                closure,
                closure_reference=reference,
                closure_file_sha256="c" * 64,
                checkpoint_file_sha256="a" * 64,
                onnx_file_sha256="b" * 64,
                previous_nms=0.70,
                expected_previous_nms=0.75,
                reason=reason,
            )
        return
    elif attack == "reason":
        reason = ""
    with pytest.raises(ValueError, match=match):
        _build_reconciliation(
            model,
            dataset,
            closure,
            reference,
            previous_nms=0.75,
            reason=reason,
        )


def test_closure_hash_is_deterministic_and_sensitive() -> None:
    _, closure, _, _ = _fixture()
    first = _canonical_artifact_hash(closure)
    assert first == _canonical_artifact_hash(copy.deepcopy(closure))
    changed = copy.deepcopy(closure)
    changed["operating_point"]["confidence_threshold"] = 0.3
    assert _canonical_artifact_hash(changed) != first


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("reduced_manifest_max_epoch", "MANIFEST_CONTRACT_MISMATCH"),
        ("changed_run_max_epoch", "RUN_CONTRACT_MISMATCH"),
        ("other_training_run", "CHECKPOINT_RUN_MISMATCH"),
        ("validation_as_test", "TEST_ROLE_INVALID"),
        ("changed_test_manifest", "TEST_CHECKPOINT_MISMATCH"),
        ("changed_checkpoint", "OPERATING_POINT_LOCK_MISMATCH"),
        ("invented_metric", "TEST_METRICS_INVALID"),
        ("missing_d40", "TAXONOMY_MISMATCH"),
        ("nan_metric", "TEST_METRICS_INVALID"),
        ("inf_metric", "TEST_METRICS_INVALID"),
        ("changed_threshold", "OPERATING_POINT_MISMATCH"),
        ("changed_thresholds_after_evaluation", "OPERATING_POINT_MISMATCH"),
        ("changed_dataset", "OPERATING_POINT_LOCK_MISMATCH"),
        ("operating_other_checkpoint", "OPERATING_POINT_MISMATCH"),
        ("operating_other_validation", "OPERATING_POINT_MISMATCH"),
        ("invalid_producer", "CLOSURE_MANIFEST_INVALID"),
    ],
)
def test_promotion_gate_rejects_attacks(attack: str, reason: str) -> None:
    record, closure, run, closure_run = _fixture()
    if attack == "reduced_manifest_max_epoch":
        record["training"]["contract_max_epoch"] = 30
    elif attack == "changed_run_max_epoch":
        run["params"]["max_epoch"] = "30"
    elif attack == "other_training_run":
        run["mlflow_run_id"] = "other-run"
    elif attack == "validation_as_test":
        closure["final_test_report"]["role"] = "VALIDATION"
        _rehash(closure)
    elif attack == "changed_test_manifest":
        closure["final_test_report"]["manifest_sha256"] = "c" * 64
        _rehash(closure)
    elif attack == "changed_checkpoint":
        record["checkpoint_sha256"] = "c" * 64
    elif attack == "invented_metric":
        closure["final_test_report"]["metrics"]["precision"] = "0.2"
        _rehash(closure)
    elif attack == "missing_d40":
        del closure["final_test_report"]["metrics"]["per_class"]["D40"]
        _rehash(closure)
    elif attack == "nan_metric":
        closure["final_test_report"]["metrics"]["map50"] = math.nan
        _rehash(closure)
    elif attack == "inf_metric":
        closure["final_test_report"]["metrics"]["map50"] = math.inf
        _rehash(closure)
    elif attack == "changed_threshold":
        record["serving_score_threshold"] = 0.3
    elif attack == "changed_thresholds_after_evaluation":
        record["serving_score_threshold"] = 0.3
        closure["operating_point"]["confidence_threshold"] = 0.3
        closure["final_test_report"]["confidence_threshold"] = 0.3
        _rehash(closure)
    elif attack == "changed_dataset":
        record["dataset_fingerprint"] = "c" * 64
    elif attack == "operating_other_checkpoint":
        closure["operating_point"]["checkpoint_sha256"] = "c" * 64
        _rehash(closure)
    elif attack == "operating_other_validation":
        closure["operating_point"]["manifest_sha256"] = "c" * 64
        _rehash(closure)
    elif attack == "invalid_producer":
        closure["producer"]["name"] = "manual"
        _rehash(closure)
    with pytest.raises(ValueError, match=reason):
        _validate(record, closure, run, closure_run)


def test_promotion_gate_rejects_extra_class_and_incomplete_schema() -> None:
    record, closure, run, closure_run = _fixture()
    closure["final_test_report"]["metrics"]["per_class"]["OTHER"] = copy.deepcopy(
        closure["final_test_report"]["metrics"]["per_class"]["D00"]
    )
    _rehash(closure)
    with pytest.raises(ValueError, match="TAXONOMY_MISMATCH"):
        _validate(record, closure, run, closure_run)

    record, closure, run, closure_run = _fixture()
    del closure["final_test_report"]["metrics"]["per_class"]["D00"]["ap50"]
    _rehash(closure)
    with pytest.raises(ValueError, match="TEST_METRICS_INVALID"):
        _validate(record, closure, run, closure_run)


def test_manual_json_without_canonical_mlflow_evidence_is_rejected() -> None:
    record, closure, run, _ = _fixture()
    with pytest.raises(ValueError, match="CLOSURE_MANIFEST_INVALID"):
        _validate(record, closure, run, {})


def test_early_stop_requires_bound_auditable_approval() -> None:
    record, closure, run, _ = _fixture()
    run["latest_train_epoch"] = 159.0
    approval = {
        "schema_version": 1,
        "decision": "EARLY_STOP_APPROVED",
        "training_run_id": run["mlflow_run_id"],
        "checkpoint_sha256": record["checkpoint_sha256"],
        "training_contract_sha256": record["model_contract_sha256"],
        "approved_by": "model-review-board",
        "approved_at": "2026-09-20T12:00:00+00:00",
        "reason": "validation plateau documented",
        "persisted_last_checkpoint_sha256": "d" * 64,
        "persisted_last_epoch": 160,
    }
    approval["approval_sha256"] = _canonical_artifact_hash(approval, hash_field="approval_sha256")
    closure["training"].update(
        completed_epochs=160,
        completion_reason="EARLY_STOP_APPROVED",
        early_stop_approval=approval,
    )
    _rehash(closure)
    closure_run = _closure_run(closure, run)
    _validate(record, closure, run, closure_run)

    closure["training"]["early_stop_approval"]["checkpoint_sha256"] = "c" * 64
    _rehash(closure)
    closure_run = _closure_run(closure, run)
    with pytest.raises(ValueError, match="TRAINING_NOT_COMPLETE"):
        _validate(record, closure, run, closure_run)


def test_early_stop_uses_persisted_epoch_not_partial_mlflow_epoch() -> None:
    record, closure, run, _ = _fixture()
    run["latest_train_epoch"] = 160.0
    approval = {
        "schema_version": 1,
        "decision": "EARLY_STOP_APPROVED",
        "training_run_id": run["mlflow_run_id"],
        "checkpoint_sha256": record["checkpoint_sha256"],
        "training_contract_sha256": record["model_contract_sha256"],
        "approved_by": "model-review-board",
        "approved_at": "2026-09-20T12:00:00+00:00",
        "reason": "validation plateau documented",
        "persisted_last_checkpoint_sha256": "d" * 64,
        "persisted_last_epoch": 160,
    }
    approval["approval_sha256"] = _canonical_artifact_hash(approval, hash_field="approval_sha256")
    closure["training"].update(
        completed_epochs=160,
        completion_reason="EARLY_STOP_APPROVED",
        early_stop_approval=approval,
    )
    _rehash(closure)
    _validate(record, closure, run, _closure_run(closure, run))

    closure["training"]["completed_epochs"] = 161
    _rehash(closure)
    with pytest.raises(ValueError, match="TRAINING_NOT_COMPLETE"):
        _validate(record, closure, run, _closure_run(closure, run))


def test_finished_run_with_unapproved_early_epoch_is_rejected() -> None:
    record, closure, run, closure_run = _fixture()
    run["latest_train_epoch"] = 159.0
    closure["training"]["completed_epochs"] = 160
    _rehash(closure)
    closure_run = _closure_run(closure, run)
    with pytest.raises(ValueError, match="TRAINING_NOT_COMPLETE"):
        _validate(record, closure, run, closure_run)


def test_manifest_roles_are_distinct_and_frozen() -> None:
    validation = _manifest_evidence("VALIDATION")
    test = _manifest_evidence("TEST")
    assert validation["role"] == "VALIDATION"
    assert test["role"] == "TEST"
    assert validation["sha256"] != test["sha256"]


def test_close_model_rejects_candidate_binding_before_test_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import serving

    record, _, run, _ = _fixture()
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"controlled-checkpoint")
    from app.ml.serving import sha256_file

    record["checkpoint_sha256"] = sha256_file(checkpoint)
    record["dataset_fingerprint"] = "c" * 64
    record["validation_metrics"]["mlflow_run_id"] = run["mlflow_run_id"]
    lock = copy.deepcopy(record["operating_point_lock"])
    for key in (
        "checkpoint_sha256",
        "checkpoint_epoch",
        "global_step",
        "config_fingerprint",
        "dataset_fingerprint",
        "split_fingerprint",
    ):
        source = "epoch" if key == "checkpoint_epoch" else key
        lock[key] = record[source]
    lock["operating_point_lock_sha256"] = _canonical_artifact_hash(
        lock, hash_field="operating_point_lock_sha256"
    )
    operating_lock = tmp_path / "operating-point-lock.json"
    operating_lock.write_text(json.dumps(lock), encoding="utf-8")
    export_manifest = tmp_path / "export.json"
    export_manifest.write_text(json.dumps(record), encoding="utf-8")
    metadata = load_model_config()
    metadata["training"]["output_directory"] = str(tmp_path)
    monkeypatch.setattr(serving, "load_model_config", lambda path=None: metadata)
    monkeypatch.setattr(serving, "training_run_for", lambda *args: run)
    checkpoint_info = {
        "checkpoint_sha256": record["checkpoint_sha256"],
        "epoch": record["epoch"],
        "global_step": record["global_step"],
        "model_id": metadata["model_id"],
        "architecture": metadata["architecture"],
        "num_classes": metadata["num_classes"],
        "class_order": metadata["class_names"],
        **{key: value for key, value in run["params"].items() if key.endswith("_fingerprint")},
    }
    monkeypatch.setattr(
        serving,
        "load_checkpoint_model",
        lambda path, **kwargs: (object(), checkpoint_info),
    )
    evaluated = False

    def forbidden_evaluation(*args, **kwargs):
        nonlocal evaluated
        evaluated = True
        raise AssertionError("TEST/VALIDATION não deveria abrir")

    monkeypatch.setattr(serving, "evaluate_checkpoint_for_role", forbidden_evaluation)
    args = Namespace(
        export_manifest=export_manifest,
        operating_point_lock=operating_lock,
        output=tmp_path / "promotion.json",
        batch_size=1,
        early_stop_approval=None,
    )
    with pytest.raises(ValueError, match="RUN_CONTRACT_MISMATCH"):
        serving._close_model_command(args)
    assert not evaluated


def test_frozen_test_ledger_is_atomic_and_single_use(tmp_path: Path) -> None:
    record, _, _, _ = _fixture()
    lock = record["operating_point_lock"]
    ledger = tmp_path / "frozen-test-ledger.json"

    first = open_frozen_test_once(ledger, lock)

    assert first["state"] == "OPENED"
    assert json.loads(ledger.read_text(encoding="utf-8")) == first
    with pytest.raises(ValueError, match="FROZEN_TEST_ALREADY_OPENED"):
        open_frozen_test_once(ledger, lock)


def test_frozen_test_ledger_cannot_be_reopened_with_another_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import serving

    record, _, _, _ = _fixture()
    first_lock = record["operating_point_lock"]
    second_lock = copy.deepcopy(first_lock)
    second_lock["operating_point_lock_sha256"] = "b" * 64
    monkeypatch.setattr(serving, "SERVING_DIR", tmp_path)
    first_path = serving.frozen_test_ledger_path(first_lock)
    second_path = serving.frozen_test_ledger_path(second_lock)
    assert first_path == second_path
    open_frozen_test_once(first_path, first_lock)
    with pytest.raises(ValueError, match="FROZEN_TEST_ALREADY_OPENED"):
        open_frozen_test_once(second_path, second_lock)


def test_frozen_test_evaluation_claim_is_single_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import serving

    record, _, _, _ = _fixture()
    lock = record["operating_point_lock"]
    monkeypatch.setattr(serving, "SERVING_DIR", tmp_path)
    ledger = serving.frozen_test_ledger_path(lock)
    serving.open_frozen_test_once(ledger, lock)

    claim = serving.claim_frozen_test_evaluation_once(ledger, lock)

    assert json.loads(claim.read_text(encoding="utf-8"))["state"] == "EVALUATION_CLAIMED"
    with pytest.raises(ValueError, match="FROZEN_TEST_ALREADY_OPENED"):
        serving.claim_frozen_test_evaluation_once(ledger, lock)


def test_frozen_test_evaluation_rejects_existing_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import serving

    record, _, _, _ = _fixture()
    lock = record["operating_point_lock"]
    monkeypatch.setattr(serving, "SERVING_DIR", tmp_path)
    result = tmp_path / f"frozen-test-result-{lock['operating_point_lock_sha256'][:12]}.json"
    result.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="FROZEN_TEST_ALREADY_OPENED"):
        serving.claim_frozen_test_evaluation_once(serving.frozen_test_ledger_path(lock), lock)


@pytest.mark.parametrize("classification", ["WEAK", "NOT_APPROVED", "FAILED", "REJECTED"])
def test_promotion_rejects_explicitly_unapproved_quality(classification: str) -> None:
    record, closure, run, closure_run = _fixture()
    record["quality_classification"] = classification

    with pytest.raises(ValueError, match="QUALITY_GATE_REJECTED"):
        _validate(record, closure, run, closure_run)


def test_operating_point_lock_rejects_changed_checkpoint_and_threshold() -> None:
    record, closure, run, closure_run = _fixture()
    record["checkpoint_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="OPERATING_POINT_LOCK_MISMATCH"):
        _validate(record, closure, run, closure_run)

    record, closure, run, closure_run = _fixture()
    record["serving_score_threshold"] = 0.3
    with pytest.raises(ValueError, match="OPERATING_POINT_MISMATCH"):
        _validate(record, closure, run, closure_run)


def test_missing_quality_contract_stops_before_artifact_or_model_io(tmp_path, monkeypatch):
    from argparse import Namespace

    from app.ml import serving

    missing = tmp_path / "missing-quality.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("model/config must not be accessed without a quality contract")

    monkeypatch.setattr(serving, "load_model_config", forbidden)
    with pytest.raises(ValueError, match="QUALITY_GATE_NOT_AVAILABLE"):
        serving.select_operating_point_on_validation(
            tmp_path / "missing.pt",
            contract_path=tmp_path / "config.json",
            representation="ema",
            quality_contract_path=missing,
        )
    with pytest.raises(ValueError, match="QUALITY_GATE_NOT_AVAILABLE"):
        serving._lock_operating_point_command(Namespace(quality_contract=missing))
    with pytest.raises(ValueError, match="QUALITY_GATE_NOT_AVAILABLE"):
        serving.evaluate_quality_contract({}, {}, contract_path=missing)
    assert list(tmp_path.iterdir()) == []


def test_quality_contract_is_explicit_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml import serving

    monkeypatch.setattr(serving, "PROJECT_ROOT", tmp_path)
    contract_path = tmp_path / "quality.json"
    contract = {
        "schema_version": 1,
        "source_role": "VALIDATION",
        "criteria": [
            {"metric": "map50_95", "operator": ">=", "value": 0.2},
            {"metric": "per_class.D40.recall", "operator": ">=", "value": 0.4},
        ],
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    metrics = {"map50_95": 0.21, "per_class": {"D40": {"recall": 0.39}}}

    result = evaluate_quality_contract(metrics, contract, contract_path=contract_path)

    assert result["source_role"] == "VALIDATION"
    assert result["decision"] == "REJECTED"
    assert result["passed"] is False
    assert result["criteria"][1]["passed"] is False
    with pytest.raises(ValueError, match="quality metric ausente"):
        evaluate_quality_contract({}, contract, contract_path=contract_path)
