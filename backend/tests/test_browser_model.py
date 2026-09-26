"""Gerador do manifesto da detecção ao vivo: fail-closed, sem checkpoint nem treino."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.ml.browser_model import (
    BrowserPublishError,
    build_browser_manifest,
    main,
    publish,
)
from app.ml.serving import PROJECT_ROOT, _canonical_artifact_hash

CONTRACT = PROJECT_ROOT / "datasets" / "metadata" / "yolox_model_v1.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Path]:
    contract = tmp_path / "datasets" / "metadata" / "contract.json"
    contract.parent.mkdir(parents=True)
    shutil.copyfile(CONTRACT, contract)
    config = json.loads(contract.read_text(encoding="utf-8"))
    onnx = tmp_path / "models" / "serving" / "yolox-s-model-v1-abc.onnx"
    onnx.parent.mkdir(parents=True)
    onnx.write_bytes(b"onnx-sintetico-de-teste")
    record = {
        "stage": "baseline_early",
        "checkpoint_sha256": "1" * 64,
        "validation_metrics": {"map50_95": 0.1},
        "parity": {"passed": True},
        "onnx_path": "models/serving/yolox-s-model-v1-abc.onnx",
        "onnx_sha256": _sha(onnx),
        "model_contract_path": "datasets/metadata/contract.json",
        "model_contract_sha256": _sha(contract),
        "class_names": config["canonical_class_names"],
        "input_size": config["test_size"],
        "serving_score_threshold": 0.25,
    }
    record_path = onnx.with_suffix(".json")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return {"root": tmp_path, "record": record_path, "onnx": onnx, "contract": contract}


def _build(project: dict[str, Path], **overrides):
    kwargs = {
        "status": "EXPERIMENTAL",
        "authorize_use": True,
        "authorize_distribution": True,
        "authorization_ref": "docs/LIVE_DETECTION.md",
        "project_root": project["root"],
        **overrides,
    }
    return build_browser_manifest(project["record"], **kwargs)


def _rewrite(project: dict[str, Path], **changes) -> None:
    record = json.loads(project["record"].read_text(encoding="utf-8"))
    record.update(changes)
    project["record"].write_text(json.dumps(record), encoding="utf-8")


def test_manifesto_reproduz_o_contrato_do_export(project):
    manifest, onnx = _build(project)
    contract = json.loads(project["contract"].read_text(encoding="utf-8"))
    assert onnx == project["onnx"].resolve()
    assert manifest["scientific_status"] == "EXPERIMENTAL"
    assert manifest["class_names"] == contract["canonical_class_names"]
    assert manifest["input"]["size"] == contract["test_size"]
    assert manifest["input"]["color"] == "BGR"
    assert manifest["onnx"]["sha256"] == _sha(project["onnx"])
    assert manifest["onnx"]["path"].startswith("/models/") and ".." not in manifest["onnx"]["path"]
    assert manifest["postprocess"]["score_threshold"] == 0.25
    assert manifest["postprocess"]["nms_threshold"] == contract["evaluation"]["nms_threshold"]
    assert manifest["provenance"]["closure_manifest_sha256"] is None
    assert manifest["provenance"]["registration_manifest_sha256"] == _sha(project["record"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorize_use": False},
        {"authorize_distribution": False},
        {"authorization_ref": "  "},
        {"status": "REJECTED"},
        {"max_detections": 0},
    ],
)
def test_sem_autorizacao_explicita_nao_publica(project, overrides):
    with pytest.raises(BrowserPublishError):
        _build(project, **overrides)


def test_onnx_com_checksum_divergente_e_recusado(project):
    project["onnx"].write_bytes(b"outro-conteudo")
    with pytest.raises(BrowserPublishError, match="checksum"):
        _build(project)


def test_sem_paridade_ou_validation_e_recusado(project):
    _rewrite(project, parity={"passed": False})
    with pytest.raises(BrowserPublishError, match="paridade"):
        _build(project)
    _rewrite(project, parity={"passed": True}, validation_metrics=None)
    with pytest.raises(BrowserPublishError, match="VALIDATION"):
        _build(project)


def test_modelo_rejeitado_ou_contrato_alterado_e_recusado(project):
    _rewrite(project, quality_classification="REJECTED")
    with pytest.raises(BrowserPublishError, match="não aprovado"):
        _build(project)
    _rewrite(project, quality_classification=None, model_contract_sha256="0" * 64)
    with pytest.raises(BrowserPublishError, match="contrato"):
        _build(project)


def test_caminho_fora_do_projeto_e_recusado(project):
    _rewrite(project, onnx_path="../fora.onnx")
    with pytest.raises(BrowserPublishError, match="fora do projeto"):
        _build(project)


def test_classes_divergentes_sao_recusadas(project):
    _rewrite(project, class_names=["URMIND_ROAD_D00"])
    with pytest.raises(BrowserPublishError, match="classes"):
        _build(project)


def test_approved_exige_closure_do_mesmo_checkpoint_e_lock_aprovado(project):
    with pytest.raises(BrowserPublishError, match="APPROVED"):
        _build(project, status="APPROVED")
    closure = {"checkpoint": {"sha256": "1" * 64}}
    closure["closure_manifest_sha256"] = _canonical_artifact_hash(closure)
    closure_path = project["root"] / "closure.json"
    closure_path.write_text(json.dumps(closure), encoding="utf-8")
    _rewrite(
        project,
        operating_point_lock={
            "confidence_threshold": 0.4,
            "nms_threshold": 0.5,
            "quality_contract": {"decision": "APPROVED"},
        },
    )
    manifest, _ = _build(project, status="APPROVED", closure_path=closure_path)
    assert manifest["provenance"]["closure_manifest_sha256"] == closure["closure_manifest_sha256"]
    assert manifest["postprocess"]["score_threshold"] == 0.4

    closure["closure_manifest_sha256"] = "2" * 64
    closure_path.write_text(json.dumps(closure), encoding="utf-8")
    with pytest.raises(BrowserPublishError, match="adulterado"):
        _build(project, status="APPROVED", closure_path=closure_path)


def test_publicacao_atomica_confere_a_copia(project, tmp_path):
    manifest, onnx = _build(project)
    output = tmp_path / "public" / "models"
    written = publish(manifest, onnx, output)
    assert json.loads(written.read_text(encoding="utf-8")) == manifest
    published = output / Path(manifest["onnx"]["path"]).name
    assert _sha(published) == manifest["onnx"]["sha256"]
    assert not list(output.glob("*.part"))


def test_cli_bloqueia_sem_autorizacao(project):
    with pytest.raises(SystemExit, match="BROWSER_MODEL_PUBLISH_BLOCKED"):
        main(
            [
                "--manifest",
                str(project["record"]),
                "--status",
                "EXPERIMENTAL",
                "--authorization-ref",
                "x",
                "--dry-run",
            ]
        )


def _calibration(project: dict[str, Path], **changes) -> Path:
    classes = json.loads(project["contract"].read_text(encoding="utf-8"))["canonical_class_names"]
    validation = project["root"] / "datasets" / "manifests" / "validation.jsonl"
    validation.parent.mkdir(parents=True, exist_ok=True)
    validation.write_text('{"split": "VALIDATION"}', encoding="utf-8")
    document = {
        "split": "VALIDATION",
        "test_accessed": False,
        "validation_manifest": {
            "path": "datasets/manifests/validation.jsonl",
            "sha256": _sha(validation),
        },
        "onnx_sha256": _sha(project["onnx"]),
        "nms": "per_class",
        "nms_threshold": 0.45,
        "class_score_thresholds": dict(zip(classes, (0.05, 0.13, 0.35, 0.13), strict=True)),
        **changes,
    }
    path = project["root"] / "datasets" / "reports" / "calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_calibracao_define_nms_e_limiar_por_classe(project):
    calibration = _calibration(project)
    manifest, _ = _build(project, calibration_path=calibration)
    assert manifest["inference_profile"]["calibration_sha256"] == _sha(calibration)
    assert manifest["postprocess"] == {
        "score_threshold": 0.05,
        "nms_threshold": 0.45,
        "nms": "per_class",
        "max_detections": 100,
        "class_score_thresholds": [0.05, 0.13, 0.35, 0.13],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"onnx_sha256": "f" * 64},
        {"split": "TEST"},
        {"test_accessed": True},
        {
            "validation_manifest": {
                "path": "datasets/manifests/validation.jsonl",
                "sha256": "0" * 64,
            }
        },
        {"validation_manifest": None},
        {"nms": "soft"},
        {"nms_threshold": 0},
        {"class_score_thresholds": {"URMIND_ROAD_D00": 0.1}},
    ],
)
def test_calibracao_invalida_e_recusada(project, changes):
    with pytest.raises(BrowserPublishError):
        _build(project, calibration_path=_calibration(project, **changes))


def test_perfil_de_inferencia_muda_com_limiar_ou_nms(project):
    base, _ = _build(project)
    calibrated, _ = _build(project, calibration_path=_calibration(project))
    assert base["inference_profile"]["calibration_sha256"] is None
    assert base["onnx"]["sha256"] == calibrated["onnx"]["sha256"]
    assert base["inference_profile"]["sha256"] != calibrated["inference_profile"]["sha256"]
    again, _ = _build(project, calibration_path=_calibration(project))
    assert again["inference_profile"] == calibrated["inference_profile"]


def test_calibracao_sem_ampliar_entra_no_manifesto_e_no_perfil(project):
    ampliando, _ = _build(project, calibration_path=_calibration(project))
    sem_ampliar, _ = _build(
        project, calibration_path=_calibration(project, letterbox_upscale=False)
    )
    assert "upscale" not in ampliando["input"]["letterbox"]
    assert sem_ampliar["input"]["letterbox"] == {
        "pad_value": 114,
        "anchor": "top-left",
        "upscale": False,
    }
    assert sem_ampliar["inference_profile"]["sha256"] != ampliando["inference_profile"]["sha256"]
    with pytest.raises(BrowserPublishError, match="letterbox_upscale"):
        _build(project, calibration_path=_calibration(project, letterbox_upscale="nao"))


class _Response:
    def __init__(self, status_code, content=b"", payload=None):
        self.status_code, self.content, self._payload = status_code, content, payload or {}

    def json(self):
        return self._payload


class _FakeStorage:
    def __init__(self, *, bucket=None, stored=None, serve=None):
        self.bucket, self.stored, self.serve, self.calls = bucket, stored, serve, []

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if url.endswith("/bucket/models"):
            return _Response(200, payload=self.bucket) if self.bucket else _Response(404)
        body = self.serve if self.serve is not None else self.stored
        return _Response(200, body) if body is not None else _Response(400)

    def post(self, url, headers=None, json=None, content=None):
        self.calls.append(("POST", url))
        if url.endswith("/bucket"):
            assert json["public"] is True and json["id"] == "models"
            self.bucket = {"public": True}
            return _Response(200)
        assert headers["x-upsert"] == "false"
        if self.stored is not None:
            return _Response(409)
        self.stored = content
        return _Response(200)


def _storage_setup(tmp_path, *, distribution=True):
    from app.ml.browser_model import storage_object_path

    weights = b"onnx-de-teste"
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(weights)
    manifest = {
        "schema_version": 1,
        "model_version": "model-v2-abc",
        "scientific_status": "EXPERIMENTAL",
        "use_authorized": True,
        "distribution_authorized": distribution,
        "onnx": {"path": "/models/m.onnx", "sha256": _sha(onnx), "size_bytes": len(weights)},
        "class_names": ["URMIND_ROAD_D00"],
        "inference_profile": {"sha256": "p" * 64},
    }
    path = tmp_path / "live-detection.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    settings = type(
        "S", (), {"supabase_url": "https://ref.supabase.co", "supabase_secret_key": "k"}
    )()
    return path, onnx, settings, weights, storage_object_path(manifest)


def test_publica_no_bucket_publico_imutavel_e_confere_o_hash(tmp_path):
    from app.ml.browser_model import publish_to_storage

    path, onnx, settings, weights, object_path = _storage_setup(tmp_path)
    storage = _FakeStorage()
    result = publish_to_storage(path, onnx, settings, storage)
    assert result["uploaded"] is True and storage.stored == weights
    assert result["object_path"] == object_path == f"model-v2-abc/{result['sha256']}/m.onnx"
    assert result["public_url"].endswith(f"/object/public/models/{object_path}")
    assert result["inference_profile_sha256"] == "p" * 64
    again = publish_to_storage(path, onnx, settings, storage)
    assert again["uploaded"] is False  # verified in place, never overwritten


@pytest.mark.parametrize(
    ("storage", "distribution", "message"),
    [
        (_FakeStorage(bucket={"public": False}), True, "não é público"),
        (_FakeStorage(serve=b"outro-conteudo"), True, "não confere"),
        (_FakeStorage(), False, "distribuição"),
    ],
)
def test_publicacao_recusa_bucket_privado_objeto_divergente_e_sem_autorizacao(
    tmp_path, storage, distribution, message
):
    from app.ml.browser_model import publish_to_storage

    path, onnx, settings, _, _ = _storage_setup(tmp_path, distribution=distribution)
    with pytest.raises(BrowserPublishError, match=message):
        publish_to_storage(path, onnx, settings, storage)


def _yolox_like_onnx(path: Path) -> None:
    """Conv (corpo) -> Transpose (cabeça) -> Exp/Mul (decodificação), E/S FP32."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(0)
    graph = helper.make_graph(
        [
            helper.make_node("Conv", ["images", "w"], ["features"], name="/backbone/Conv"),
            helper.make_node(
                "Transpose", ["features"], ["t"], name="/head/Transpose", perm=[0, 2, 3, 1]
            ),
            helper.make_node("Exp", ["t"], ["e"], name="/head/Exp"),
            helper.make_node("Mul", ["e", "stride"], ["output"], name="/head/Mul"),
        ],
        "yolox_like",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 8, 8, 4])],
        [
            numpy_helper.from_array(
                (rng.standard_normal((4, 3, 1, 1)) * 0.1).astype(np.float32), "w"
            ),
            numpy_helper.from_array(np.array([32.0], dtype=np.float32), "stride"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, str(path))


def test_float16_mantem_es_e_decodificacao_em_fp32_e_o_resultado(tmp_path):
    pytest.importorskip("onnxconverter_common")
    import numpy as np
    import onnx
    import onnxruntime as ort

    from app.ml.browser_model import float16_onnx

    source, target = tmp_path / "m.onnx", tmp_path / "m-fp16.onnx"
    _yolox_like_onnx(source)
    float16_onnx(source, target)
    model = onnx.load(str(target))
    dtypes = {init.name: init.data_type for init in model.graph.initializer}
    assert dtypes["w"] == onnx.TensorProto.FLOAT16  # corpo em FP16
    assert dtypes["stride"] == onnx.TensorProto.FLOAT  # decodificação em FP32
    for value in (*model.graph.input, *model.graph.output):
        assert value.type.tensor_type.elem_type == onnx.TensorProto.FLOAT
    images = np.random.default_rng(1).uniform(0, 255, (1, 3, 8, 8)).astype(np.float32) / 255
    run = lambda p: ort.InferenceSession(str(p), providers=["CPUExecutionProvider"]).run(
        None, {"images": images}
    )[0]
    np.testing.assert_allclose(run(target), run(source), rtol=2e-3)


def test_manifesto_float16_troca_o_onnx_e_o_perfil(project, tmp_path):
    pytest.importorskip("onnxconverter_common")
    from app.ml.browser_model import inference_profile_sha256, with_float16

    manifest, _ = _build(project)
    source = tmp_path / "real.onnx"
    _yolox_like_onnx(source)
    converted, onnx_path = with_float16(manifest, source, tmp_path / "out")
    assert converted["onnx"]["sha256"] == _sha(onnx_path)
    assert converted["onnx"]["source_sha256"] == manifest["onnx"]["sha256"]
    assert converted["onnx"]["precision"] == "float16"
    assert converted["onnx"]["path"] == f"/models/{onnx_path.name}"
    assert onnx_path.name.startswith(f"{manifest['model_version']}-fp16-")
    assert converted["model_version"] == manifest["model_version"]
    assert converted["inference_profile"]["sha256"] == inference_profile_sha256(converted)
    assert converted["inference_profile"]["sha256"] != manifest["inference_profile"]["sha256"]
    assert manifest["onnx"]["sha256"] != converted["onnx"]["sha256"]  # original intacto
