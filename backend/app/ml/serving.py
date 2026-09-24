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
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import numpy as np

from app.ml.yolox_model import MODEL_METADATA_PATH, load_model_config

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVING_DIR = PROJECT_ROOT / "models" / "serving"
READINESS_REPORT_PATH = PROJECT_ROOT / "datasets" / "reports" / "pre_training_readiness.json"
CLOSURE_SCHEMA_VERSION = 1
CLOSURE_PRODUCER = "app.ml.serving.close-model"
CLOSURE_PRODUCER_VERSION = 1
OPERATING_POINT_LOCK_PRODUCER = "app.ml.serving.lock-operating-point"
OPERATING_POINT_LOCK_VERSION = 1
FROZEN_TEST_LEDGER_VERSION = 1
OPSET = 17
# PROVISÓRIO (§31.16): limiar de produto, igual ao mínimo que vira evento no domínio.
# O 0,01 do contrato é o de mAP, não de decisão.
SERVING_SCORE_THRESHOLD = 0.25
MODEL_METADATA_RECONCILIATION_OPERATION = "metadata_reconciliation_recorded_after_correction"
MODEL_METADATA_RECONCILIATION_REASON = "reconciliation_with_authoritative_model_closure"
MODEL_METADATA_RECONCILIATION_ACTOR = "app.ml.serving.reconcile-model-metadata"


class ModelNotAvailableError(RuntimeError):
    """Sem ONNX verificado: o Worker grava `model_not_available`, nunca resultado falso."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _promotion_fail(code: str, detail: str) -> NoReturn:
    raise ValueError(f"PROMOTION_GATE_FAIL:{code}: {detail}")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_artifact_hash(
    document: dict[str, Any], *, hash_field: str = "closure_manifest_sha256"
) -> str:
    from app.ml.training import canonical_sha256

    payload = {key: value for key, value in document.items() if key != hash_field}
    return canonical_sha256(payload)


def evaluate_quality_contract(
    metrics: dict[str, Any], contract: dict[str, Any], *, contract_path: Path
) -> dict[str, Any]:
    """Aplica critérios pré-declarados de VALIDATION sem defaults implícitos."""
    if not contract_path.is_file():
        raise ValueError("QUALITY_GATE_NOT_AVAILABLE: contrato de qualidade ausente")
    if contract.get("schema_version") != 1 or contract.get("source_role") != "VALIDATION":
        raise ValueError("quality contract inválido ou não derivado de VALIDATION")

    def metric_value(path: str) -> float:
        value: Any = metrics
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"quality metric ausente: {path}")
            value = value[part]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"quality metric inválida: {path}")
        return float(value)

    results: list[dict[str, Any]] = []
    for criterion in contract.get("criteria", []):
        if not isinstance(criterion, dict):
            raise TypeError("quality criterion inválido")
        name = criterion.get("metric")
        operator = criterion.get("operator")
        target = criterion.get("value")
        if (
            not isinstance(name, str)
            or operator not in {">=", "<="}
            or isinstance(target, bool)
            or not isinstance(target, (int, float))
        ):
            raise ValueError("quality criterion malformado")
        actual = metric_value(name)
        passed = actual >= float(target) if operator == ">=" else actual <= float(target)
        results.append(
            {
                "metric": name,
                "operator": operator,
                "target": float(target),
                "actual": actual,
                "passed": passed,
            }
        )
    if not results:
        raise ValueError("quality contract sem critérios")
    passed = all(item["passed"] for item in results)
    return {
        "schema_version": 1,
        "source_role": "VALIDATION",
        "quality_contract_path": contract_path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "quality_contract_sha256": sha256_file(contract_path),
        "passed": passed,
        "decision": "APPROVED" if passed else "REJECTED",
        "criteria": results,
    }


def build_operating_point_lock(
    record: dict[str, Any],
    *,
    training_run_id: str,
    contract_path: Path,
    validation_manifest: Path,
    test_manifest: Path,
    representation: str,
    confidence_threshold: float,
    nms_threshold: float,
    validation_metrics: dict[str, Any],
    quality_contract: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Congela toda decisão selecionada em VALIDATION antes de qualquer TEST."""
    if representation not in {"raw", "ema"}:
        raise ValueError("representation deve ser raw ou ema")
    for name, value in {
        "confidence_threshold": confidence_threshold,
        "nms_threshold": nms_threshold,
    }.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f"{name} inválido")
    _validated_metrics(validation_metrics, code="VALIDATION_EVIDENCE_MISSING")
    if (
        validation_metrics.get("score_threshold") != confidence_threshold
        or validation_metrics.get("evaluation_nms_threshold") != nms_threshold
    ):
        raise ValueError("métricas de VALIDATION divergem do operating point")
    if quality_contract.get("source_role") != "VALIDATION" or quality_contract.get(
        "decision"
    ) not in {"APPROVED", "REJECTED"}:
        raise ValueError("quality contract deve ser decidido exclusivamente em VALIDATION")
    if quality_contract.get("decision") == "APPROVED" and not quality_contract.get("passed"):
        raise ValueError("quality contract aprovado precisa ter todos os critérios satisfeitos")
    document = {
        "schema_version": OPERATING_POINT_LOCK_VERSION,
        "producer": OPERATING_POINT_LOCK_PRODUCER,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "training_run_id": training_run_id,
        "checkpoint_sha256": record.get("checkpoint_sha256"),
        "checkpoint_epoch": record.get("epoch"),
        "global_step": record.get("global_step"),
        "representation": representation,
        "contract_path": contract_path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "contract_sha256": sha256_file(contract_path),
        "config_fingerprint": record.get("config_fingerprint"),
        "dataset_fingerprint": record.get("dataset_fingerprint"),
        "split_fingerprint": record.get("split_fingerprint"),
        "taxonomy_fingerprint": record.get("class_mapping_fingerprint"),
        "validation_manifest": {
            "path": validation_manifest.resolve().relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(validation_manifest),
        },
        "frozen_test_manifest": {
            "path": test_manifest.resolve().relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(test_manifest),
        },
        "confidence_threshold": float(confidence_threshold),
        "nms_threshold": float(nms_threshold),
        "validation_metrics": validation_metrics,
        "quality_contract": quality_contract,
    }
    document["operating_point_lock_sha256"] = _canonical_artifact_hash(
        document, hash_field="operating_point_lock_sha256"
    )
    return document


def validate_operating_point_lock(
    lock: dict[str, Any], record: dict[str, Any], *, require_approved: bool
) -> None:
    if (
        lock.get("schema_version") != OPERATING_POINT_LOCK_VERSION
        or lock.get("producer") != OPERATING_POINT_LOCK_PRODUCER
    ):
        _promotion_fail("OPERATING_POINT_LOCK_INVALID", "schema/produtor inválido")
    expected_hash = _canonical_artifact_hash(lock, hash_field="operating_point_lock_sha256")
    if lock.get("operating_point_lock_sha256") != expected_hash:
        _promotion_fail("OPERATING_POINT_LOCK_INVALID", "hash do lock inválido")
    bindings = {
        "checkpoint_sha256": record.get("checkpoint_sha256"),
        "checkpoint_epoch": record.get("epoch"),
        "global_step": record.get("global_step"),
        "config_fingerprint": record.get("config_fingerprint"),
        "dataset_fingerprint": record.get("dataset_fingerprint"),
        "split_fingerprint": record.get("split_fingerprint"),
        "taxonomy_fingerprint": record.get("class_mapping_fingerprint"),
    }
    if any(lock.get(key) != value for key, value in bindings.items()):
        _promotion_fail("OPERATING_POINT_LOCK_MISMATCH", "bindings do candidato divergentes")
    if record.get("model_contract_path") is not None:
        if lock.get("representation") != record.get("representation"):
            _promotion_fail("OPERATING_POINT_LOCK_MISMATCH", "variante RAW/EMA diverge do ONNX")
        validation_metrics = lock.get("validation_metrics")
        if not isinstance(validation_metrics, dict) or any(
            validation_metrics.get(key) != expected
            for key, expected in {
                "checkpoint_sha256": lock.get("checkpoint_sha256"),
                "representation": lock.get("representation"),
                "manifest_sha256": (lock.get("validation_manifest") or {}).get("sha256"),
                "score_threshold": lock.get("confidence_threshold"),
                "evaluation_nms_threshold": lock.get("nms_threshold"),
            }.items()
        ):
            _promotion_fail("OPERATING_POINT_LOCK_MISMATCH", "métricas não correspondem ao lock")
    for key in ("contract_path", "validation_manifest", "frozen_test_manifest"):
        value = lock.get(key)
        path_value = (
            value
            if isinstance(value, str)
            else value.get("path")
            if isinstance(value, dict)
            else None
        )
        if not isinstance(path_value, str):
            _promotion_fail("OPERATING_POINT_LOCK_INVALID", f"{key} ausente")
        path = (PROJECT_ROOT / path_value).resolve()
        try:
            path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            _promotion_fail("OPERATING_POINT_LOCK_INVALID", f"{key} fora do projeto")
        expected = (
            lock.get("contract_sha256")
            if key == "contract_path"
            else value.get("sha256")
            if isinstance(value, dict)
            else None
        )
        if not path.is_file() or sha256_file(path) != expected:
            _promotion_fail("OPERATING_POINT_LOCK_MISMATCH", f"{key} mudou após o lock")
    quality = lock.get("quality_contract")
    if not isinstance(quality, dict) or quality.get("source_role") != "VALIDATION":
        _promotion_fail("QUALITY_GATE_INVALID", "quality contract ausente ou contaminado")
    if record.get("model_contract_path") is not None:
        quality_path_value = quality.get("quality_contract_path")
        if not isinstance(quality_path_value, str):
            _promotion_fail("QUALITY_GATE_INVALID", "path do quality contract ausente")
        quality_path = (PROJECT_ROOT / quality_path_value).resolve()
        try:
            quality_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            _promotion_fail("QUALITY_GATE_INVALID", "quality contract fora do projeto")
        if not quality_path.is_file() or sha256_file(quality_path) != quality.get(
            "quality_contract_sha256"
        ):
            _promotion_fail("QUALITY_GATE_INVALID", "quality contract mudou após o lock")
        expected_quality = evaluate_quality_contract(
            lock.get("validation_metrics", {}),
            json.loads(quality_path.read_text(encoding="utf-8")),
            contract_path=quality_path,
        )
        if quality != expected_quality:
            _promotion_fail("QUALITY_GATE_INVALID", "decisão de qualidade diverge da validação")
    if require_approved and (
        quality.get("decision") != "APPROVED" or quality.get("passed") is not True
    ):
        _promotion_fail("QUALITY_GATE_REJECTED", "candidato não foi aprovado em VALIDATION")


def open_frozen_test_once(ledger_path: Path, lock: dict[str, Any]) -> dict[str, Any]:
    """Registra a abertura antes do primeiro byte de TEST; colisão sempre bloqueia."""
    entry = {
        "schema_version": FROZEN_TEST_LEDGER_VERSION,
        "state": "OPENED",
        "opened_at": datetime.now(UTC).isoformat(),
        "operating_point_lock_sha256": lock["operating_point_lock_sha256"],
        "frozen_test_manifest_sha256": lock["frozen_test_manifest"]["sha256"],
        "checkpoint_sha256": lock["checkpoint_sha256"],
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        _promotion_fail("FROZEN_TEST_ALREADY_OPENED", "ledger já existe para este protocolo")
        raise AssertionError from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(entry, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def frozen_test_ledger_path(lock: dict[str, Any]) -> Path:
    """Uma única abertura por manifest congelado, mesmo com outro lock."""
    return SERVING_DIR / ("frozen-test-ledger-" + lock["frozen_test_manifest"]["sha256"] + ".json")


def claim_frozen_test_evaluation_once(ledger_path: Path, lock: dict[str, Any]) -> Path:
    """Consume the opened protocol before loading TEST; a crash remains fail-closed."""
    result_path = SERVING_DIR / (
        "frozen-test-result-" + lock["operating_point_lock_sha256"][:12] + ".json"
    )
    claim_path = ledger_path.with_suffix(".evaluation-claim.json")
    if result_path.exists():
        _promotion_fail("FROZEN_TEST_ALREADY_OPENED", "resultado de TEST já existe")
    try:
        descriptor = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        _promotion_fail("FROZEN_TEST_ALREADY_OPENED", "avaliação de TEST já iniciada")
        raise AssertionError from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "state": "EVALUATION_CLAIMED",
                "claimed_at": datetime.now(UTC).isoformat(),
                "operating_point_lock_sha256": lock["operating_point_lock_sha256"],
                "frozen_test_manifest_sha256": lock["frozen_test_manifest"]["sha256"],
            },
            handle,
            indent=2,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return claim_path


def build_model_metadata_reconciliation_audit(
    model: Any,
    dataset: Any,
    closure: dict[str, Any],
    *,
    closure_reference: dict[str, Any],
    closure_file_sha256: str,
    checkpoint_file_sha256: str,
    onnx_file_sha256: str,
    previous_nms: float,
    expected_previous_nms: float,
    reason: str,
) -> dict[str, Any]:
    """Valida o estado científico imutável e descreve uma regularização posterior."""
    from app.ml.training import canonical_sha256

    if reason != MODEL_METADATA_RECONCILIATION_REASON:
        raise ValueError("motivo canônico da reconciliação é obrigatório")
    if isinstance(previous_nms, bool) or not isinstance(previous_nms, (int, float)):
        raise TypeError("NMS anterior inválido")
    if float(previous_nms) != float(expected_previous_nms):
        raise ValueError("NMS anterior diverge da origem documentada da inconsistência")
    if closure.get("schema_version") != CLOSURE_SCHEMA_VERSION or closure.get("producer") != {
        "name": CLOSURE_PRODUCER,
        "version": CLOSURE_PRODUCER_VERSION,
    }:
        raise ValueError("closure autoritativo inválido")
    closure_manifest_sha256 = closure.get("closure_manifest_sha256")
    if (
        not _is_sha256(closure_manifest_sha256)
        or closure_manifest_sha256 != _canonical_artifact_hash(closure)
        or closure_reference.get("sha256") != closure_file_sha256
    ):
        raise ValueError("closure autoritativo diverge da referência registrada")

    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    serving = metrics.get("serving")
    operating = closure.get("operating_point")
    checkpoint = closure.get("checkpoint")
    taxonomy = closure.get("taxonomy")
    closure_dataset = closure.get("dataset")
    if not isinstance(serving, dict):
        raise TypeError("ModelVersion sem binding de serving")
    if not isinstance(operating, dict):
        raise TypeError("closure sem operating point")
    if not isinstance(checkpoint, dict):
        raise TypeError("closure sem checkpoint")
    if not isinstance(taxonomy, dict):
        raise TypeError("closure sem taxonomia")
    if not isinstance(closure_dataset, dict):
        raise TypeError("ModelVersion/closure sem bindings obrigatórios")
    if model.promoted_at is not None:
        raise ValueError(
            "reconciliação administrativa não pode promover ou alterar modelo promovido"
        )
    if (
        model.checksum != onnx_file_sha256
        or serving.get("checkpoint_sha256") != checkpoint_file_sha256
    ):
        raise ValueError("artifact científico diverge do ModelVersion/closure")
    if checkpoint.get("sha256") != checkpoint_file_sha256:
        raise ValueError("checkpoint diverge do closure autoritativo")
    if model.dataset_version_id != dataset.id:
        raise ValueError("DatasetVersion do modelo diverge")
    split = dataset.split if isinstance(dataset.split, dict) else {}
    if (
        split.get("rdd_split_sha256") != closure_dataset.get("version")
        or metrics.get("fingerprints", {}).get("dataset_fingerprint")
        != closure_dataset.get("fingerprint")
        or metrics.get("fingerprints", {}).get("split_fingerprint")
        != closure_dataset.get("version")
    ):
        raise ValueError("dataset/fingerprint diverge do closure autoritativo")
    expected_taxonomy = {
        "schema_version": taxonomy.get("version"),
        "class_names": taxonomy.get("class_names"),
        "canonical_class_names": taxonomy.get("canonical_class_names"),
    }
    if (
        metrics.get("taxonomy") != expected_taxonomy
        or list(dataset.classes) != taxonomy.get("canonical_class_names")
        or metrics.get("fingerprints", {}).get("class_mapping_fingerprint")
        != taxonomy.get("fingerprint")
    ):
        raise ValueError("taxonomia diverge do closure autoritativo")
    stored_validation = metrics.get("validation")
    closure_validation = closure.get("selection_evidence", {}).get("metrics")

    def matches_with_derived_f1(stored: Any, authoritative: Any) -> bool:
        if isinstance(stored, dict) and isinstance(authoritative, dict):
            if set(stored) - set(authoritative):
                return False
            if any(key != "f1" for key in set(authoritative) - set(stored)):
                return False
            return all(
                matches_with_derived_f1(value, authoritative[key]) for key, value in stored.items()
            )
        return stored == authoritative

    validation_matches = matches_with_derived_f1(stored_validation, closure_validation)
    if (
        metrics.get("checkpoint_selection") != closure.get("selection_evidence")
        or metrics.get("operating_point") != operating
        or metrics.get("final_test") != closure.get("final_test_report")
        or not validation_matches
    ):
        raise ValueError("métricas/evidências científicas divergem do closure")
    if metrics.get("closure_artifact") != closure_reference:
        raise ValueError("referência do closure diverge do ModelVersion")
    benchmark = metrics.get("benchmark")
    if not isinstance(benchmark, dict) or benchmark.get("onnx_sha256") != onnx_file_sha256:
        raise ValueError("benchmark pertence a outro ONNX")

    authoritative_confidence = operating.get("confidence_threshold")
    authoritative_nms = operating.get("nms_threshold")
    if isinstance(authoritative_confidence, bool) or not isinstance(
        authoritative_confidence, (int, float)
    ):
        raise TypeError("operating point autoritativo inválido")
    if isinstance(authoritative_nms, bool) or not isinstance(authoritative_nms, (int, float)):
        raise TypeError("operating point autoritativo inválido")
    if (
        serving.get("score_threshold") != authoritative_confidence
        or serving.get("nms_threshold") != authoritative_nms
    ):
        raise ValueError("metadata atual não coincide com o operating point autoritativo")
    if float(previous_nms) == float(authoritative_nms):
        raise ValueError("reconciliação exige valor anterior diferente do valor autoritativo")

    scientific_bindings = {
        "checkpoint_sha256": checkpoint_file_sha256,
        "onnx_sha256": onnx_file_sha256,
        "dataset_version_id": str(dataset.id),
        "dataset_fingerprint": closure_dataset["fingerprint"],
        "taxonomy_fingerprint": taxonomy["fingerprint"],
        "confidence_threshold": authoritative_confidence,
        "closure_manifest_sha256": closure_manifest_sha256,
        "closure_file_sha256": closure_file_sha256,
        "test_manifest_sha256": closure_dataset["manifests"]["TEST"]["sha256"],
        "validation_manifest_sha256": closure_dataset["manifests"]["VALIDATION"]["sha256"],
    }
    before = {
        "serving": {"nms_threshold": float(previous_nms)},
        "scientific_bindings": scientific_bindings,
    }
    after = {
        "serving": {"nms_threshold": float(authoritative_nms)},
        "scientific_bindings": scientific_bindings,
        "reconciliation": {
            "reason": reason,
            "source": "canonical_model_closure",
            "closure_path": closure_reference.get("path"),
            "recorded_after_correction": True,
        },
    }
    event = {
        "operation": MODEL_METADATA_RECONCILIATION_OPERATION,
        "entity_type": "ModelVersion",
        "entity_id": str(model.id),
        "actor": MODEL_METADATA_RECONCILIATION_ACTOR,
        "before": before,
        "after": after,
    }
    return {**event, "event_hash": canonical_sha256(event)}


def load_checkpoint_model(
    checkpoint: Path,
    *,
    contract_path: Path = MODEL_METADATA_PATH,
    representation: str = "raw",
) -> tuple[Any, dict[str, Any]]:
    """YOLOX-s com os pesos da representação declarada, em modo avaliação.

    V1 fechou com `model_state_dict` (raw), porque o trainer V1 só media raw.
    O V2 mede raw e EMA em VALIDATION e congela uma política; quem avalia,
    seleciona e exporta precisa pedir a mesma representação.
    """
    import torch

    from app.ml.yolox_model import instantiate_model

    if representation not in {"raw", "ema"}:
        raise ValueError("representation deve ser raw ou ema")
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    metadata = load_model_config(contract_path)
    expected_checkpoint_contract = {
        "model_id": metadata["model_id"],
        "architecture": metadata["architecture"],
        "num_classes": metadata["num_classes"],
        "class_order": list(metadata["class_names"]),
    }
    if any(payload.get(key) != value for key, value in expected_checkpoint_contract.items()):
        raise ValueError("checkpoint diverge do contrato MODEL")
    model = instantiate_model(config_path=contract_path)
    state = payload["model_state_dict" if representation == "raw" else "ema_state_dict"]
    if state is None:
        raise ValueError(f"checkpoint sem pesos {representation}")
    model.load_state_dict(state, strict=True)
    model.eval()
    model.head.decode_in_inference = True
    info = {
        "epoch": payload.get("epoch"),
        "best_metric": payload.get("best_metric"),
        "checkpoint_sha256": sha256_file(checkpoint),
        "weights": "model_state_dict" if representation == "raw" else "ema_state_dict",
        "representation": representation,
        "global_step": payload.get("global_step"),
        "training_metadata": payload.get("training_metadata"),
        **expected_checkpoint_contract,
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


def export_onnx(
    checkpoint: Path,
    destination: Path,
    *,
    contract_path: Path = MODEL_METADATA_PATH,
    representation: str = "raw",
) -> dict[str, Any]:
    import torch

    contract_path = contract_path.resolve()
    model, info = load_checkpoint_model(
        checkpoint, contract_path=contract_path, representation=representation
    )
    metadata = load_model_config(contract_path)
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
        "model_contract_sha256": sha256_file(contract_path),
        "model_contract_path": contract_path.relative_to(PROJECT_ROOT).as_posix(),
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

    def __init__(
        self,
        onnx_path: Path,
        expected_sha256: str,
        *,
        nms_threshold: float | None = None,
        expected_input_size: tuple[int, int] | None = None,
        expected_class_names: tuple[str, ...] | None = None,
        contract_path: Path = MODEL_METADATA_PATH,
        expected_contract_sha256: str | None = None,
    ) -> None:
        if not onnx_path.is_file():
            raise ModelNotAvailableError(f"ONNX ausente: {onnx_path.name}")
        if sha256_file(onnx_path) != expected_sha256:
            raise ModelNotAvailableError("ONNX diverge do checksum registrado")
        import onnxruntime as ort  # type: ignore[import-untyped]

        contract_path = contract_path.resolve()
        try:
            contract_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise ModelNotAvailableError("contrato do modelo fora do projeto") from exc
        if (
            expected_contract_sha256 is not None
            and sha256_file(contract_path) != expected_contract_sha256
        ):
            raise ModelNotAvailableError("contrato do modelo diverge do checksum registrado")
        metadata = load_model_config(contract_path)
        self.class_names: list[str] = list(metadata["canonical_class_names"])
        self.input_size: tuple[int, int] = tuple(metadata["test_size"])
        if expected_class_names is not None and tuple(self.class_names) != expected_class_names:
            raise ModelNotAvailableError("classes do ModelVersion divergem do contrato")
        if expected_input_size is not None and self.input_size != expected_input_size:
            raise ModelNotAvailableError("input_size do ModelVersion diverge do contrato")
        configured_nms = (
            float(metadata["evaluation"]["nms_threshold"])
            if nms_threshold is None
            else float(nms_threshold)
        )
        if not 0.0 < configured_nms <= 1.0:
            raise ModelNotAvailableError("NMS threshold do ModelVersion é inválido")
        self.nms_threshold = configured_nms
        self.session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.provider = self.session.get_providers()[0]

    def raw(self, image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        tensor, ratio = self.preprocess(image_bgr)
        output = self.infer(tensor)
        return output, ratio

    @staticmethod
    def decode(image_bytes: bytes) -> np.ndarray:
        import cv2

        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("imagem não decodificável")
        return image

    def preprocess(self, image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        return _preprocess(image_bgr, self.input_size)

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        (output,) = self.session.run(None, {"images": tensor})
        return output

    def postprocess(
        self,
        output: np.ndarray,
        ratio: float,
        image_shape: tuple[int, ...],
        *,
        score_threshold: float,
    ) -> list[ServedDetection]:
        from yolox.utils.demo_utils import multiclass_nms  # type: ignore[import-not-found]

        height, width = image_shape[:2]
        predictions = output[0]
        boxes = predictions[:, :4].copy()
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
        xyxy /= ratio
        scores = predictions[:, 4:5] * predictions[:, 5:]
        kept = multiclass_nms(
            xyxy,
            scores,
            nms_thr=self.nms_threshold,
            score_thr=score_threshold,
        )
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

    def detect(
        self, image_bytes: bytes, *, score_threshold: float = SERVING_SCORE_THRESHOLD
    ) -> list[ServedDetection]:
        image = self.decode(image_bytes)
        tensor, ratio = self.preprocess(image)
        output = self.infer(tensor)
        return self.postprocess(
            output,
            ratio,
            image.shape,
            score_threshold=score_threshold,
        )


def parity_check(
    checkpoint: Path,
    onnx_path: Path,
    image_paths: list[Path],
    *,
    contract_path: Path = MODEL_METADATA_PATH,
    representation: str = "raw",
) -> dict[str, Any]:
    """Máxima diferença absoluta PyTorch × ONNX na saída decodificada, em imagens reais."""
    import cv2
    import torch

    metadata = load_model_config(contract_path)
    model, _ = load_checkpoint_model(
        checkpoint, contract_path=contract_path, representation=representation
    )
    detector = OnnxDetector(
        onnx_path,
        sha256_file(onnx_path),
        contract_path=contract_path,
        expected_input_size=tuple(metadata["test_size"]),
        expected_class_names=tuple(metadata["canonical_class_names"]),
    )
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
            "select r.run_uuid, r.start_time, r.end_time, r.status "
            "from runs r join params p on p.run_uuid = r.run_uuid "
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
        epoch = connection.execute(
            "select value from latest_metrics where run_uuid = ? and key = 'train/epoch'",
            (row[0],),
        ).fetchone()
    finally:
        connection.close()
    return {
        "mlflow_run_id": row[0],
        "started_at_ms": row[1],
        "ended_at_ms": row[2],
        "status": row[3],
        "latest_train_epoch": epoch[0] if epoch is not None else None,
        "params": params,
    }


def training_contract_complete(run: dict[str, Any], *, max_epoch: int) -> bool:
    latest = run.get("latest_train_epoch")
    return (
        run.get("status") == "FINISHED"
        and isinstance(latest, (int, float))
        and not isinstance(latest, bool)
        and math.isfinite(float(latest))
        and int(latest) == float(latest)
        and int(latest) + 1 == max_epoch
    )


def validation_metrics_from_training_run(
    config_fingerprint: str,
    global_step: int,
    best_metric: float | None,
    *,
    representation: str = "raw",
    selection_representation: str = "raw",
) -> dict[str, Any]:
    """Métrica do evaluator oficial sobre a VALIDATION inteira, no passo exato do checkpoint.

    Não reavalia (o treino continua na mesma GPU); lê o que o próprio trainer mediu e
    exige que o mAP50-95 bata com o `best_metric` gravado dentro do checkpoint.
    """
    import sqlite3

    database = PROJECT_ROOT / "mlruns" / "mlflow.db"
    if representation not in {"raw", "ema"} or selection_representation not in {"raw", "ema"}:
        raise ValueError("representação de pesos inválida")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        prefixes = {
            "raw": "validation/",
            "ema": "validation_ema/",
        }
        rows = connection.execute(
            "select m.run_uuid, m.key, m.value from metrics m "
            "join params p on p.run_uuid = m.run_uuid and p.key = 'config_fingerprint' "
            "where p.value = ? and m.step = ? and (m.key like ? or m.key like ?)",
            (config_fingerprint, global_step, "validation/%", "validation_ema/%"),
        ).fetchall()
    finally:
        connection.close()
    runs = {row[0] for row in rows}
    if len(runs) != 1:
        raise ValueError(
            f"validação do passo {global_step} não encontrada de forma única no MLflow"
        )
    by_representation = {
        variant: {
            key.removeprefix(prefix): value for _, key, value in rows if key.startswith(prefix)
        }
        for variant, prefix in prefixes.items()
    }
    selected = by_representation[selection_representation]
    if best_metric is None or abs(selected.get("map50_95", float("nan")) - best_metric) > 1e-6:
        raise ValueError("mAP50-95 do MLflow diverge do best_metric gravado no checkpoint")
    values = by_representation[representation]
    if not values or values.get("map50_95") is None:
        raise ValueError(f"métricas {representation} ausentes no passo do checkpoint")
    per_class: dict[str, dict[str, float]] = {}
    for key, value in values.items():
        if "/" in key:
            label, metric = key.split("/", 1)
            per_class.setdefault(label, {})[metric] = value
    return {
        "source": "training_run_validation (evaluator oficial, VALIDATION completa)",
        "mlflow_run_id": runs.pop(),
        "global_step": global_step,
        "representation": representation,
        "precision": values.get("precision"),
        "recall": values.get("recall"),
        "map50": values.get("map50"),
        "map50_95": values.get("map50_95"),
        "per_class": per_class,
    }


def validation_images(limit: int, *, manifest_path: Path | None = None) -> list[Path]:
    from app.ml.detection_dataset import load_authorized_manifest
    from app.ml.evaluator import manifest_for_evaluation

    rows = load_authorized_manifest(
        manifest_path or manifest_for_evaluation("VALIDATION"), intended_split="VALIDATION"
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


def _timing_summary(samples: list[float]) -> dict[str, float | int]:
    """Resumo determinístico em ms; rejeita medições que não podem ser comparadas."""
    import statistics

    if not samples or any(not math.isfinite(value) or value < 0 for value in samples):
        raise ValueError("amostras de benchmark devem ser finitas, não negativas e não vazias")
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "count": len(samples),
        "mean": round(statistics.fmean(samples), 3),
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
    }


def validate_benchmark_report(
    report: dict[str, Any],
    *,
    expected_sha256: str,
    expected_confidence: float,
    expected_nms: float,
) -> None:
    """Falha fechado para impedir atribuição de telemetria a outro modelo/operating point."""
    if report.get("schema_version") != 2 or report.get("benchmark_data_role") != "VALIDATION":
        raise ValueError("schema/role do benchmark inválido")
    if report.get("onnx_sha256") != expected_sha256:
        raise ValueError("benchmark diverge do SHA256 do ONNX")
    if not isinstance(report.get("model_version"), str) or not report["model_version"].strip():
        raise ValueError("benchmark sem vínculo ao ModelVersion")
    operating = report.get("operating_point")
    if not isinstance(operating, dict) or (
        operating.get("confidence_threshold") != expected_confidence
        or operating.get("nms_threshold") != expected_nms
    ):
        raise ValueError("benchmark diverge do operating point registrado")
    runs = report.get("measured_iterations")
    stages = report.get("stages_ms")
    if (
        isinstance(runs, bool)
        or not isinstance(runs, int)
        or runs <= 0
        or not isinstance(stages, dict)
    ):
        raise ValueError("quantidade de iterações/estágios do benchmark inválida")
    for stage in ("decode", "preprocess", "inference", "postprocess", "pipeline"):
        summary = stages.get(stage)
        if not isinstance(summary, dict) or summary.get("count") != runs:
            raise ValueError(f"estágio {stage} incompleto")
        values = [summary.get(key) for key in ("mean", "p50", "p95", "min", "max")]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for value in values
        ):
            raise ValueError(f"estágio {stage} contém latência inválida")
        if not summary["min"] <= summary["p50"] <= summary["p95"] <= summary["max"]:
            raise ValueError(f"percentis do estágio {stage} são inconsistentes")
    component_mean = sum(
        float(stages[stage]["mean"])
        for stage in ("decode", "preprocess", "inference", "postprocess")
    )
    if not math.isclose(component_mean, float(stages["pipeline"]["mean"]), abs_tol=0.01):
        raise ValueError("pipeline total diverge da soma dos estágios")
    memory = report.get("memory")
    if not isinstance(memory, dict):
        raise TypeError("telemetria de memória ausente")
    rss_values = [
        memory.get("baseline_process_rss_mb"),
        memory.get("process_rss_mb"),
        memory.get("observed_peak_process_rss_mb"),
    ]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        for value in rss_values
    ):
        raise ValueError("telemetria RSS inválida")
    if memory["observed_peak_process_rss_mb"] < max(
        memory["baseline_process_rss_mb"], memory["process_rss_mb"]
    ):
        raise ValueError("pico RSS observado é menor que baseline/final")
    if report.get("execution_provider") == "CPUExecutionProvider" and (
        memory.get("vram_mb") is not None or memory.get("vram_status") != "NOT_APPLICABLE"
    ):
        raise ValueError("CPUExecutionProvider deve declarar VRAM como NOT_APPLICABLE")


def benchmark_onnx(
    onnx_path: Path,
    checksum: str,
    *,
    model_version: str | None = None,
    confidence_threshold: float = SERVING_SCORE_THRESHOLD,
    nms_threshold: float | None = None,
    expected_input_size: tuple[int, int] | None = None,
    expected_class_names: tuple[str, ...] | None = None,
    images: int = 30,
    warmup: int = 10,
    runs: int = 200,
    validation_manifest_path: Path | None = None,
    contract_path: Path = MODEL_METADATA_PATH,
) -> dict[str, Any]:
    """Mede o pipeline ONNX real por estágio usando somente imagens de VALIDATION."""
    import os
    import platform
    import time

    import onnxruntime as ort  # type: ignore[import-untyped]
    import psutil  # type: ignore[import-untyped]

    if images <= 0 or warmup < 0 or runs <= 0:
        raise ValueError("images/runs devem ser positivos e warmup não pode ser negativo")
    paths = (
        validation_images(images)
        if validation_manifest_path is None
        else validation_images(images, manifest_path=validation_manifest_path)
    )
    if not paths:
        raise ValueError("VALIDATION sem imagens para benchmark")
    payloads = [path.read_bytes() for path in paths]
    load_started = time.perf_counter()
    detector = OnnxDetector(
        onnx_path,
        checksum,
        contract_path=contract_path,
        nms_threshold=nms_threshold,
        expected_input_size=expected_input_size,
        expected_class_names=expected_class_names,
    )
    model_load_ms = (time.perf_counter() - load_started) * 1000
    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    for index in range(warmup):
        detector.detect(
            payloads[index % len(payloads)],
            score_threshold=confidence_threshold,
        )
    stage_samples: dict[str, list[float]] = {
        "decode": [],
        "preprocess": [],
        "inference": [],
        "postprocess": [],
        "pipeline": [],
    }
    observed_rss = [baseline_rss]
    for index in range(runs):
        started = time.perf_counter()
        image = detector.decode(payloads[index % len(payloads)])
        decoded = time.perf_counter()
        tensor, ratio = detector.preprocess(image)
        preprocessed = time.perf_counter()
        output = detector.infer(tensor)
        inferred = time.perf_counter()
        detector.postprocess(
            output,
            ratio,
            image.shape,
            score_threshold=confidence_threshold,
        )
        finished = time.perf_counter()
        stage_samples["decode"].append((decoded - started) * 1000)
        stage_samples["preprocess"].append((preprocessed - decoded) * 1000)
        stage_samples["inference"].append((inferred - preprocessed) * 1000)
        stage_samples["postprocess"].append((finished - inferred) * 1000)
        stage_samples["pipeline"].append((finished - started) * 1000)
        observed_rss.append(process.memory_info().rss)
    stages = {name: _timing_summary(values) for name, values in stage_samples.items()}
    final_rss = process.memory_info().rss
    observed_rss.append(final_rss)
    memory_divisor = 1024 * 1024
    report = {
        "schema_version": 2,
        "scope": "decode + preprocess + ONNX Runtime inference + postprocess; sem Storage/rede/fila/DB/EventContext/frontend",
        "benchmark_data_role": "VALIDATION",
        "validation_manifest_sha256": (
            sha256_file(validation_manifest_path) if validation_manifest_path is not None else None
        ),
        "model_version": model_version,
        "onnx_sha256": checksum,
        "model_contract_sha256": sha256_file(contract_path),
        "taxonomy": list(detector.class_names),
        "operating_point": {
            "confidence_threshold": confidence_threshold,
            "nms_threshold": detector.nms_threshold,
        },
        "model_load_ms": round(model_load_ms, 3),
        "warmup_iterations": warmup,
        "measured_iterations": runs,
        "stages_ms": stages,
        "pipeline_fps_equivalent": round(1000 / float(stages["pipeline"]["mean"]), 2),
        "memory": {
            "baseline_process_rss_mb": round(baseline_rss / memory_divisor, 2),
            "process_rss_mb": round(final_rss / memory_divisor, 2),
            "observed_peak_process_rss_mb": round(max(observed_rss) / memory_divisor, 2),
            "vram_mb": None,
            "vram_status": (
                "NOT_APPLICABLE" if detector.provider == "CPUExecutionProvider" else "NOT_MEASURED"
            ),
        },
        "hardware": {
            "cpu": platform.processor() or platform.machine(),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count(),
            "total_ram_mb": round(psutil.virtual_memory().total / memory_divisor, 2),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "onnxruntime": ort.__version__,
        "execution_provider": detector.provider,
        "input_size": list(detector.input_size),
        "real_images": len(payloads),
        "warmup_runs": warmup,
        "timed_runs": runs,
        # Compatibilidade com artifacts/consumidores schema v1: latency_ms sempre foi inference.
        "latency_ms": stages["inference"],
        "fps_approx": round(1000 / float(stages["inference"]["mean"]), 2),
        "decode_preprocess_infer_postprocess_ms": {
            "mean": stages["pipeline"]["mean"],
            "images": runs,
        },
    }
    validate_benchmark_report(
        report,
        expected_sha256=checksum,
        expected_confidence=confidence_threshold,
        expected_nms=detector.nms_threshold,
    )
    return report


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


def _as_completed_epochs(run: dict[str, Any]) -> int | None:
    latest = run.get("latest_train_epoch")
    if (
        not isinstance(latest, (int, float))
        or isinstance(latest, bool)
        or not math.isfinite(float(latest))
        or int(latest) != float(latest)
    ):
        return None
    return int(latest) + 1


def _parse_timestamp(value: Any, *, code: str) -> None:
    if not isinstance(value, str):
        _promotion_fail(code, "timestamp ausente")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _promotion_fail(code, "timestamp inválido")
    if parsed.tzinfo is None:
        _promotion_fail(code, "timestamp sem timezone")


def _validated_metrics(metrics: Any, *, code: str) -> dict[str, Any]:
    metadata = load_model_config()
    if not isinstance(metrics, dict):
        _promotion_fail(code, "metrics deve ser objeto")
    required = ("precision", "recall", "f1", "map50", "map50_95")
    for key in required:
        value = metrics.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            _promotion_fail(code, f"métrica agregada {key} inválida")
    per_class = metrics.get("per_class")
    expected_classes = list(metadata["class_names"])
    if not isinstance(per_class, dict) or list(per_class) != expected_classes:
        _promotion_fail("TAXONOMY_MISMATCH", "classes de métricas divergentes")
    class_required = ("precision", "recall", "f1", "ap50", "ap50_95")
    for label in expected_classes:
        values = per_class[label]
        if not isinstance(values, dict):
            _promotion_fail(code, f"métricas de {label} inválidas")
        for key in class_required:
            value = values.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                _promotion_fail(code, f"métrica {label}/{key} inválida")
        for key in ("support", "true_positives", "false_positives", "false_negatives"):
            if key in values and (
                isinstance(values[key], bool) or not isinstance(values[key], int) or values[key] < 0
            ):
                _promotion_fail(code, f"contagem {label}/{key} inválida")
    return metrics


def _with_f1(metrics: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(metrics))

    def fill(values: dict[str, Any]) -> None:
        precision = values.get("precision")
        recall = values.get("recall")
        if (
            "f1" not in values
            and isinstance(precision, (int, float))
            and isinstance(recall, (int, float))
        ):
            values["f1"] = (
                0.0
                if precision + recall == 0
                else round(2 * precision * recall / (precision + recall), 6)
            )

    fill(result)
    for values in result.get("per_class", {}).values():
        if isinstance(values, dict):
            fill(values)
    return result


def _manifest_evidence(role: str, path: Path | None = None) -> dict[str, Any]:
    from app.ml.evaluator import manifest_for_evaluation

    if role not in {"VALIDATION", "TEST"}:
        raise ValueError("evidence role inválido")
    path = path or manifest_for_evaluation(role, allow_test=role == "TEST")
    if path == manifest_for_evaluation(role, allow_test=role == "TEST"):
        readiness = json.loads(READINESS_REPORT_PATH.read_text(encoding="utf-8"))
        counts = readiness["counts"][role]
    else:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        counts = {
            "records": len(rows),
            "boxes": sum(len(row.get("boxes", [])) for row in rows),
        }
    return {
        "role": role,
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": sha256_file(path),
        "sample_count": counts["records"],
        "annotation_count": counts["boxes"],
    }


def _authoritative_contract(
    run: dict[str, Any], *, contract_path: Path = MODEL_METADATA_PATH
) -> dict[str, Any]:
    from app.ml.training import TrainingConfig, checkpoint_fingerprints

    contract_path = contract_path.resolve()
    metadata = load_model_config(contract_path)
    config = TrainingConfig.from_model_metadata(metadata)
    batch_size = (run.get("params") or {}).get("batch_size")
    if batch_size is not None:
        try:
            config = config.with_overrides(batch_size=int(batch_size))
        except (TypeError, ValueError) as exc:
            _promotion_fail("RUN_CONTRACT_MISMATCH", "batch_size do run é inválido")
            raise AssertionError from exc
    return {
        "path": contract_path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": sha256_file(contract_path),
        "schema_version": metadata["schema_version"],
        "model_id": metadata["model_id"],
        "architecture": metadata["architecture"],
        "max_epoch": config.max_epoch,
        "input_size": list(config.input_size),
        "num_classes": config.num_classes,
        "class_names": list(metadata["class_names"]),
        "canonical_class_names": list(metadata["canonical_class_names"]),
        "fingerprints": checkpoint_fingerprints(metadata, config),
    }


def _validate_early_stop_approval(
    approval: Any,
    *,
    run_id: str,
    checkpoint_sha256: str,
    contract_sha256: str,
    completed_epochs: int,
) -> None:
    if not isinstance(approval, dict):
        _promotion_fail("TRAINING_NOT_COMPLETE", "early stop sem aprovação auditável")
    required = {
        "schema_version": 1,
        "decision": "EARLY_STOP_APPROVED",
        "training_run_id": run_id,
        "checkpoint_sha256": checkpoint_sha256,
        "training_contract_sha256": contract_sha256,
    }
    if any(approval.get(key) != value for key, value in required.items()):
        _promotion_fail("TRAINING_NOT_COMPLETE", "aprovação de early stop não corresponde")
    if not isinstance(approval.get("approved_by"), str) or not approval["approved_by"].strip():
        _promotion_fail("TRAINING_NOT_COMPLETE", "aprovador de early stop ausente")
    if not isinstance(approval.get("reason"), str) or not approval["reason"].strip():
        _promotion_fail("TRAINING_NOT_COMPLETE", "justificativa de early stop ausente")
    if approval.get("persisted_last_epoch") != completed_epochs or not _is_sha256(
        approval.get("persisted_last_checkpoint_sha256")
    ):
        _promotion_fail(
            "TRAINING_NOT_COMPLETE",
            "estado persistido do early stop ausente ou divergente",
        )
    _parse_timestamp(approval.get("approved_at"), code="TRAINING_NOT_COMPLETE")
    if approval.get("approval_sha256") != _canonical_artifact_hash(
        approval, hash_field="approval_sha256"
    ):
        _promotion_fail("TRAINING_NOT_COMPLETE", "hash da aprovação de early stop inválido")


def _validate_training_completion(
    closure: dict[str, Any], run: dict[str, Any], contract: dict[str, Any]
) -> None:
    training = closure.get("training")
    if not isinstance(training, dict):
        _promotion_fail("TRAINING_NOT_COMPLETE", "evidência de treino ausente")
    run_id = run.get("mlflow_run_id")
    if not isinstance(run_id, str) or training.get("training_run_id") != run_id:
        _promotion_fail("CHECKPOINT_RUN_MISMATCH", "run de treino divergente")
    if run.get("status") != "FINISHED":
        _promotion_fail("TRAINING_NOT_COMPLETE", "run de treino não finalizado")
    observed_epochs = _as_completed_epochs(run)
    reason = training.get("completion_reason")
    if reason == "MAX_EPOCH_REACHED":
        completed = training.get("completed_epochs")
        if completed is None or completed != observed_epochs:
            _promotion_fail("TRAINING_NOT_COMPLETE", "epoch final não corresponde ao MLflow")
        if completed != contract["max_epoch"]:
            _promotion_fail("TRAINING_NOT_COMPLETE", "epoch final diverge do max_epoch oficial")
    elif reason == "EARLY_STOP_APPROVED":
        completed = training.get("completed_epochs")
        if (
            isinstance(completed, bool)
            or not isinstance(completed, int)
            or completed <= 0
            or completed >= contract["max_epoch"]
            or observed_epochs is None
            or observed_epochs < completed
        ):
            _promotion_fail(
                "TRAINING_NOT_COMPLETE",
                "epoch persistida do early stop diverge do run",
            )
        _validate_early_stop_approval(
            training.get("early_stop_approval"),
            run_id=run_id,
            checkpoint_sha256=closure["checkpoint"]["sha256"],
            contract_sha256=contract["sha256"],
            completed_epochs=completed,
        )
    else:
        _promotion_fail("TRAINING_NOT_COMPLETE", "completion_reason não autoriza promoção")


def closure_run_for(run_id: str | None) -> dict[str, Any]:
    """Lê a prova MLflow criada pelo produtor canônico, sem alterar o tracking store."""
    import sqlite3

    database = PROJECT_ROOT / "mlruns" / "mlflow.db"
    if not run_id or not database.is_file():
        return {}
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "select run_uuid, status, artifact_uri from runs where run_uuid = ?", (run_id,)
        ).fetchone()
        if row is None:
            return {}
        params = dict(
            connection.execute(
                "select key, value from params where run_uuid = ?", (run_id,)
            ).fetchall()
        )
    finally:
        connection.close()
    artifact_hash = params.get("closure_manifest_sha256")
    artifact_closure_hash = None
    if (
        isinstance(artifact_hash, str)
        and _is_sha256(artifact_hash)
        and isinstance(row[2], str)
        and row[2].startswith("file:")
    ):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(row[2])
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        artifact = Path(raw_path) / f"model-closure-{artifact_hash[:12]}.json"
        if artifact.is_file():
            try:
                artifact_document = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                artifact_document = None
            if isinstance(artifact_document, dict):
                artifact_closure_hash = _canonical_artifact_hash(artifact_document)
    return {
        "mlflow_run_id": row[0],
        "status": row[1],
        "params": params,
        "artifact_closure_sha256": artifact_closure_hash,
    }


def _validate_candidate_bindings(
    record: dict[str, Any],
    training_run: dict[str, Any],
    contract: dict[str, Any],
    *,
    checkpoint_info: dict[str, Any] | None = None,
) -> None:
    if record.get("model_contract_sha256") != contract["sha256"]:
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "hash do contrato candidato diverge")
    manifest_max_epoch = (record.get("training") or {}).get("contract_max_epoch")
    if manifest_max_epoch != contract["max_epoch"]:
        _promotion_fail("MANIFEST_CONTRACT_MISMATCH", "contract_max_epoch não é o oficial")
    run_max_epoch = (training_run.get("params") or {}).get("max_epoch")
    if run_max_epoch is not None:
        try:
            run_max_epoch_value = int(run_max_epoch)
        except (TypeError, ValueError):
            _promotion_fail("RUN_CONTRACT_MISMATCH", "max_epoch do run é inválido")
        if (
            str(run_max_epoch_value) != str(run_max_epoch)
            or run_max_epoch_value != contract["max_epoch"]
        ):
            _promotion_fail("RUN_CONTRACT_MISMATCH", "max_epoch do run diverge do contrato")
    if (
        record.get("input_size") != contract["input_size"]
        or record.get("class_names") != contract["canonical_class_names"]
    ):
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "interface do candidato diverge")
    params = training_run.get("params") or {}
    if (
        params.get("model_id") != contract["model_id"]
        or params.get("architecture") != contract["architecture"]
    ):
        _promotion_fail("RUN_CONTRACT_MISMATCH", "modelo do run diverge do contrato")
    if params.get("class_order") != ",".join(contract["class_names"]):
        _promotion_fail("TAXONOMY_MISMATCH", "taxonomia do run diverge")
    if params.get("input_size") != "x".join(str(value) for value in contract["input_size"]):
        _promotion_fail("RUN_CONTRACT_MISMATCH", "input_size do run diverge")
    for key, expected in contract["fingerprints"].items():
        if record.get(key) != expected:
            _promotion_fail("RUN_CONTRACT_MISMATCH", f"{key} diverge do contrato")
        if params.get(key) != expected:
            _promotion_fail("CHECKPOINT_RUN_MISMATCH", f"{key} do run diverge")
        if checkpoint_info is not None and checkpoint_info.get(key) != expected:
            _promotion_fail("CHECKPOINT_RUN_MISMATCH", f"{key} do checkpoint diverge")
    if checkpoint_info is not None:
        expected_checkpoint = {
            "checkpoint_sha256": record.get("checkpoint_sha256"),
            "epoch": record.get("epoch"),
            "global_step": record.get("global_step"),
            "model_id": contract["model_id"],
            "architecture": contract["architecture"],
            "num_classes": contract["num_classes"],
            "class_order": contract["class_names"],
        }
        if any(checkpoint_info.get(key) != value for key, value in expected_checkpoint.items()):
            _promotion_fail("CHECKPOINT_HASH_MISMATCH", "metadata do checkpoint diverge")


def _validate_closure_artifact(
    closure: Any,
    record: dict[str, Any],
    *,
    training_run: dict[str, Any],
    closure_run: dict[str, Any],
    contract_path: Path = MODEL_METADATA_PATH,
) -> None:
    if not isinstance(closure, dict):
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "closure artifact ausente")
    if closure.get("schema_version") != CLOSURE_SCHEMA_VERSION or closure.get("producer") != {
        "name": CLOSURE_PRODUCER,
        "version": CLOSURE_PRODUCER_VERSION,
    }:
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "schema/produtor incompatível")
    _parse_timestamp(closure.get("generated_at"), code="CLOSURE_MANIFEST_INVALID")
    closure_hash = closure.get("closure_manifest_sha256")
    if not _is_sha256(closure_hash) or closure_hash != _canonical_artifact_hash(closure):
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "hash determinístico inválido")

    contract = _authoritative_contract(training_run, contract_path=contract_path)
    closure_contract = closure.get("training_contract")
    if not isinstance(closure_contract, dict) or closure_contract != contract:
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "contrato oficial divergente")
    _validate_candidate_bindings(record, training_run, contract)

    checkpoint = closure.get("checkpoint")
    if not isinstance(checkpoint, dict):
        _promotion_fail("CHECKPOINT_HASH_MISMATCH", "checkpoint evidence ausente")
    checkpoint_fields = {
        "sha256": record.get("checkpoint_sha256"),
        "representation": record.get("representation"),
        "epoch": record.get("epoch"),
        "global_step": record.get("global_step"),
        "model_id": contract["model_id"],
        "architecture": contract["architecture"],
        "config_fingerprint": record.get("config_fingerprint"),
        "dataset_fingerprint": record.get("dataset_fingerprint"),
        "split_fingerprint": record.get("split_fingerprint"),
        "taxonomy_fingerprint": record.get("class_mapping_fingerprint"),
    }
    if any(checkpoint.get(key) != value for key, value in checkpoint_fields.items()):
        _promotion_fail("CHECKPOINT_HASH_MISMATCH", "checkpoint não corresponde ao candidato")
    if any(
        isinstance(checkpoint.get(key), bool)
        or not isinstance(checkpoint.get(key), int)
        or checkpoint[key] < 0
        for key in ("epoch", "global_step")
    ):
        _promotion_fail("CHECKPOINT_HASH_MISMATCH", "epoch/global_step inválidos")
    for key, expected in contract["fingerprints"].items():
        if checkpoint.get(key.replace("class_mapping", "taxonomy")) != expected:
            _promotion_fail("CHECKPOINT_RUN_MISMATCH", f"{key} do closure diverge")
    _validate_training_completion(closure, training_run, contract)

    dataset = closure.get("dataset")
    if (
        not isinstance(dataset, dict)
        or dataset.get("fingerprint") != record.get("dataset_fingerprint")
        or dataset.get("version") != record.get("split_fingerprint")
    ):
        _promotion_fail("TEST_DATASET_MISMATCH", "dataset do fechamento diverge")
    manifests = dataset.get("manifests") if isinstance(dataset, dict) else None
    if contract_path.resolve() == MODEL_METADATA_PATH.resolve():
        expected_validation = _manifest_evidence("VALIDATION")
        expected_test = _manifest_evidence("TEST")
    else:
        from app.ml.training import dataset_binding

        metadata = load_model_config(contract_path)
        binding = dataset_binding(metadata.get("dataset"))
        expected_validation = _manifest_evidence("VALIDATION", binding.validation_manifest)
        expected_test = _manifest_evidence(
            "TEST", PROJECT_ROOT / metadata["dataset"]["sealed_roles"]["FROZEN_INTERNAL_TEST_V2"]
        )
    if not isinstance(manifests, dict) or manifests.get("VALIDATION") != expected_validation:
        _promotion_fail("VALIDATION_MANIFEST_MISMATCH", "manifest VALIDATION divergente")
    if manifests.get("TEST") != expected_test:
        _promotion_fail("TEST_MANIFEST_MISMATCH", "manifest TEST divergente")
    if expected_validation["sha256"] == expected_test["sha256"]:
        _promotion_fail("TEST_MANIFEST_MISMATCH", "VALIDATION e TEST não são distintos")

    taxonomy = closure.get("taxonomy")
    expected_taxonomy = {
        "version": contract["schema_version"],
        "fingerprint": record.get("class_mapping_fingerprint"),
        "class_names": contract["class_names"],
        "canonical_class_names": contract["canonical_class_names"],
    }
    if taxonomy != expected_taxonomy:
        _promotion_fail("TAXONOMY_MISMATCH", "taxonomia do fechamento diverge")

    reports = (
        ("selection_evidence", "VALIDATION", expected_validation, "VALIDATION_EVIDENCE_MISSING"),
        ("operating_point", "VALIDATION", expected_validation, "OPERATING_POINT_MISMATCH"),
        ("final_test_report", "TEST", expected_test, "TEST_EVIDENCE_MISSING"),
    )
    for key, role, manifest, code in reports:
        report = closure.get(key)
        if not isinstance(report, dict):
            _promotion_fail(code, f"{key} ausente")
        if report.get("schema_version") != 1 or report.get("role") != role:
            _promotion_fail(
                "TEST_ROLE_INVALID" if role == "TEST" else code, f"role de {key} inválido"
            )
        if report.get("producer") != closure["producer"]:
            _promotion_fail(code, f"produtor de {key} inválido")
        _parse_timestamp(report.get("generated_at"), code=code)
        bindings = {
            "training_run_id": training_run["mlflow_run_id"],
            "checkpoint_sha256": checkpoint["sha256"],
            "checkpoint_epoch": checkpoint["epoch"],
            "training_contract_sha256": contract["sha256"],
            "dataset_fingerprint": dataset["fingerprint"],
            "dataset_version": dataset["version"],
            "manifest_sha256": manifest["sha256"],
            "taxonomy_fingerprint": taxonomy["fingerprint"],
        }
        if any(report.get(name) != value for name, value in bindings.items()):
            mismatch = "TEST_CHECKPOINT_MISMATCH" if role == "TEST" else code
            _promotion_fail(mismatch, f"bindings de {key} divergentes")
        _validated_metrics(
            report.get("metrics"), code="TEST_METRICS_INVALID" if role == "TEST" else code
        )
    selection = closure["selection_evidence"]
    if (
        selection.get("selected_metric") != "map50_95"
        or selection.get("selected_metric_value") != selection["metrics"]["map50_95"]
    ):
        _promotion_fail("VALIDATION_EVIDENCE_MISSING", "seleção não usa mAP50:95 medido")
    if selection["selected_metric_value"] != record.get("best_metric") or selection[
        "selected_metric_value"
    ] != record.get("validation_metrics", {}).get("map50_95"):
        _promotion_fail("VALIDATION_EVIDENCE_MISSING", "métrica selecionada diverge do checkpoint")
    operating = closure["operating_point"]
    final_test = closure["final_test_report"]
    for threshold in ("confidence_threshold", "nms_threshold"):
        value = operating.get(threshold)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 < float(value) <= 1.0
        ):
            _promotion_fail("OPERATING_POINT_MISMATCH", f"{threshold} inválido")
        if final_test.get(threshold) != value:
            _promotion_fail("OPERATING_POINT_MISMATCH", "thresholds mudaram após VALIDATION")
    for report in (operating, final_test):
        measured = report["metrics"]
        if (
            measured.get("score_threshold") != report["confidence_threshold"]
            or measured.get("evaluation_nms_threshold") != report["nms_threshold"]
        ):
            _promotion_fail(
                "OPERATING_POINT_MISMATCH",
                "thresholds do report divergem da avaliação medida",
            )
    if record.get("serving_score_threshold") != operating["confidence_threshold"]:
        _promotion_fail("OPERATING_POINT_MISMATCH", "threshold do serving diverge")
    if (
        final_test.get("evaluation_status") != "COMPLETED"
        or final_test.get("sample_count") != expected_test["sample_count"]
        or final_test.get("annotation_count") != expected_test["annotation_count"]
    ):
        _promotion_fail("TEST_EVIDENCE_MISSING", "cobertura do TEST incompleta")

    run_params = closure_run.get("params") if isinstance(closure_run, dict) else None
    expected_run_params = {
        "producer": CLOSURE_PRODUCER,
        "producer_version": str(CLOSURE_PRODUCER_VERSION),
        "closure_manifest_sha256": closure_hash,
        "training_run_id": training_run["mlflow_run_id"],
        "checkpoint_sha256": checkpoint["sha256"],
        "validation_manifest_sha256": expected_validation["sha256"],
        "test_manifest_sha256": expected_test["sha256"],
        "dataset_fingerprint": dataset["fingerprint"],
    }
    if (
        closure_run.get("status") != "FINISHED"
        or closure_run.get("artifact_closure_sha256") != closure_hash
        or not isinstance(run_params, dict)
        or any(run_params.get(key) != value for key, value in expected_run_params.items())
    ):
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "prova MLflow do produtor ausente/divergente")


def validate_registration_manifest(
    record: dict[str, Any],
    *,
    promote: bool,
    closure: dict[str, Any] | None = None,
    training_run: dict[str, Any] | None = None,
    closure_run: dict[str, Any] | None = None,
) -> None:
    """Bloqueia registro incompleto e promoção antes do fechamento do protocolo."""
    metrics = record.get("validation_metrics")
    if not isinstance(metrics, dict) or metrics.get("map50_95") is None:
        raise ValueError(
            "registro exige métrica de VALIDATION medida; exporte sem --skip-validation"
        )
    parity = record.get("parity")
    if not isinstance(parity, dict) or not parity.get("passed"):
        raise ValueError("registro exige paridade PyTorch x ONNX aprovada")
    if not promote:
        return

    if record.get("stage") != "final":
        _promotion_fail("TRAINING_NOT_COMPLETE", "promoção exige stage final")
    if record.get("checkpoint") != "best":
        _promotion_fail("VALIDATION_EVIDENCE_MISSING", "checkpoint não é best.pt")
    training = record.get("training")
    if not isinstance(training, dict):
        _promotion_fail("TRAINING_NOT_COMPLETE", "training manifest ausente")
    contract_value = record.get("model_contract_path")
    if contract_value is None:
        contract_path = MODEL_METADATA_PATH.resolve()
    elif isinstance(contract_value, str):
        contract_path = (PROJECT_ROOT / contract_value).resolve()
        try:
            contract_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            _promotion_fail("TRAINING_CONTRACT_MISMATCH", "contrato fora do projeto")
    else:
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "path do contrato inválido")
    if not contract_path.is_file() or sha256_file(contract_path) != record.get(
        "model_contract_sha256"
    ):
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "contrato ausente ou alterado")
    metadata = load_model_config(contract_path)
    required_hashes = (
        "checkpoint_sha256",
        "onnx_sha256",
        "model_contract_sha256",
        "config_fingerprint",
        "dataset_fingerprint",
        "split_fingerprint",
        "class_mapping_fingerprint",
    )
    if any(not _is_sha256(record.get(field)) for field in required_hashes):
        _promotion_fail("CHECKPOINT_HASH_MISMATCH", "checksums/fingerprints incompletos")
    if str(record.get("quality_classification", "")).upper() in {
        "WEAK",
        "NOT APPROVED",
        "NOT_APPROVED",
        "FAILED",
        "REJECTED",
    }:
        _promotion_fail("QUALITY_GATE_REJECTED", "manifesto classifica o modelo como não aprovado")
    operating_lock = record.get("operating_point_lock")
    if not isinstance(operating_lock, dict):
        _promotion_fail("OPERATING_POINT_LOCK_INVALID", "manifesto não contém o lock")
    validate_operating_point_lock(operating_lock, record, require_approved=True)
    if record.get("serving_score_threshold") != operating_lock.get("confidence_threshold"):
        _promotion_fail("OPERATING_POINT_MISMATCH", "serving diverge do lock")
    benchmark = record.get("benchmark")
    if (
        not isinstance(benchmark, dict)
        or benchmark.get("passed") is not True
        or benchmark.get("onnx_sha256") != record.get("onnx_sha256")
        or benchmark.get("confidence_threshold") != operating_lock.get("confidence_threshold")
        or benchmark.get("nms_threshold") != operating_lock.get("nms_threshold")
    ):
        _promotion_fail("BENCHMARK_INVALID", "benchmark aprovado e vinculado ao ONNX é obrigatório")
    if record.get("model_contract_path") is not None:
        report_value = benchmark.get("report_path")
        if not isinstance(report_value, str):
            _promotion_fail("BENCHMARK_INVALID", "benchmark report ausente")
        report_path = (PROJECT_ROOT / report_value).resolve()
        try:
            report_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            _promotion_fail("BENCHMARK_INVALID", "benchmark fora do projeto")
        if not report_path.is_file() or sha256_file(report_path) != benchmark.get("report_sha256"):
            _promotion_fail("BENCHMARK_INVALID", "benchmark report alterado")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report != benchmark.get("report"):
            _promotion_fail("BENCHMARK_INVALID", "benchmark report diverge do manifesto")
        validate_benchmark_report(
            report,
            expected_sha256=record["onnx_sha256"],
            expected_confidence=operating_lock["confidence_threshold"],
            expected_nms=operating_lock["nms_threshold"],
        )
        if (
            report.get("validation_manifest_sha256")
            != operating_lock["validation_manifest"]["sha256"]
            or report.get("model_contract_sha256") != operating_lock["contract_sha256"]
        ):
            _promotion_fail("BENCHMARK_INVALID", "benchmark usa outra VALIDATION")
    if (
        not isinstance(record.get("onnx_path"), str)
        or not isinstance(record.get("opset"), int)
        or record.get("input_size") != metadata["test_size"]
        or record.get("class_names") != metadata["canonical_class_names"]
    ):
        _promotion_fail("TRAINING_CONTRACT_MISMATCH", "contrato ONNX incompleto")
    if training_run is None or closure_run is None:
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "lineage canônico não foi fornecido")
    _validate_closure_artifact(
        closure,
        record,
        training_run=training_run,
        closure_run=closure_run,
        contract_path=contract_path,
    )
    if record.get("model_contract_path") is not None:
        assert isinstance(closure, dict)
        quality_reference = operating_lock["quality_contract"]
        quality_path = PROJECT_ROOT / quality_reference["quality_contract_path"]
        test_metrics = closure["final_test_report"]["metrics"]
        expected_test_quality = evaluate_quality_contract(
            test_metrics,
            json.loads(quality_path.read_text(encoding="utf-8")),
            contract_path=quality_path,
        )
        if (
            not expected_test_quality["passed"]
            or record.get("frozen_test_quality") != expected_test_quality
        ):
            _promotion_fail("QUALITY_GATE_REJECTED", "Frozen Test não passou critérios pré-fixados")


def validate_registered_model_evidence(model: Any, dataset: Any) -> None:
    """Recheck persisted promotion evidence against its actual artifacts."""
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    reference = metrics.get("promotion_manifest")
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        raise TypeError("promotion manifest ausente do ModelVersion")
    manifest_path = (PROJECT_ROOT / reference["path"]).resolve()
    try:
        manifest_path.relative_to(SERVING_DIR.resolve())
    except ValueError as exc:
        raise ValueError("promotion manifest fora do serving canônico") from exc
    if not manifest_path.is_file() or sha256_file(manifest_path) != reference.get("sha256"):
        raise ValueError("promotion manifest ausente ou alterado")
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    closure_reference = record.get("closure_artifact")
    if not isinstance(closure_reference, dict) or not isinstance(
        closure_reference.get("path"), str
    ):
        raise TypeError("closure ausente do promotion manifest")
    closure_path = (PROJECT_ROOT / closure_reference["path"]).resolve()
    try:
        closure_path.relative_to(SERVING_DIR.resolve())
    except ValueError as exc:
        raise ValueError("closure fora do serving canônico") from exc
    if not closure_path.is_file() or sha256_file(closure_path) != closure_reference.get("sha256"):
        raise ValueError("closure ausente ou alterado")
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    training = closure.get("training")
    training_run_id = training.get("training_run_id") if isinstance(training, dict) else None
    validate_registration_manifest(
        record,
        promote=True,
        closure=closure,
        training_run=training_run_for(record.get("config_fingerprint"), training_run_id),
        closure_run=closure_run_for(closure_reference.get("mlflow_run_id")),
    )

    contract_value = record.get("model_contract_path")
    contract_path = (
        (PROJECT_ROOT / contract_value).resolve()
        if isinstance(contract_value, str)
        else MODEL_METADATA_PATH.resolve()
    )
    metadata = load_model_config(contract_path)
    serving = metrics.get("serving")
    fingerprints = metrics.get("fingerprints")
    closure_dataset = closure["dataset"]
    expected_split = {
        "dataset_fingerprint": closure_dataset["fingerprint"],
        "split_fingerprint": closure_dataset["version"],
        "manifests": closure_dataset["manifests"],
    }
    if (
        model.kind != "vision"
        or model.name != metadata["model_id"]
        or model.version != f"{record['stage']}-epoch{record['epoch']}-{record['onnx_sha256'][:12]}"
        or model.checksum != record["onnx_sha256"]
        or dataset is None
        or model.dataset_version_id != dataset.id
        or dataset.name != metadata["dataset"]["dataset_version_name"]
        or dataset.version != closure_dataset["version"][:16]
        or dataset.split != expected_split
        or list(dataset.classes) != metadata["canonical_class_names"]
        or metrics.get("quality_classification") != record.get("quality_classification")
        or metrics.get("frozen_test_quality") != record.get("frozen_test_quality")
        or metrics.get("closure_artifact") != closure_reference
        or metrics.get("benchmark") != record.get("benchmark")
        or metrics.get("operating_point") != closure.get("operating_point")
        or metrics.get("final_test") != closure.get("final_test_report")
        or not isinstance(serving, dict)
        or not isinstance(fingerprints, dict)
        or fingerprints.get("dataset_fingerprint") != closure_dataset["fingerprint"]
        or fingerprints.get("split_fingerprint") != closure_dataset["version"]
        or serving.get("onnx_path") != record.get("onnx_path")
        or serving.get("checkpoint_sha256") != record.get("checkpoint_sha256")
        or serving.get("model_contract_sha256") != record.get("model_contract_sha256")
        or serving.get("score_threshold") != record.get("serving_score_threshold")
        or serving.get("nms_threshold") != closure["operating_point"]["nms_threshold"]
    ):
        raise ValueError("ModelVersion/DatasetVersion diverge do closure validado")
    onnx_path = (PROJECT_ROOT / record["onnx_path"]).resolve()
    try:
        onnx_path.relative_to(SERVING_DIR.resolve())
    except ValueError as exc:
        raise ValueError("ONNX fora do serving canônico") from exc
    checkpoint_path = (
        PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt"
    ).resolve()
    if not onnx_path.is_file() or sha256_file(onnx_path) != model.checksum:
        raise ValueError("ONNX ausente ou alterado")
    if not checkpoint_path.is_file() or sha256_file(checkpoint_path) != record["checkpoint_sha256"]:
        raise ValueError("checkpoint ausente ou alterado")


def _metrics_by_group(
    run: Any,
    rows: list[dict[str, Any]],
    *,
    labels: list[str],
    confidence_threshold: float,
    iou_threshold: float,
) -> dict[str, dict[str, Any]]:
    """Métricas por grupo do manifest (país): separa domínio visto de não visto.

    A VALIDATION_V2 mistura países que o V1 treinou com a China, que o V1 só
    usou para selecionar checkpoint; sem esse recorte, V1 vs V2 é incomparável.
    """
    from app.ml.metrics import evaluate

    group_of = {str(row["image_path"]): str(row["group"]) for row in rows}
    result: dict[str, dict[str, Any]] = {}
    for group in sorted(set(group_of.values())):
        members = {image for image, value in group_of.items() if value == group}
        metrics = evaluate(
            [p for p in run.predictions if p.image_id in members],
            [t for t in run.ground_truths if t.image_id in members],
            labels=labels,
            score_threshold=confidence_threshold,
            iou_threshold=iou_threshold,
        ).as_persisted()
        per_class: dict[str, dict[str, Any]] = metrics["per_class"]  # type: ignore[assignment]
        result[group] = {
            "images": len(members),
            **{key: metrics[key] for key in ("precision", "recall", "f1", "map50", "map50_95")},
            "per_class_ap50_95": {label: values["ap50_95"] for label, values in per_class.items()},
        }
    return result


def evaluate_checkpoint_for_role(
    checkpoint: Path,
    *,
    role: str,
    confidence_threshold: float,
    nms_threshold: float,
    batch_size: int = 8,
    contract_path: Path = MODEL_METADATA_PATH,
    manifest_path: Path | None = None,
    representation: str = "raw",
    frozen_test_ledger: Path | None = None,
    operating_point_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Executa o evaluator único no split explicitamente declarado pelo fechamento.

    Sem `manifest_path` o papel resolve para os manifests do V1. A geração V2
    passa o manifest do próprio contrato (VALIDATION_V2 ou, uma única vez após
    o congelamento, FROZEN_INTERNAL_TEST_V2).
    """
    from app.ml.detection_dataset import (
        AuthorizedDetectionDataset,
        build_yolox_dataloader,
        load_authorized_manifest,
    )
    from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator, manifest_for_evaluation
    from app.ml.metrics import evaluate, hard_cases, negative_image_false_positives

    if role not in {"VALIDATION", "TEST"}:
        raise ValueError("closure evaluation exige VALIDATION ou TEST")
    if role == "TEST":
        lock = operating_point_lock
        ledger_path = frozen_test_ledger
        if not isinstance(lock, dict) or ledger_path is None:
            _promotion_fail("FROZEN_TEST_LOCK_REQUIRED", "TEST exige lock e ledger")
        if ledger_path.resolve() != frozen_test_ledger_path(lock).resolve():
            _promotion_fail("FROZEN_TEST_LOCK_REQUIRED", "ledger não é o canônico")
        if not ledger_path.is_file():
            _promotion_fail("FROZEN_TEST_LOCK_REQUIRED", "ledger ainda não foi aberto")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if any(
            ledger.get(key) != expected
            for key, expected in {
                "state": "OPENED",
                "operating_point_lock_sha256": lock.get("operating_point_lock_sha256"),
                "frozen_test_manifest_sha256": lock.get("frozen_test_manifest", {}).get("sha256"),
                "checkpoint_sha256": lock.get("checkpoint_sha256"),
            }.items()
        ):
            _promotion_fail("FROZEN_TEST_LOCK_REQUIRED", "ledger diverge do lock")
        if (
            sha256_file(checkpoint) != lock.get("checkpoint_sha256")
            or representation != lock.get("representation")
            or confidence_threshold != lock.get("confidence_threshold")
            or nms_threshold != lock.get("nms_threshold")
            or sha256_file(contract_path) != lock.get("contract_sha256")
            or manifest_path is None
            or sha256_file(manifest_path) != lock.get("frozen_test_manifest", {}).get("sha256")
        ):
            _promotion_fail("FROZEN_TEST_LOCK_REQUIRED", "avaliação diverge do lock")
        claim_frozen_test_evaluation_once(ledger_path, lock)
    metadata = load_model_config(contract_path)
    base = EvaluationConfig.from_model_metadata(metadata)
    config = EvaluationConfig(
        confidence_threshold=base.confidence_threshold,
        nms_threshold=nms_threshold,
        iou_threshold=base.iou_threshold,
        num_classes=base.num_classes,
        class_names=base.class_names,
        input_size=base.input_size,
    )
    resolved_manifest = manifest_path or manifest_for_evaluation(role, allow_test=role == "TEST")
    rows = load_authorized_manifest(resolved_manifest, intended_split=role)
    dataset = AuthorizedDetectionDataset(
        rows,
        input_size=config.input_size,
        mode="validation" if role == "VALIDATION" else "test",
    )
    loader = build_yolox_dataloader(
        dataset,
        batch_size=batch_size,
        num_workers=0,
        shuffle=False,
    )
    model, info = load_checkpoint_model(
        checkpoint, contract_path=contract_path, representation=representation
    )
    model.cuda()
    run = YOLOXEvaluator(
        model, loader, config, device="cuda", role=role, manifest_path=resolved_manifest
    ).evaluate()
    operating_result = evaluate(
        run.predictions,
        run.ground_truths,
        labels=list(config.class_names),
        score_threshold=confidence_threshold,
        iou_threshold=config.iou_threshold,
    )
    return {
        "samples": run.samples,
        "positive_images": run.positive_images,
        "negative_images": run.negative_images,
        **operating_result.as_persisted(),
        "evaluation_nms_threshold": nms_threshold,
        "manifest": resolved_manifest.relative_to(PROJECT_ROOT).as_posix(),
        "manifest_sha256": sha256_file(resolved_manifest),
        "checkpoint_sha256": info["checkpoint_sha256"],
        "checkpoint_epoch": info["epoch"],
        "representation": representation,
        "negative_image_false_positives": negative_image_false_positives(
            run.predictions,
            {str(row["image_path"]) for row in rows if not row["boxes"]},
            score_threshold=confidence_threshold,
        ),
        "by_group": _metrics_by_group(
            run,
            rows,
            labels=list(config.class_names),
            confidence_threshold=confidence_threshold,
            iou_threshold=config.iou_threshold,
        ),
        "hard_cases": hard_cases(
            run.predictions,
            run.ground_truths,
            score_threshold=confidence_threshold,
            iou_threshold=config.iou_threshold,
        ),
    }


def select_operating_point_on_validation(
    checkpoint: Path,
    *,
    contract_path: Path,
    representation: str,
    quality_contract_path: Path,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Executa inferência uma vez por NMS e seleciona somente sobre VALIDATION."""
    if not quality_contract_path.is_file():
        raise ValueError("QUALITY_GATE_NOT_AVAILABLE: contrato de qualidade ausente")
    from app.ml.detection_dataset import (
        AuthorizedDetectionDataset,
        build_yolox_dataloader,
        load_authorized_manifest,
    )
    from app.ml.evaluator import EvaluationConfig, YOLOXEvaluator
    from app.ml.metrics import evaluate, negative_image_false_positives
    from app.ml.training import dataset_binding

    metadata = load_model_config(contract_path)
    policy = json.loads(quality_contract_path.read_text(encoding="utf-8"))
    selection = policy.get("selection_method")
    if not isinstance(selection, dict):
        raise TypeError("quality contract sem selection_method")
    confidence_candidates = selection.get("confidence_candidates")
    nms_candidates = selection.get("nms_candidates")
    if not isinstance(confidence_candidates, list) or not isinstance(nms_candidates, list):
        raise TypeError("quality contract sem candidatos explícitos")
    binding = dataset_binding(metadata.get("dataset"))
    rows = load_authorized_manifest(binding.validation_manifest, intended_split="VALIDATION")
    dataset = AuthorizedDetectionDataset(
        rows, input_size=tuple(metadata["input_size"]), mode="validation"
    )
    loader = build_yolox_dataloader(dataset, batch_size=batch_size, num_workers=0, shuffle=False)
    model, info = load_checkpoint_model(
        checkpoint, contract_path=contract_path, representation=representation
    )
    model.cuda()
    base = EvaluationConfig.from_model_metadata(metadata)
    negative_ids = {str(row["image_path"]) for row in rows if not row["boxes"]}
    candidates: list[dict[str, Any]] = []
    for nms in nms_candidates:
        config = EvaluationConfig(
            confidence_threshold=base.confidence_threshold,
            nms_threshold=float(nms),
            iou_threshold=base.iou_threshold,
            num_classes=base.num_classes,
            class_names=base.class_names,
            input_size=base.input_size,
        )
        run = YOLOXEvaluator(
            model,
            loader,
            config,
            device="cuda",
            role="VALIDATION",
            manifest_path=binding.validation_manifest,
        ).evaluate()
        for confidence in confidence_candidates:
            operating = evaluate(
                run.predictions,
                run.ground_truths,
                labels=list(config.class_names),
                score_threshold=float(confidence),
                iou_threshold=config.iou_threshold,
            )
            metrics = {
                "samples": run.samples,
                "positive_images": run.positive_images,
                "negative_images": run.negative_images,
                **operating.as_persisted(),
                "evaluation_nms_threshold": float(nms),
                "manifest": binding.validation_manifest.relative_to(PROJECT_ROOT).as_posix(),
                "manifest_sha256": sha256_file(binding.validation_manifest),
                "checkpoint_sha256": info["checkpoint_sha256"],
                "checkpoint_epoch": info["epoch"],
                "representation": representation,
                "negative_image_false_positives": negative_image_false_positives(
                    run.predictions,
                    negative_ids,
                    score_threshold=float(confidence),
                ),
                "by_group": _metrics_by_group(
                    run,
                    rows,
                    labels=list(config.class_names),
                    confidence_threshold=float(confidence),
                    iou_threshold=config.iou_threshold,
                ),
            }
            quality = evaluate_quality_contract(
                metrics, policy, contract_path=quality_contract_path
            )
            candidates.append({"metrics": metrics, "quality": quality})

    def rank(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
        metrics = item["metrics"]
        return (
            float(metrics["per_class"]["D40"]["f1"]),
            float(metrics["f1"]),
            -float(metrics["negative_image_false_positives"]["false_positives_per_negative_image"]),
            -float(metrics["score_threshold"]),
            -float(metrics["evaluation_nms_threshold"]),
        )

    eligible = [item for item in candidates if item["quality"]["passed"]]
    selected = max(eligible or candidates, key=rank)
    selected_metrics = selected["metrics"]
    return {
        **selected_metrics,
        "quality_contract": selected["quality"],
        "operating_point_selection": {
            "role": "VALIDATION",
            "status": "APPROVED" if eligible else "REJECTED",
            "eligible_candidates": len(eligible),
            "evaluated_candidates": len(candidates),
            "ranking": selection.get("ranking"),
            "candidates": [
                {
                    "confidence_threshold": item["metrics"]["score_threshold"],
                    "nms_threshold": item["metrics"]["evaluation_nms_threshold"],
                    "map50_95": item["metrics"]["map50_95"],
                    "f1": item["metrics"]["f1"],
                    "d40_f1": item["metrics"]["per_class"]["D40"]["f1"],
                    "passed": item["quality"]["passed"],
                }
                for item in candidates
            ],
        },
    }


def build_closure_artifact(
    record: dict[str, Any],
    *,
    training_run: dict[str, Any],
    completion_reason: str,
    selection_metrics: dict[str, Any],
    operating_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    confidence_threshold: float,
    nms_threshold: float,
    generated_at: str | None = None,
    early_stop_approval: dict[str, Any] | None = None,
    contract_path: Path = MODEL_METADATA_PATH,
    validation_manifest_path: Path | None = None,
    test_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Monta o único artifact imutável que pode autorizar promoção."""
    contract = _authoritative_contract(training_run, contract_path=contract_path)
    validation_manifest = _manifest_evidence("VALIDATION", validation_manifest_path)
    test_manifest = _manifest_evidence("TEST", test_manifest_path)
    timestamp = generated_at or datetime.now(UTC).isoformat()
    producer = {"name": CLOSURE_PRODUCER, "version": CLOSURE_PRODUCER_VERSION}
    checkpoint = {
        "sha256": record.get("checkpoint_sha256"),
        "representation": record.get("representation"),
        "epoch": record.get("epoch"),
        "global_step": record.get("global_step"),
        "model_id": contract["model_id"],
        "architecture": contract["architecture"],
        "config_fingerprint": record.get("config_fingerprint"),
        "dataset_fingerprint": record.get("dataset_fingerprint"),
        "split_fingerprint": record.get("split_fingerprint"),
        "taxonomy_fingerprint": record.get("class_mapping_fingerprint"),
    }
    dataset = {
        "version": record.get("split_fingerprint"),
        "fingerprint": record.get("dataset_fingerprint"),
        "manifests": {"VALIDATION": validation_manifest, "TEST": test_manifest},
    }
    taxonomy = {
        "version": contract["schema_version"],
        "fingerprint": record.get("class_mapping_fingerprint"),
        "class_names": contract["class_names"],
        "canonical_class_names": contract["canonical_class_names"],
    }
    common = {
        "schema_version": 1,
        "producer": producer,
        "generated_at": timestamp,
        "training_run_id": training_run.get("mlflow_run_id"),
        "checkpoint_sha256": checkpoint["sha256"],
        "checkpoint_epoch": checkpoint["epoch"],
        "training_contract_sha256": contract["sha256"],
        "dataset_fingerprint": dataset["fingerprint"],
        "dataset_version": dataset["version"],
        "taxonomy_fingerprint": taxonomy["fingerprint"],
    }
    completed_epochs = _as_completed_epochs(training_run)
    if completion_reason == "EARLY_STOP_APPROVED" and isinstance(early_stop_approval, dict):
        completed_epochs = early_stop_approval.get("persisted_last_epoch")
    closure = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "producer": producer,
        "generated_at": timestamp,
        "training_contract": contract,
        "training": {
            "training_run_id": training_run.get("mlflow_run_id"),
            "completed_epochs": completed_epochs,
            "completion_reason": completion_reason,
            "early_stop_approval": early_stop_approval,
        },
        "checkpoint": checkpoint,
        "dataset": dataset,
        "taxonomy": taxonomy,
        "selection_evidence": {
            **common,
            "role": "VALIDATION",
            "manifest_sha256": validation_manifest["sha256"],
            "selected_metric": "map50_95",
            "selected_metric_value": selection_metrics.get("map50_95"),
            "metrics": _with_f1(selection_metrics),
        },
        "operating_point": {
            **common,
            "role": "VALIDATION",
            "manifest_sha256": validation_manifest["sha256"],
            "confidence_threshold": confidence_threshold,
            "nms_threshold": nms_threshold,
            "metrics": _with_f1(operating_metrics),
        },
        "final_test_report": {
            **common,
            "role": "TEST",
            "manifest_sha256": test_manifest["sha256"],
            "input_size": contract["input_size"],
            "confidence_threshold": confidence_threshold,
            "nms_threshold": nms_threshold,
            "metrics": _with_f1(test_metrics),
            "sample_count": test_metrics.get("samples"),
            "annotation_count": sum(
                values.get("support", 0)
                for values in test_metrics.get("per_class", {}).values()
                if isinstance(values, dict)
            ),
            "evaluation_status": "COMPLETED",
        },
    }
    closure["closure_manifest_sha256"] = _canonical_artifact_hash(closure)
    return closure


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    encoded = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") == encoded:
            return
        raise FileExistsError(f"artifact imutável já existe: {path}")
    staging = path.with_name(f".{path.name}.tmp")
    try:
        staging.write_text(encoded, encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _lock_operating_point_command(args: argparse.Namespace) -> int:
    """Congela um ponto escolhido exclusivamente em VALIDATION."""
    if not args.quality_contract.is_file():
        raise ValueError("QUALITY_GATE_NOT_AVAILABLE: contrato de qualidade ausente")
    from app.ml.training import dataset_binding

    record = json.loads(args.export_manifest.read_text(encoding="utf-8"))
    validate_registration_manifest(record, promote=False)
    contract_path = args.contract.resolve()
    metadata = load_model_config(contract_path)
    binding = dataset_binding(metadata.get("dataset"))
    validation = json.loads(args.validation_report.read_text(encoding="utf-8"))
    quality_policy = json.loads(args.quality_contract.read_text(encoding="utf-8"))
    expected = {
        "checkpoint_sha256": record.get("checkpoint_sha256"),
        "representation": args.representation,
        "manifest_sha256": sha256_file(binding.validation_manifest),
        "score_threshold": args.confidence_threshold,
        "evaluation_nms_threshold": args.nms_threshold,
    }
    mismatches = [key for key, value in expected.items() if validation.get(key) != value]
    if mismatches:
        raise SystemExit(
            "validation report diverge do candidato/operating point: " + ", ".join(mismatches)
        )
    recomputed = select_operating_point_on_validation(
        (PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt").resolve(),
        contract_path=contract_path,
        representation=args.representation,
        quality_contract_path=args.quality_contract.resolve(),
    )
    if validation != recomputed:
        raise SystemExit("validation report não coincide com reavaliação canônica")
    quality = evaluate_quality_contract(
        validation,
        quality_policy,
        contract_path=args.quality_contract.resolve(),
    )
    lock = build_operating_point_lock(
        record,
        training_run_id=args.training_run_id,
        contract_path=contract_path,
        validation_manifest=binding.validation_manifest,
        test_manifest=(
            PROJECT_ROOT / metadata["dataset"]["sealed_roles"]["FROZEN_INTERNAL_TEST_V2"]
        ),
        representation=args.representation,
        confidence_threshold=args.confidence_threshold,
        nms_threshold=args.nms_threshold,
        validation_metrics=validation,
        quality_contract=quality,
    )
    validate_operating_point_lock(lock, record, require_approved=False)
    _write_json_atomic(args.output, lock)
    print(
        json.dumps(
            {
                "operating_point_lock": str(args.output),
                "sha256": lock["operating_point_lock_sha256"],
            },
            indent=2,
        )
    )
    return 0


def _close_model_command(args: argparse.Namespace) -> int:
    record = json.loads(args.export_manifest.read_text(encoding="utf-8"))
    validate_registration_manifest(record, promote=False)
    lock = json.loads(args.operating_point_lock.read_text(encoding="utf-8"))
    validate_operating_point_lock(lock, record, require_approved=True)
    if record.get("checkpoint") != "best":
        raise SystemExit("close-model exige best.pt selecionado por VALIDATION")
    contract_path = (PROJECT_ROOT / lock["contract_path"]).resolve()
    metadata = load_model_config(contract_path)
    checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt"
    if not checkpoint.is_file() or sha256_file(checkpoint) != record.get("checkpoint_sha256"):
        raise SystemExit("best.pt atual diverge do export manifest")
    training_run_id = (record.get("validation_metrics") or {}).get("mlflow_run_id")
    training_run = training_run_for(record.get("config_fingerprint"), training_run_id)
    contract = _authoritative_contract(training_run, contract_path=contract_path)
    _, checkpoint_info = load_checkpoint_model(
        checkpoint,
        contract_path=contract_path,
        representation=lock["representation"],
    )
    _validate_candidate_bindings(
        record,
        training_run,
        contract,
        checkpoint_info=checkpoint_info,
    )
    completed = _as_completed_epochs(training_run)
    completion_reason = "MAX_EPOCH_REACHED"
    early_stop_approval = None
    if completed is None or completed < contract["max_epoch"]:
        if args.early_stop_approval is None:
            raise SystemExit("training ainda não concluiu o contrato oficial")
        early_stop_approval = json.loads(args.early_stop_approval.read_text(encoding="utf-8"))
        last_checkpoint = checkpoint.with_name("last.pt")
        if not last_checkpoint.is_file():
            raise SystemExit("last.pt ausente para comprovar estado persistido do early stop")
        _, last_info = load_checkpoint_model(last_checkpoint)
        persisted_epoch = last_info.get("epoch")
        persisted_epoch = persisted_epoch + 1 if isinstance(persisted_epoch, int) else None
        if (
            early_stop_approval.get("persisted_last_checkpoint_sha256")
            != last_info.get("checkpoint_sha256")
            or early_stop_approval.get("persisted_last_epoch") != persisted_epoch
            or any(
                last_info.get(key) != contract["fingerprints"][key]
                for key in contract["fingerprints"]
            )
        ):
            raise SystemExit("approval diverge do last.pt persistido")
        completed = persisted_epoch
        completion_reason = "EARLY_STOP_APPROVED"
    provisional = {
        "training": {
            "training_run_id": training_run.get("mlflow_run_id"),
            "completed_epochs": completed,
            "completion_reason": completion_reason,
            "early_stop_approval": early_stop_approval,
        },
        "checkpoint": {"sha256": record.get("checkpoint_sha256")},
    }
    _validate_training_completion(provisional, training_run, contract)
    benchmark_report = json.loads(args.benchmark_report.read_text(encoding="utf-8"))
    validate_benchmark_report(
        benchmark_report,
        expected_sha256=record["onnx_sha256"],
        expected_confidence=lock["confidence_threshold"],
        expected_nms=lock["nms_threshold"],
    )
    if (
        benchmark_report.get("validation_manifest_sha256") != lock["validation_manifest"]["sha256"]
        or benchmark_report.get("model_contract_sha256") != lock["contract_sha256"]
    ):
        _promotion_fail("BENCHMARK_INVALID", "benchmark usa outra VALIDATION")
    selection_metrics = record["validation_metrics"]
    operating_metrics = lock["validation_metrics"]
    ledger_path = frozen_test_ledger_path(lock)
    ledger = open_frozen_test_once(ledger_path, lock)
    test_metrics = evaluate_checkpoint_for_role(
        checkpoint,
        role="TEST",
        confidence_threshold=lock["confidence_threshold"],
        nms_threshold=lock["nms_threshold"],
        batch_size=args.batch_size,
        contract_path=contract_path,
        manifest_path=(PROJECT_ROOT / lock["frozen_test_manifest"]["path"]).resolve(),
        representation=lock["representation"],
        frozen_test_ledger=ledger_path,
        operating_point_lock=lock,
    )
    quality_reference = lock["quality_contract"]
    quality_path = PROJECT_ROOT / quality_reference["quality_contract_path"]
    frozen_quality = evaluate_quality_contract(
        test_metrics,
        json.loads(quality_path.read_text(encoding="utf-8")),
        contract_path=quality_path,
    )
    frozen_result = {
        "role": "FROZEN_INTERNAL_TEST_V2",
        "ledger": ledger,
        "operating_point_lock_sha256": lock["operating_point_lock_sha256"],
        "checkpoint_sha256": record["checkpoint_sha256"],
        "representation": lock["representation"],
        "metrics": test_metrics,
        "fixed_validation_criteria_applied_to_test": frozen_quality,
    }
    frozen_result_path = SERVING_DIR / (
        "frozen-test-result-" + lock["operating_point_lock_sha256"][:12] + ".json"
    )
    _write_json_atomic(frozen_result_path, frozen_result)
    if not frozen_quality["passed"]:
        print(json.dumps({"status": "FAILED", "result": str(frozen_result_path)}))
        return 2
    closure = build_closure_artifact(
        record,
        training_run=training_run,
        completion_reason=completion_reason,
        selection_metrics=selection_metrics,
        operating_metrics=operating_metrics,
        test_metrics=test_metrics,
        confidence_threshold=lock["confidence_threshold"],
        nms_threshold=lock["nms_threshold"],
        early_stop_approval=early_stop_approval,
        contract_path=contract_path,
        validation_manifest_path=(PROJECT_ROOT / lock["validation_manifest"]["path"]).resolve(),
        test_manifest_path=(PROJECT_ROOT / lock["frozen_test_manifest"]["path"]).resolve(),
    )
    closure["operating_point_lock"] = {
        "path": args.operating_point_lock.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "sha256": sha256_file(args.operating_point_lock),
        "artifact_sha256": lock["operating_point_lock_sha256"],
    }
    closure["frozen_test_ledger"] = {
        **ledger,
        "path": ledger_path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": sha256_file(ledger_path),
    }
    closure["closure_manifest_sha256"] = _canonical_artifact_hash(closure)
    closure_hash = closure["closure_manifest_sha256"]
    closure_path = SERVING_DIR / f"{metadata['model_id']}-closure-{closure_hash[:12]}.json"
    params = {
        "producer": CLOSURE_PRODUCER,
        "producer_version": CLOSURE_PRODUCER_VERSION,
        "closure_manifest_sha256": closure_hash,
        "training_run_id": training_run["mlflow_run_id"],
        "checkpoint_sha256": record["checkpoint_sha256"],
        "validation_manifest_sha256": closure["dataset"]["manifests"]["VALIDATION"]["sha256"],
        "test_manifest_sha256": closure["dataset"]["manifests"]["TEST"]["sha256"],
        "dataset_fingerprint": record["dataset_fingerprint"],
    }
    _write_json_atomic(closure_path, closure)
    closure_run_id = log_mlflow(
        f"model-closure-{closure_hash[:12]}",
        {"purpose": "MODEL_CLOSURE", "training_run_id": training_run["mlflow_run_id"]},
        params,
        {
            "validation/map50_95": closure["selection_evidence"]["metrics"]["map50_95"],
            "test/map50_95": closure["final_test_report"]["metrics"]["map50_95"],
        },
        closure,
    )
    promotion_manifest = {
        **record,
        "stage": "final",
        "stage_note": None,
        "training": {
            "completed_epochs": completed,
            "contract_max_epoch": contract["max_epoch"],
            "completion_reason": completion_reason,
        },
        "serving_score_threshold": lock["confidence_threshold"],
        "quality_classification": "APPROVED",
        "frozen_test_quality": frozen_quality,
        "frozen_test_result": {
            "path": frozen_result_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(frozen_result_path),
        },
        "benchmark": {
            "passed": True,
            "onnx_sha256": record["onnx_sha256"],
            "confidence_threshold": lock["confidence_threshold"],
            "nms_threshold": lock["nms_threshold"],
            "report_path": args.benchmark_report.resolve().relative_to(PROJECT_ROOT).as_posix(),
            "report_sha256": sha256_file(args.benchmark_report),
            "report": benchmark_report,
        },
        "operating_point_lock": lock,
        "closure_artifact": {
            "path": closure_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(closure_path),
            "mlflow_run_id": closure_run_id,
        },
    }
    _write_json_atomic(args.output, promotion_manifest)
    print(json.dumps({"closure": str(closure_path), "manifest": str(args.output)}, indent=2))
    return 0


async def register_model(
    manifest_path: Path, *, promote: bool, shadow_dev: bool = False
) -> dict[str, Any]:
    """Grava dataset_versions + model_versions a partir de um export verificado (§9, §10)."""
    from datetime import UTC, datetime

    from app.config import URMIND_DEV_SHADOW_REF, get_settings
    from app.db.session import Database
    from app.repositories.core import InferenceRepository

    if shadow_dev:
        from urllib.parse import urlsplit

        settings = get_settings()
        # Explicitly scoped to the single user-authorized DEV project. Never
        # turn a rejected scientific result into a production promotion.
        dev_ref = URMIND_DEV_SHADOW_REF
        if (
            promote
            or settings.app_env.lower() not in {"development", "dev", "demo"}
            or urlsplit(settings.supabase_url or "").hostname != f"{dev_ref}.supabase.co"
            or urlsplit(settings.database_pooler_url or "").username != f"postgres.{dev_ref}"
        ):
            raise ValueError("registro shadow permitido somente no Urmind DEV")
    # Reject an unauthorized environment before reading local scientific artifacts.
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if shadow_dev:
        frozen_path = SERVING_DIR / "frozen-test-result-f0e81ba65bb1.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        lock = json.loads(
            (SERVING_DIR / "yolox-s-quality-rebuild-operating-point-lock-verified.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            record.get("onnx_path") != "models/serving/yolox-s-quality-rebuild-7d91f7f6f0c0.onnx"
            or frozen.get("fixed_validation_criteria_applied_to_test", {}).get("decision")
            != "REJECTED"
            or frozen.get("checkpoint_sha256") != record.get("checkpoint_sha256")
            or frozen.get("operating_point_lock_sha256") != lock.get("operating_point_lock_sha256")
            or lock.get("checkpoint_sha256") != record.get("checkpoint_sha256")
            or lock.get("confidence_threshold") != record.get("serving_score_threshold")
            or record.get("parity", {}).get("passed") is not True
            or record.get("class_names")
            != ["URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40"]
        ):
            raise ValueError("evidência científica do shadow DEV incompatível")
    validate_registration_manifest(record, promote=False)
    closure: dict[str, Any] | None = None
    training_run: dict[str, Any] | None = None
    closure_run: dict[str, Any] | None = None
    closure_reference = record.get("closure_artifact")
    if closure_reference is not None:
        if not isinstance(closure_reference, dict):
            _promotion_fail("CLOSURE_MANIFEST_INVALID", "referência ao closure ausente")
        closure_path_value = closure_reference.get("path")
        if not isinstance(closure_path_value, str):
            _promotion_fail("CLOSURE_MANIFEST_INVALID", "path do closure ausente")
        closure_path = (PROJECT_ROOT / closure_path_value).resolve()
        try:
            closure_path.relative_to(SERVING_DIR.resolve())
        except ValueError:
            _promotion_fail("CLOSURE_MANIFEST_INVALID", "closure fora do serving canônico")
        if not closure_path.is_file() or sha256_file(closure_path) != closure_reference.get(
            "sha256"
        ):
            _promotion_fail("CLOSURE_MANIFEST_INVALID", "closure ausente ou alterado")
        closure = json.loads(closure_path.read_text(encoding="utf-8"))
        closure_training = closure.get("training")
        training_run_id = (
            closure_training.get("training_run_id") if isinstance(closure_training, dict) else None
        )
        training_run = training_run_for(record.get("config_fingerprint"), training_run_id)
        closure_run = closure_run_for(closure_reference.get("mlflow_run_id"))
    elif promote:
        _promotion_fail("CLOSURE_MANIFEST_INVALID", "referência ao closure ausente")
    validate_registration_manifest(
        record,
        promote=closure is not None or promote,
        closure=closure,
        training_run=training_run,
        closure_run=closure_run,
    )
    promotion_reference = None
    if promote:
        resolved_manifest = manifest_path.resolve()
        try:
            resolved_manifest.relative_to(SERVING_DIR.resolve())
        except ValueError as exc:
            raise ValueError("promotion manifest fora do serving canônico") from exc
        promotion_reference = {
            "path": resolved_manifest.relative_to(PROJECT_ROOT.resolve()).as_posix(),
            "sha256": sha256_file(resolved_manifest),
        }
    metrics = (
        closure["selection_evidence"]["metrics"]
        if closure is not None
        else record["validation_metrics"]
    )
    onnx_path = (PROJECT_ROOT / record["onnx_path"]).resolve()
    try:
        onnx_path.relative_to(SERVING_DIR.resolve())
    except ValueError as exc:
        raise ValueError("ONNX fora do diretório canônico de serving") from exc
    if not onnx_path.is_file():
        raise ValueError("ONNX registrado não existe")
    if sha256_file(onnx_path) != record["onnx_sha256"]:
        raise ValueError("ONNX em disco diverge do manifesto de export")
    contract_value = record.get("model_contract_path")
    contract_path = (
        (PROJECT_ROOT / contract_value).resolve()
        if isinstance(contract_value, str)
        else MODEL_METADATA_PATH.resolve()
    )
    metadata = load_model_config(contract_path)
    if promote:
        checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt"
        if not checkpoint.is_file() or sha256_file(checkpoint) != record["checkpoint_sha256"]:
            raise ValueError("best.pt atual diverge do checkpoint selecionado")
    if closure is None and metadata.get("dataset", {}).get("dataset_version_name"):
        dataset_name = metadata["dataset"]["dataset_version_name"]
        dataset_version = record["split_fingerprint"][:16]
        dataset_split = {
            "dataset_fingerprint": record["dataset_fingerprint"],
            "split_fingerprint": record["split_fingerprint"],
            "manifests": metadata["dataset"]["manifests"],
        }
    elif closure is None:
        readiness = json.loads(READINESS_REPORT_PATH.read_text(encoding="utf-8"))
        dataset_name = "rdd2022-model-v1-authorized"
        dataset_version = readiness["integrity"]["rdd_split_sha256"][:16]
        dataset_split = {
            "manifests_sha256": readiness["integrity"]["manifests_sha256"],
            "rdd_split_sha256": readiness["integrity"]["rdd_split_sha256"],
            "counts": readiness["counts"],
        }
    else:
        dataset_name = metadata["dataset"]["dataset_version_name"]
        dataset_version = closure["dataset"]["version"][:16]
        dataset_split = {
            "dataset_fingerprint": closure["dataset"]["fingerprint"],
            "split_fingerprint": closure["dataset"]["version"],
            "manifests": closure["dataset"]["manifests"],
        }
    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            repository = InferenceRepository(session)
            dataset = await repository.upsert_dataset_version(
                name=dataset_name,
                version=dataset_version,
                source="https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_Damage_Dataset_released_through_CRDDC_2022/21431547",
                license="CC BY 4.0",
                classes=list(metadata["canonical_class_names"]),
                split=dataset_split,
            )
            model = await repository.register_model(
                refresh_unpromoted=promote or shadow_dev,
                name=metadata["model_id"],
                kind="vision",
                version=f"{record.get('stage', STAGE_BASELINE_EARLY)}-epoch{record['epoch']}-{record['onnx_sha256'][:12]}",
                checksum=record["onnx_sha256"],
                dataset_version_id=dataset.id,
                metrics={
                    "stage": record.get("stage", STAGE_BASELINE_EARLY),
                    "stage_note": record.get("stage_note"),
                    "training": (
                        {
                            **closure["training"],
                            "contract": closure["training_contract"],
                        }
                        if closure is not None
                        else record.get("training")
                    ),
                    "code": record.get("code"),
                    "checkpoint_selection": (
                        closure.get("selection_evidence")
                        if closure is not None
                        else record.get("checkpoint_selection")
                    ),
                    "operating_point": (
                        closure.get("operating_point")
                        if closure is not None
                        else record.get("operating_point")
                    ),
                    "final_test": (
                        closure.get("final_test_report")
                        if closure is not None
                        else record.get("final_test_metrics")
                    ),
                    "closure_artifact": record.get("closure_artifact"),
                    "promotion_manifest": promotion_reference,
                    "quality_classification": "REJECTED"
                    if shadow_dev
                    else record.get("quality_classification"),
                    "serving_status": "EXPERIMENTAL_SHADOW" if shadow_dev else None,
                    "shadow_authorized": shadow_dev,
                    "shadow_scope": "URMIND_DEV_ONLY" if shadow_dev else None,
                    "shadow_project_ref": dev_ref if shadow_dev else None,
                    "frozen_test_quality": (
                        {
                            "decision": "REJECTED",
                            "result_path": frozen_path.relative_to(PROJECT_ROOT).as_posix(),
                            "result_sha256": sha256_file(frozen_path),
                            "operating_point_lock_sha256": frozen["operating_point_lock_sha256"],
                        }
                        if shadow_dev
                        else record.get("frozen_test_quality")
                    ),
                    "benchmark": record.get("benchmark"),
                    "error_analysis": record.get("error_analysis"),
                    "taxonomy": {
                        "schema_version": metadata["schema_version"],
                        "class_names": metadata["class_names"],
                        "canonical_class_names": metadata["canonical_class_names"],
                    },
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
                        "nms_threshold": (
                            closure["operating_point"]["nms_threshold"]
                            if closure is not None
                            else metadata["evaluation"]["nms_threshold"]
                        ),
                        "parity": record["parity"],
                        "checkpoint_sha256": record["checkpoint_sha256"],
                        "representation": record.get("representation"),
                        "model_contract_sha256": record["model_contract_sha256"],
                        "model_contract_path": record.get("model_contract_path"),
                    },
                },
                promoted_at=None,
            )
            if promote:
                await repository.promote_exclusive(model, promoted_at=datetime.now(UTC))
            await session.commit()
            return {
                "model_version_id": str(model.id),
                "version": model.version,
                "promoted": model.promoted_at is not None,
            }
    finally:
        await database.close()


async def record_model_metadata_reconciliation(
    model_version_id: Any,
    *,
    previous_nms: float,
    reason: str = MODEL_METADATA_RECONCILIATION_REASON,
) -> dict[str, Any]:
    """Registra agora, sem backdate, a correção administrativa já ocorrida."""
    import uuid

    from app.config import get_settings
    from app.db.session import Database
    from app.repositories.core import DecisionRepository, InferenceRepository

    identifier = uuid.UUID(str(model_version_id))
    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            inference = InferenceRepository(session)
            model = await inference.model_version_for_update(identifier)
            if model is None:
                raise ValueError("ModelVersion inexistente")
            dataset = (
                await inference.dataset_version(model.dataset_version_id)
                if model.dataset_version_id is not None
                else None
            )
            if dataset is None:
                raise ValueError("ModelVersion sem DatasetVersion registrado")

            metrics = model.metrics if isinstance(model.metrics, dict) else {}
            closure_reference = metrics.get("closure_artifact")
            if not isinstance(closure_reference, dict):
                raise TypeError("ModelVersion sem referência ao closure")
            closure_value = closure_reference.get("path")
            if not isinstance(closure_value, str):
                raise TypeError("ModelVersion sem path do closure")
            closure_path = (PROJECT_ROOT / closure_value).resolve()
            try:
                closure_path.relative_to(SERVING_DIR.resolve())
            except ValueError as exc:
                raise ValueError("closure fora do diretório canônico") from exc
            if not closure_path.is_file():
                raise ValueError("closure autoritativo ausente")
            closure_file_sha256 = sha256_file(closure_path)
            closure = json.loads(closure_path.read_text(encoding="utf-8"))

            serving = metrics.get("serving")
            if not isinstance(serving, dict) or not isinstance(serving.get("onnx_path"), str):
                raise TypeError("ModelVersion sem contrato de serving")
            onnx_path = (PROJECT_ROOT / serving["onnx_path"]).resolve()
            try:
                onnx_path.relative_to(SERVING_DIR.resolve())
            except ValueError as exc:
                raise ValueError("ONNX fora do diretório canônico") from exc
            if not onnx_path.is_file():
                raise ValueError("ONNX registrado ausente")

            checkpoint_path = (
                PROJECT_ROOT / load_model_config()["training"]["output_directory"] / "best.pt"
            ).resolve()
            if not checkpoint_path.is_file():
                raise ValueError("best.pt autoritativo ausente")
            event = build_model_metadata_reconciliation_audit(
                model,
                dataset,
                closure,
                closure_reference=closure_reference,
                closure_file_sha256=closure_file_sha256,
                checkpoint_file_sha256=sha256_file(checkpoint_path),
                onnx_file_sha256=sha256_file(onnx_path),
                previous_nms=previous_nms,
                expected_previous_nms=float(load_model_config()["evaluation"]["nms_threshold"]),
                reason=reason,
            )
            decisions = DecisionRepository(session)
            existing = await decisions.audit_by_event_hash(event["event_hash"])
            if existing is None:
                audit = await decisions.add_audit(
                    operation=event["operation"],
                    entity_type=event["entity_type"],
                    entity_id=identifier,
                    actor=event["actor"],
                    before=event["before"],
                    after=event["after"],
                    event_hash=event["event_hash"],
                )
                await session.commit()
            else:
                if (
                    existing.entity_id != identifier
                    or existing.entity_type != event["entity_type"]
                    or existing.operation != event["operation"]
                    or existing.actor != event["actor"]
                    or existing.before_data != event["before"]
                    or existing.after_data != event["after"]
                ):
                    raise ValueError("evento existente diverge da reconciliação canônica")
                audit = existing
            return {
                "audit_log_id": str(audit.id),
                "model_version_id": str(identifier),
                "event_hash": audit.event_hash,
                "created_at": audit.created_at.isoformat(),
                "operation": audit.operation,
                "actor": audit.actor,
                "before": audit.before_data,
                "after": audit.after_data,
                "created": existing is None,
            }
    finally:
        await database.close()


def _report_command(command: str) -> int:
    from datetime import UTC, datetime

    model = _run_async(_promoted_model())
    serving = model["metrics"]["serving"]
    contract_value = serving.get("model_contract_path")
    contract_path = (
        PROJECT_ROOT / contract_value if isinstance(contract_value, str) else MODEL_METADATA_PATH
    )
    metadata = load_model_config(contract_path)
    from app.ml.training import dataset_binding

    validation_manifest = dataset_binding(metadata.get("dataset")).validation_manifest
    tags = {
        "model_version_id": str(model["id"]),
        "model_version": model["version"],
        "stage": str(model["metrics"].get("stage", "")),
    }
    flat: dict[str, float | None]
    if command == "evaluate":
        checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / "best.pt"
        if sha256_file(checkpoint) != serving["checkpoint_sha256"]:
            raise SystemExit(
                "best.pt atual não é o checkpoint do modelo promovido; avaliação recusada"
            )
        report = evaluate_checkpoint_for_role(
            checkpoint,
            role="VALIDATION",
            confidence_threshold=float(serving["score_threshold"]),
            nms_threshold=float(serving["nms_threshold"]),
            contract_path=contract_path,
            manifest_path=validation_manifest,
            representation=str(serving.get("representation") or "raw"),
        )
        flat = {
            f"validation/{key}": report[key]
            for key in ("map50", "map50_95", "precision", "recall", "f1")
        }
        for label, values in report["per_class"].items():
            for key in ("precision", "recall", "f1", "ap50", "ap50_95"):
                flat[f"validation/{label}/{key}"] = values[key]
        key = "evaluation_full"
    else:
        report = benchmark_onnx(
            PROJECT_ROOT / serving["onnx_path"],
            model["checksum"],
            model_version=str(model["version"]),
            confidence_threshold=float(serving["score_threshold"]),
            nms_threshold=float(serving["nms_threshold"]),
            expected_input_size=tuple(serving["input_size"]),
            expected_class_names=tuple(serving["class_names"]),
            validation_manifest_path=validation_manifest,
            contract_path=contract_path,
        )
        flat = {
            f"latency/{stage}/{metric}_ms": values[metric]
            for stage, values in report["stages_ms"].items()
            for metric in ("mean", "p50", "p95")
        }
        flat["latency/pipeline/fps"] = report["pipeline_fps_equivalent"]
        flat["memory/process_rss_mb"] = report["memory"]["process_rss_mb"]
        flat["memory/observed_peak_process_rss_mb"] = report["memory"][
            "observed_peak_process_rss_mb"
        ]
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


def mine_hard_negative_candidates(
    checkpoint: Path,
    *,
    contract_path: Path,
    representation: str,
    score_threshold: float = 0.5,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Negativas de TRAIN em que o modelo dispara com confiança: candidatas a revisão.

    Só lê o manifest TRAIN do contrato; VALIDATION, holdout e sonda nunca são
    minerados. O resultado não altera rótulo nem amostragem: uma negativa com
    disparo confiante pode ser hard negative OU omissão de rótulo, e só revisão
    humana decide (`rdd2022_v2_negative_policy.json`).
    """
    import torch

    from app.ml.detection_dataset import (
        AuthorizedDetectionDataset,
        build_yolox_dataloader,
        load_authorized_manifest,
    )
    from app.ml.evaluator import (
        EvaluationConfig,
        _image_infos,
        _indices,
        detections_to_predictions,
        postprocess_batch,
    )
    from app.ml.training import dataset_binding

    metadata = load_model_config(contract_path)
    manifest = dataset_binding(metadata.get("dataset")).train_manifest
    rows = [
        row
        for row in load_authorized_manifest(manifest, intended_split="TRAIN")
        if not row["boxes"]
    ]
    config = EvaluationConfig.from_model_metadata(metadata)
    dataset = AuthorizedDetectionDataset(rows, input_size=config.input_size, mode="train")
    loader = build_yolox_dataloader(dataset, batch_size=batch_size, num_workers=0)
    model, info = load_checkpoint_model(
        checkpoint, contract_path=contract_path, representation=representation
    )
    model.cuda()
    candidates: list[dict[str, Any]] = []
    with torch.inference_mode():
        for images, _, info_imgs, image_indices in loader:
            detections = postprocess_batch(model(images.cuda()), config)
            for prediction in detections_to_predictions(
                detections, _image_infos(info_imgs), _indices(image_indices), dataset, config
            ):
                if prediction.score >= score_threshold:
                    candidates.append(
                        {
                            "image": prediction.image_id,
                            "predicted_class": prediction.label,
                            "score": round(prediction.score, 4),
                            "box_xyxy": [
                                round(value, 1)
                                for value in (
                                    prediction.box.x1,
                                    prediction.box.y1,
                                    prediction.box.x2,
                                    prediction.box.y2,
                                )
                            ],
                        }
                    )
    group_of = {str(row["image_path"]): str(row["group"]) for row in rows}
    by_class: dict[str, int] = {}
    for item in candidates:
        by_class[item["predicted_class"]] = by_class.get(item["predicted_class"], 0) + 1
        item["group"] = group_of[item["image"]]
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return {
        "schema_version": 1,
        "producer": "app.ml.serving.mine-hard-negatives",
        "generated_at": datetime.now(UTC).isoformat(),
        "role": "TRAIN",
        "manifest": manifest.relative_to(PROJECT_ROOT).as_posix(),
        "manifest_sha256": sha256_file(manifest),
        "checkpoint_sha256": info["checkpoint_sha256"],
        "checkpoint_epoch": info["epoch"],
        "representation": representation,
        "score_threshold": score_threshold,
        "negative_images_scanned": len(rows),
        "images_with_candidate": len({item["image"] for item in candidates}),
        "candidates_by_predicted_class": dict(sorted(by_class.items())),
        "ground_truth_state": "XML RDD2022 válido sem objetos (NORMAL_NEGATIVE na origem)",
        "classification": "HARD_NEGATIVE_REVIEW_REQUIRED",
        "usage": "somente revisão humana; não altera rótulo, split nem amostragem",
        "candidates": candidates,
    }


def _evaluate_checkpoint_command(args: argparse.Namespace) -> int:
    """Avaliação de VALIDATION para screening/baseline; TEST não é alcançável aqui."""
    from app.ml.training import dataset_binding

    manifest_contract = load_model_config(args.manifest_contract or args.contract)
    manifest = dataset_binding(manifest_contract.get("dataset")).validation_manifest
    report = evaluate_checkpoint_for_role(
        args.checkpoint.resolve(),
        role="VALIDATION",
        confidence_threshold=args.confidence_threshold,
        nms_threshold=args.nms_threshold,
        contract_path=args.contract.resolve(),
        manifest_path=manifest,
        representation=args.representation,
    )
    report = {
        "schema_version": 1,
        "producer": "app.ml.serving.evaluate-checkpoint",
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "checkpoint": args.checkpoint.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "model_contract": args.contract.resolve().relative_to(PROJECT_ROOT).as_posix(),
        **report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {
        k: report[k]
        for k in ("label", "representation", "map50", "map50_95", "precision", "recall", "f1")
    }
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--checkpoint", choices=["best", "last"], default="best")
    export.add_argument("--contract", type=Path, default=MODEL_METADATA_PATH)
    export.add_argument("--representation", choices=["raw", "ema"], default="raw")
    export.add_argument("--parity-images", type=int, default=8)
    export.add_argument("--skip-validation", action="store_true")
    export.add_argument(
        "--recompute-validation",
        action="store_true",
        help="reavalia na GPU em vez de usar a validação do próprio run de treino",
    )
    sub.add_parser("evaluate", help="reavalia o modelo promovido na VALIDATION completa")
    evaluate_checkpoint = sub.add_parser(
        "evaluate-checkpoint",
        help="avalia um checkpoint na VALIDATION de um contrato (screening/baseline)",
    )
    evaluate_checkpoint.add_argument("--checkpoint", type=Path, required=True)
    evaluate_checkpoint.add_argument("--contract", type=Path, default=MODEL_METADATA_PATH)
    evaluate_checkpoint.add_argument(
        "--manifest-contract",
        type=Path,
        help="contrato cujo dataset define a VALIDATION (default: --contract)",
    )
    evaluate_checkpoint.add_argument("--representation", choices=["raw", "ema"], default="raw")
    evaluate_checkpoint.add_argument("--confidence-threshold", type=float, default=0.25)
    evaluate_checkpoint.add_argument("--nms-threshold", type=float, default=0.5)
    evaluate_checkpoint.add_argument("--label", required=True)
    evaluate_checkpoint.add_argument("--output", type=Path, required=True)
    select_point = sub.add_parser("select-operating-point")
    select_point.add_argument("--checkpoint", type=Path, required=True)
    select_point.add_argument("--contract", type=Path, required=True)
    select_point.add_argument("--quality-contract", type=Path, required=True)
    select_point.add_argument("--representation", choices=["raw", "ema"], required=True)
    select_point.add_argument("--batch-size", type=int, default=8)
    select_point.add_argument("--output", type=Path, required=True)
    mine = sub.add_parser(
        "mine-hard-negatives",
        help="lista negativas de TRAIN com disparo confiante, para revisão humana",
    )
    mine.add_argument("--checkpoint", type=Path, required=True)
    mine.add_argument("--contract", type=Path, required=True)
    mine.add_argument("--representation", choices=["raw", "ema"], default="ema")
    mine.add_argument("--score-threshold", type=float, default=0.5)
    mine.add_argument("--output", type=Path, required=True)
    sub.add_parser("benchmark", help="latência do ONNX promovido no hardware atual")
    lock = sub.add_parser("lock-operating-point")
    lock.add_argument("--export-manifest", type=Path, required=True)
    lock.add_argument("--contract", type=Path, required=True)
    lock.add_argument("--validation-report", type=Path, required=True)
    lock.add_argument("--quality-contract", type=Path, required=True)
    lock.add_argument("--training-run-id", required=True)
    lock.add_argument("--representation", choices=["raw", "ema"], required=True)
    lock.add_argument("--confidence-threshold", type=float, required=True)
    lock.add_argument("--nms-threshold", type=float, required=True)
    lock.add_argument("--output", type=Path, required=True)
    close = sub.add_parser(
        "close-model",
        help="fecha VALIDATION/operating point/TEST e produz o manifesto de promoção",
    )
    close.add_argument("--export-manifest", type=Path, required=True)
    close.add_argument("--output", type=Path, required=True)
    close.add_argument("--operating-point-lock", type=Path, required=True)
    close.add_argument("--benchmark-report", type=Path, required=True)
    close.add_argument("--batch-size", type=int, default=8)
    close.add_argument("--early-stop-approval", type=Path)
    register = sub.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--promote", action="store_true")
    register.add_argument(
        "--shadow-dev", action="store_true", help="autoriza somente este artifact no Urmind DEV"
    )
    reconcile = sub.add_parser(
        "reconcile-model-metadata",
        help="registra auditoria posterior de metadata reconciliada com o closure",
    )
    reconcile.add_argument("--model-version", required=True)
    reconcile.add_argument("--previous-nms", type=float, required=True)
    args = parser.parse_args(argv)

    if args.command in ("evaluate", "benchmark"):
        return _report_command(args.command)

    if args.command == "evaluate-checkpoint":
        return _evaluate_checkpoint_command(args)

    if args.command == "select-operating-point":
        report = select_operating_point_on_validation(
            args.checkpoint.resolve(),
            contract_path=args.contract.resolve(),
            representation=args.representation,
            quality_contract_path=args.quality_contract.resolve(),
            batch_size=args.batch_size,
        )
        _write_json_atomic(args.output, report)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "selection": report["operating_point_selection"],
                    "quality": report["quality_contract"],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "mine-hard-negatives":
        report = mine_hard_negative_candidates(
            args.checkpoint.resolve(),
            contract_path=args.contract.resolve(),
            representation=args.representation,
            score_threshold=args.score_threshold,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in report.items() if k != "candidates"}, indent=2))
        return 0

    if args.command == "lock-operating-point":
        return _lock_operating_point_command(args)

    if args.command == "close-model":
        return _close_model_command(args)

    if args.command == "register":
        import asyncio
        import selectors
        import sys

        coroutine = register_model(args.manifest, promote=args.promote, shadow_dev=args.shadow_dev)
        if sys.platform == "win32":
            result = asyncio.run(
                coroutine,
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            result = asyncio.run(coroutine)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "reconcile-model-metadata":
        result = _run_async(
            record_model_metadata_reconciliation(
                args.model_version,
                previous_nms=args.previous_nms,
            )
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    contract_path = args.contract.resolve()
    metadata = load_model_config(contract_path)
    from app.ml.training import dataset_binding

    validation_manifest = dataset_binding(metadata.get("dataset")).validation_manifest
    checkpoint = PROJECT_ROOT / metadata["training"]["output_directory"] / f"{args.checkpoint}.pt"
    if not checkpoint.is_file():
        raise SystemExit(f"checkpoint {args.checkpoint} ainda não existe")
    info = json.loads(json.dumps({"checkpoint": args.checkpoint}))
    staging = SERVING_DIR / f".{metadata['model_id']}.onnx.tmp"
    exported = export_onnx(
        checkpoint,
        staging,
        contract_path=contract_path,
        representation=args.representation,
    )
    final = SERVING_DIR / f"{metadata['model_id']}-{exported['onnx_sha256'][:12]}.onnx"
    staging.replace(final)
    parity = parity_check(
        checkpoint,
        final,
        validation_images(args.parity_images, manifest_path=validation_manifest),
        contract_path=contract_path,
        representation=args.representation,
    )
    if not parity["passed"]:
        final.unlink()
        raise SystemExit(f"paridade PyTorch x ONNX reprovada: {parity}")
    if args.skip_validation:
        metrics = None
    elif args.recompute_validation:
        selection_representation = metadata["training"].get("selection_representation", "raw")
        metrics = evaluate_checkpoint_for_role(
            checkpoint,
            role="VALIDATION",
            confidence_threshold=float(metadata["evaluation"]["confidence_threshold"]),
            nms_threshold=float(metadata["evaluation"]["nms_threshold"]),
            contract_path=contract_path,
            manifest_path=validation_manifest,
            representation=selection_representation,
        )
        metrics["mlflow_run_id"] = training_run_for(exported.get("config_fingerprint")).get(
            "mlflow_run_id"
        )
    else:
        selection_representation = metadata["training"].get("selection_representation", "raw")
        metrics = validation_metrics_from_training_run(
            exported["config_fingerprint"],
            exported["global_step"],
            exported["best_metric"],
            representation=selection_representation,
            selection_representation=selection_representation,
        )
    run = training_run_for(exported.get("config_fingerprint"), (metrics or {}).get("mlflow_run_id"))
    max_epoch = int(metadata["training"]["max_epoch"])
    latest_train_epoch = run.get("latest_train_epoch")
    completed_epochs = (
        int(latest_train_epoch) + 1
        if isinstance(latest_train_epoch, (int, float)) and not isinstance(latest_train_epoch, bool)
        else None
    )
    is_final = training_contract_complete(run, max_epoch=max_epoch)
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
        "checkpoint_selection": (
            {
                "source": "VALIDATION",
                "primary_metric": "map50_95",
                "value": metrics.get("map50_95"),
                "epoch": exported.get("epoch"),
                "global_step": exported.get("global_step"),
                "checkpoint_sha256": exported["checkpoint_sha256"],
            }
            if metrics is not None and args.checkpoint == "best"
            else None
        ),
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
