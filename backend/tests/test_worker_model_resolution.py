from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ml.serving import ModelNotAvailableError
from app.models.core import ModelVersion
from app.repositories.core import InferenceRepository
from app.worker import Worker


def _model(**serving_overrides):
    serving = {
        "onnx_path": "models/serving/model.onnx",
        "model_contract_path": "datasets/metadata/yolox_model_v1.json",
        "model_contract_sha256": "b" * 64,
        "score_threshold": 0.2,
        "nms_threshold": 0.65,
        "input_size": [640, 640],
        "class_names": [
            "URMIND_ROAD_D00",
            "URMIND_ROAD_D10",
            "URMIND_ROAD_D20",
            "URMIND_ROAD_D40",
        ],
        **serving_overrides,
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        checksum="a" * 64,
        metrics={"serving": serving},
    )


@pytest.mark.asyncio
async def test_worker_only_archives_after_analysis_and_resumes_without_reinference(monkeypatch):
    from app import worker as worker_module

    capture_id = uuid.uuid4()
    event_id = uuid.uuid4()
    capture = SimpleNamespace(
        source="pwa_photo",
        point="fixture-point",
        quality={"inference": {"status": "detection_completed", "event_ids": [str(event_id)]}},
    )
    actions: list[str] = []
    job = {"msg_id": 7, "message": {"capture_id": str(capture_id)}, "read_ct": 2}
    assessment = SimpleNamespace(
        factors={"phase4_snapshot": {"features": {}}, "decision_trace": {"rules": []}}
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            actions.append("commit")

    class Database:
        @staticmethod
        def sessionmaker():
            return Session()

    class Queue:
        def __init__(self, _session):
            pass

        async def read_job(self, _timeout):
            return job

        async def archive_job(self, _id):
            actions.append("archive")

        async def set_capture_inference(self, item, state):
            item.quality["inference"] = state
            actions.append(state["status"])

    class Captures:
        def __init__(self, _session):
            pass

        async def get(self, _id):
            return capture

    class Events:
        def __init__(self, _session):
            pass

        async def get(self, _id):
            return SimpleNamespace(id=event_id)

    class Decisions:
        def __init__(self, _session):
            pass

        async def latest_risk(self, _id):
            return assessment

    class Service:
        def __init__(self, _captures, events, _decisions):
            self.events = events

    monkeypatch.setattr(worker_module, "InferenceRepository", Queue)
    monkeypatch.setattr(worker_module, "CaptureRepository", Captures)
    monkeypatch.setattr(worker_module, "EventRepository", Events)
    monkeypatch.setattr(worker_module, "DecisionRepository", Decisions)
    monkeypatch.setattr(worker_module, "CoreService", Service)
    worker = Worker(Database(), SimpleNamespace())

    async def mark_context(_event_id, _bound, _capture_id):
        actions.append("context")
        return True

    monkeypatch.setattr(worker, "enrich_event", mark_context)
    assert await worker.process_one() is True
    assert "processing_detection" not in actions
    assert actions.index("archive") > actions.index("analysis_completed")
    assert actions.index("context") < actions.index("analysis_completed")
    assert (
        actions.index("enriching_context")
        < actions.index("context")
        < actions.index("building_features")
        < actions.index("analysis_completed")
    )

    # A crash after the feature stage resumes from saved event_ids, without
    # re-running detection.
    capture.quality["inference"] = {"status": "building_features", "event_ids": [str(event_id)]}
    actions.clear()
    assert await worker.process_one() is True
    assert "processing_detection" not in actions
    assert capture.quality["inference"]["status"] == "analysis_completed"

    capture.quality["inference"] = {"status": "assessing", "event_ids": [str(event_id)]}
    job["read_ct"] = 3
    actions.clear()

    async def broken_analysis(_capture_id, _event_ids, _bound):
        raise RuntimeError("assessment unavailable")

    monkeypatch.setattr(worker, "_finish_analysis", broken_analysis)
    assert await worker.process_one() is True
    assert capture.quality["inference"]["status"] == "inference_failed"
    assert actions.index("archive") > actions.index("inference_failed")

    capture.quality["inference"] = {"status": "detection_completed", "event_ids": []}
    actions.clear()
    assert await worker.process_one() is True
    assert capture.quality["inference"]["status"] == "no_supported_detection"
    assert actions.index("archive") > actions.index("no_supported_detection")

    for terminal_status in ("no_detection", "no_supported_detection"):
        capture.quality["inference"] = {"status": terminal_status}
        actions.clear()
        assert await worker.process_one() is True
        assert [action for action in actions if action != "commit"] == ["archive"]
        assert capture.quality["inference"]["status"] == terminal_status

    capture.quality["inference"] = {
        "status": "detection_completed",
        "event_ids": [],
        "detections": 2,
    }
    actions.clear()
    assert await worker.process_one() is True
    assert capture.quality["inference"]["status"] == "no_event"
    assert actions.index("archive") > actions.index("no_event")


def test_worker_resolve_thresholds_somente_do_model_version(monkeypatch) -> None:
    captured = {}

    class FakeDetector:
        def __init__(self, path, checksum, **contract):
            captured.update(path=path, checksum=checksum, **contract)

    monkeypatch.setattr("app.worker.OnnxDetector", FakeDetector)
    worker = Worker(SimpleNamespace(), SimpleNamespace())

    worker._detector_for(_model())

    assert worker._score_threshold == 0.2
    assert captured["nms_threshold"] == 0.65
    assert captured["expected_input_size"] == (640, 640)
    assert captured["expected_class_names"] == (
        "URMIND_ROAD_D00",
        "URMIND_ROAD_D10",
        "URMIND_ROAD_D20",
        "URMIND_ROAD_D40",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("score_threshold", None),
        ("nms_threshold", None),
        ("nms_threshold", True),
        ("input_size", None),
        ("input_size", [640.5, 640]),
        ("class_names", None),
        ("model_contract_path", None),
        ("model_contract_sha256", None),
    ],
)
def test_worker_recusa_contrato_de_serving_incompleto(field, value) -> None:
    worker = Worker(SimpleNamespace(), SimpleNamespace())

    with pytest.raises(ModelNotAvailableError, match="contrato"):
        worker._detector_for(_model(**{field: value}))


def test_worker_repassa_perfil_por_classe_do_model_version(monkeypatch) -> None:
    captured = {}

    class FakeDetector:
        def __init__(self, path, checksum, **contract):
            captured.update(contract)

    monkeypatch.setattr("app.worker.OnnxDetector", FakeDetector)
    Worker(SimpleNamespace(), SimpleNamespace())._detector_for(_model())
    assert captured["nms_mode"] == "class_agnostic"
    assert captured["class_score_thresholds"] is None

    thresholds = [0.05, 0.13, 0.35, 0.13]
    Worker(SimpleNamespace(), SimpleNamespace())._detector_for(
        _model(nms="per_class", class_score_thresholds=thresholds)
    )
    assert captured["nms_mode"] == "per_class"
    assert captured["class_score_thresholds"] == thresholds


@pytest.mark.parametrize("value", ["0.1", [0.1, True, 0.2, 0.3], {"D00": 0.1}])
def test_worker_recusa_limiares_por_classe_malformados(value) -> None:
    worker = Worker(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(ModelNotAvailableError, match="limiares por classe"):
        worker._detector_for(_model(nms="per_class", class_score_thresholds=value))


def test_onnx_detector_por_classe_usa_limiar_proprio_e_nms_dentro_da_classe() -> None:
    import numpy as np

    from app.ml.serving import OnnxDetector

    detector = object.__new__(OnnxDetector)
    detector.class_names = ["D00", "D10", "D20", "D40"]
    detector.nms_threshold = 0.45
    detector.nms_mode = "per_class"
    detector.class_score_thresholds = (0.05, 0.13, 0.35, 0.13)
    rows = np.array(
        [
            # cx, cy, w, h, obj, D00, D10, D20, D40
            [100, 100, 50, 50, 1, 0.9, 0, 0, 0],
            [101, 100, 50, 50, 1, 0.7, 0, 0, 0],  # mesma classe, sobreposta: suprimida
            [102, 101, 50, 50, 1, 0, 0.6, 0, 0],  # outra classe: mantida
            [300, 300, 20, 20, 1, 0, 0, 0.3, 0],  # D20 0,3 < 0,35: cai
            [400, 400, 20, 20, 1, 0.1, 0, 0, 0],  # D00 0,1 > 0,05: passa
        ],
        dtype=np.float32,
    )[None]
    served = detector.postprocess(rows, 1.0, (640, 640, 3), score_threshold=0.25)
    assert sorted((d.urmind_class, round(d.confidence, 3)) for d in served) == [
        ("D00", 0.1),
        ("D00", 0.9),
        ("D10", 0.6),
    ]


def test_worker_recusa_onnx_fora_do_serving():
    worker = Worker(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(ModelNotAvailableError, match="fora do diretório"):
        worker._detector_for(_model(onnx_path="../datasets/metadata/model.onnx"))


@pytest.mark.asyncio
async def test_promocao_remove_outra_versao_da_mesma_funcao(monkeypatch) -> None:
    validated = []

    def verify(model, dataset):
        validated.append((model, dataset))

    monkeypatch.setattr("app.ml.serving.validate_registered_model_evidence", verify)
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), get=AsyncMock())
    repository = InferenceRepository(session)
    model = ModelVersion(
        id=uuid.uuid4(),
        name="yolox-s-model-v1",
        kind="vision",
        version="final",
        checksum="a" * 64,
        metrics={
            "quality_classification": "APPROVED",
            "frozen_test_quality": {"passed": True},
            "benchmark": {"passed": True},
            "closure_artifact": {"path": "models/serving/closure.json", "sha256": "a" * 64},
        },
        promoted_at=None,
    )
    promoted_at = datetime(2026, 9, 20, tzinfo=UTC)

    await repository.promote_exclusive(model, promoted_at=promoted_at)

    assert session.execute.await_count == 2
    lock_statement = str(session.execute.await_args_list[0].args[0])
    statement = str(session.execute.await_args_list[1].args[0])
    assert "pg_advisory_xact_lock" in lock_statement
    assert "UPDATE public.model_versions" in statement
    assert "model_versions.kind" in statement
    assert "model_versions.id !=" in statement
    assert model.promoted_at == promoted_at
    session.flush.assert_awaited_once()
    assert validated and validated[0][0] is model


@pytest.mark.asyncio
@pytest.mark.parametrize("classification", ["WEAK", "REJECTED", None])
async def test_promocao_direta_recusa_modelo_sem_qualidade_aprovada(classification) -> None:
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())
    model = ModelVersion(
        id=uuid.uuid4(),
        name="yolox-s-model-v1",
        kind="vision",
        version="historical",
        checksum="a" * 64,
        metrics={"quality_classification": classification},
    )

    with pytest.raises(ValueError, match="quality gate"):
        await InferenceRepository(session).promote_exclusive(
            model, promoted_at=datetime(2026, 9, 20, tzinfo=UTC)
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_registro_previo_recebe_fechamento_validado_antes_da_promocao() -> None:
    dataset_id = uuid.uuid4()
    existing = ModelVersion(
        id=uuid.uuid4(),
        name="yolox-s-model-v1",
        kind="vision",
        version="final",
        checksum="a" * 64,
        dataset_version_id=dataset_id,
        metrics={},
        promoted_at=None,
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: existing)
    session = SimpleNamespace(execute=AsyncMock(return_value=result), flush=AsyncMock())
    approved = {"quality_classification": "APPROVED"}

    resolved = await InferenceRepository(session).register_model(
        refresh_unpromoted=True,
        name=existing.name,
        kind=existing.kind,
        version=existing.version,
        checksum=existing.checksum,
        dataset_version_id=dataset_id,
        metrics=approved,
    )

    assert resolved is existing
    assert existing.metrics == approved
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolucao_recusa_multiplos_modelos_promovidos() -> None:
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [object(), object()]))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(RuntimeError, match="múltiplos modelos vision promovidos"):
        await InferenceRepository(session).promoted_vision_model()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1])
async def test_resolucao_de_zero_ou_um_modelo_promovido(count: int, monkeypatch) -> None:
    monkeypatch.setattr("app.ml.serving.validate_registered_model_evidence", lambda *_: None)
    models = [
        SimpleNamespace(
            dataset_version_id=uuid.uuid4(),
            operational_status="PRODUCTION_APPROVED",
            metrics={
                "quality_classification": "APPROVED",
                "frozen_test_quality": {"passed": True},
                "benchmark": {"passed": True},
                "closure_artifact": {"path": "models/serving/closure.json", "sha256": "a" * 64},
            },
        )
        for _ in range(count)
    ]
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: models))
    session = SimpleNamespace(execute=AsyncMock(return_value=result), get=AsyncMock())

    resolved = await InferenceRepository(session).promoted_vision_model()

    assert resolved is (models[0] if models else None)
    assert "LIMIT" in str(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_resolucao_recusa_closure_real_divergente(monkeypatch) -> None:
    def reject(*_args):
        raise ValueError("ONNX alterado")

    monkeypatch.setattr("app.ml.serving.validate_registered_model_evidence", reject)
    model = SimpleNamespace(
        dataset_version_id=uuid.uuid4(),
        operational_status="PRODUCTION_APPROVED",
        metrics={
            "quality_classification": "APPROVED",
            "frozen_test_quality": {"passed": True},
            "benchmark": {"passed": True},
            "closure_artifact": {"path": "models/serving/closure.json", "sha256": "a" * 64},
        },
    )
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [model]))
    session = SimpleNamespace(execute=AsyncMock(return_value=result), get=AsyncMock())

    with pytest.raises(RuntimeError, match="sem closure íntegro"):
        await InferenceRepository(session).promoted_vision_model()


@pytest.mark.asyncio
async def test_resolucao_recusa_modelo_legado_sem_quality_gate() -> None:
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [SimpleNamespace(metrics={})])
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(RuntimeError, match="sem quality gate aprovado"):
        await InferenceRepository(session).promoted_vision_model()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (
            {
                "shadow_authorized": True,
                "serving_status": "EXPERIMENTAL_SHADOW",
                "quality_classification": "EXPERIMENTAL",
                "shadow_scope": "URMIND_DEV_ONLY",
                "shadow_project_ref": "impmeitwtusjtwjouggy",
            },
            True,
        ),
        ({"shadow_authorized": False}, False),
        ({"shadow_authorized": True, "quality_classification": "REJECTED"}, False),
        (
            {
                "shadow_authorized": True,
                "serving_status": "EXPERIMENTAL_SHADOW",
                "quality_classification": "REJECTED",
                "shadow_scope": "URMIND_DEV_ONLY",
                "shadow_project_ref": "impmeitwtusjtwjouggy",
            },
            True,
        ),
        ({"shadow_authorized": True, "quality_classification": "BLOCKED_DATA"}, False),
        ({"shadow_authorized": True, "quality_classification": "FAILED"}, False),
        ({"shadow_authorized": True, "quality_classification": "WEAK"}, False),
    ],
)
async def test_shadow_model_requires_explicit_dev_authorization(metrics, expected, monkeypatch):
    # The artifact-bound authorization file is covered in test_shadow_authorization.py.
    monkeypatch.setattr("app.ml.serving.shadow_authorization_current", lambda metrics: True)
    model_id = uuid.uuid4()
    dataset_id = uuid.uuid4()
    model = ModelVersion(
        id=model_id,
        kind="vision",
        promoted_at=None,
        dataset_version_id=dataset_id,
        checksum="a" * 64,
        metrics=metrics,
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[model, object()]))
    resolved = await InferenceRepository(session).shadow_vision_model(model_id)
    assert (resolved is model) is expected


@pytest.mark.asyncio
async def test_shadow_model_with_revoked_or_missing_authorization_is_not_resolved(monkeypatch):
    monkeypatch.setattr("app.ml.serving.shadow_authorization_current", lambda metrics: False)
    model = ModelVersion(
        id=uuid.uuid4(),
        kind="vision",
        promoted_at=None,
        dataset_version_id=uuid.uuid4(),
        checksum="a" * 64,
        metrics={
            "shadow_authorized": True,
            "serving_status": "EXPERIMENTAL_SHADOW",
            "quality_classification": "EXPERIMENTAL",
            "shadow_scope": "URMIND_DEV_ONLY",
            "shadow_project_ref": "impmeitwtusjtwjouggy",
        },
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[model, object()]))
    assert await InferenceRepository(session).shadow_vision_model(model.id) is None


def test_scientific_rejection_remains_distinct_from_dev_shadow_status():
    model = ModelVersion(
        name="quality-rebuild",
        kind="vision",
        version="demo",
        checksum="a" * 64,
        metrics={
            "quality_classification": "REJECTED",
            "shadow_authorized": True,
            "serving_status": "EXPERIMENTAL_SHADOW",
        },
        promoted_at=None,
    )
    assert model.operational_status == "EXPERIMENTAL_SHADOW"
    assert model.metrics["quality_classification"] == "REJECTED"
    model.metrics = {"quality_classification": "REJECTED"}
    assert model.operational_status == "REJECTED"


@pytest.mark.asyncio
async def test_dev_shadow_registration_refuses_other_project_before_database(monkeypatch, tmp_path):
    from app.ml.serving import register_model

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            supabase_url="https://other-project.supabase.co",
            database_pooler_url="postgresql://postgres.other-project@invalid.example/postgres",
        ),
    )
    manifest = tmp_path / "absent-manifest.json"
    assert not manifest.exists()
    with pytest.raises(ValueError, match="somente no Urmind DEV"):
        await register_model(manifest, promote=False, shadow_dev=True)


@pytest.mark.asyncio
async def test_production_resolution_fails_closed_on_invalid_promoted_model(monkeypatch):
    repository = InferenceRepository(SimpleNamespace())
    monkeypatch.setattr(
        repository,
        "promoted_vision_model",
        AsyncMock(side_effect=RuntimeError("closure inválido")),
    )
    assert await repository.configured_vision_model("production", None) is None
    assert await repository.configured_vision_model("disabled", None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["ARCHIVED", "QUARANTINED"])
async def test_production_refuses_inactive_lifecycle_even_with_promoted_at(lifecycle):
    model = ModelVersion(
        name="old",
        kind="vision",
        version="old",
        promoted_at=datetime.now(UTC),
        metrics={
            "quality_classification": "APPROVED",
            "frozen_test_quality": {"passed": True},
            "benchmark": {"passed": True},
            "closure_artifact": {"path": "models/serving/closure.json", "sha256": "a" * 64},
            "lifecycle_status": lifecycle,
        },
    )
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [model]))
    repository = InferenceRepository(SimpleNamespace(execute=AsyncMock(return_value=result)))
    with pytest.raises(RuntimeError, match="arquivado ou em quarentena"):
        await repository.promoted_vision_model()


def test_preprocess_sem_ampliar_mantem_escala_1_e_nao_muda_imagem_grande() -> None:
    import numpy as np

    from app.ml.serving import _preprocess

    small = np.random.default_rng(0).integers(0, 255, (512, 384, 3), dtype=np.uint8)
    tensor, ratio = _preprocess(small, (640, 640), upscale=False)
    assert ratio == 1.0 and tensor.shape == (1, 3, 640, 640)
    assert np.array_equal(tensor[0, :, :512, :384], small.transpose(2, 0, 1).astype(np.float32))
    assert (tensor[0, :, 512:, :] == 114).all() and (tensor[0, :, :, 384:] == 114).all()
    _, stretched = _preprocess(small, (640, 640))
    assert stretched == 1.25

    large = np.random.default_rng(1).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    a, ra = _preprocess(large, (640, 640), upscale=False)
    b, rb = _preprocess(large, (640, 640))
    assert ra == rb and np.array_equal(a, b)


def test_worker_repassa_letterbox_sem_ampliar(monkeypatch) -> None:
    captured = {}

    class FakeDetector:
        def __init__(self, path, checksum, **contract):
            captured.update(contract)

    monkeypatch.setattr("app.worker.OnnxDetector", FakeDetector)
    Worker(SimpleNamespace(), SimpleNamespace())._detector_for(_model())
    assert captured["letterbox_upscale"] is True
    Worker(SimpleNamespace(), SimpleNamespace())._detector_for(_model(letterbox_upscale=False))
    assert captured["letterbox_upscale"] is False
    with pytest.raises(ModelNotAvailableError, match="letterbox_upscale"):
        Worker(SimpleNamespace(), SimpleNamespace())._detector_for(_model(letterbox_upscale="no"))



def test_perfil_versionado_sem_letterbox_explicito_falha_fechado(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.OnnxDetector", lambda *args, **kwargs: None)
    with pytest.raises(ModelNotAvailableError, match="letterbox_upscale explícito"):
        Worker(SimpleNamespace(), SimpleNamespace())._detector_for(
            _model(inference_profile_sha256="p" * 64)
        )
    # With the key stated, the same profile loads.
    Worker(SimpleNamespace(), SimpleNamespace())._detector_for(
        _model(inference_profile_sha256="p" * 64, letterbox_upscale=False)
    )
