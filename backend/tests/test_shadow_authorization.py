"""Artifact-bound SHADOW_DEV authorization: the gate accepts exactly one candidate.

Evidence files are synthetic fixtures in tmp_path; nothing here touches the
Frozen Test, the real serving directory or the database.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.ml import serving
from app.ml.serving import (
    ShadowAuthorizationError,
    build_shadow_authorization,
    shadow_authorization_current,
    validate_shadow_authorization,
)

DEV = "impmeitwtusjtwjouggy"
CLASSES = ["URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    serving_dir = tmp_path / "models/serving"
    serving_dir.mkdir(parents=True)
    run_dir = tmp_path / "models/checkpoints/run"
    run_dir.mkdir(parents=True)
    onnx = serving_dir / "cand-aaaa.onnx"
    onnx.write_bytes(b"onnx-candidate")
    contract = tmp_path / "datasets/metadata/contract.json"
    contract.parent.mkdir(parents=True)
    contract.write_text('{"model_id": "cand"}')
    checkpoint = run_dir / "best.pt"
    checkpoint.write_bytes(b"checkpoint")
    calibration = tmp_path / "datasets/reports/calibration.json"
    calibration.parent.mkdir(parents=True)
    calibration.write_text('{"split": "VALIDATION"}')
    run_state = run_dir / "run_state.json"
    run_state.write_text(json.dumps({"status": "COMPLETED", "contract_sha256": _sha(contract)}))
    record = {
        "model_id": "cand",
        "architecture": "YOLOX-s",
        "stage": "final",
        "checkpoint": "best",
        "onnx_path": "models/serving/cand-aaaa.onnx",
        "onnx_sha256": _sha(onnx),
        "checkpoint_sha256": _sha(checkpoint),
        "model_contract_path": "datasets/metadata/contract.json",
        "model_contract_sha256": _sha(contract),
        "class_names": CLASSES,
        "input_size": [640, 640],
        "opset": 17,
        "validation_metrics": {"source": "VALIDATION", "map50": 0.1, "map50_95": 0.03},
        "parity": {"images": 8, "passed": True},
    }
    manifest = serving_dir / "cand-aaaa.json"
    manifest.write_text(json.dumps(record))
    monkeypatch.setattr(serving, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(serving, "SERVING_DIR", serving_dir)
    monkeypatch.setattr(
        serving, "load_model_config", lambda path: {"canonical_class_names": list(CLASSES)}
    )
    profile = {"sha256": "p" * 64, "calibration_sha256": None, "postprocess": {"nms": "per_class"}}
    monkeypatch.setattr(serving, "shadow_inference_profile", lambda *args: dict(profile))
    authorization = tmp_path / "datasets/metadata/shadow_authorizations/cand-aaaa.json"
    authorization.parent.mkdir(parents=True)
    authorization.write_text(
        json.dumps(
            build_shadow_authorization(
                manifest,
                run_state_path=run_state,
                calibration_path=calibration,
                authorized_by="test-owner",
                approval_evidence="fixture",
                purpose="supervised DEV pilot",
                dev_ref=DEV,
            )
        )
    )
    return {
        "manifest": manifest,
        "authorization": authorization,
        "record": record,
        "onnx": onnx,
        "run_state": run_state,
        "profile": profile,
        "serving_dir": serving_dir,
    }


def _edit(path: Path, **changes) -> None:
    document = json.loads(path.read_text())
    document.update(changes)
    path.write_text(json.dumps(document))


def test_matching_candidate_and_authorization_pass(tree):
    binding = validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)
    assert binding["authorization"]["production_approved"] is False
    assert binding["profile"]["sha256"] == "p" * 64
    assert binding["authorization_path"] == "datasets/metadata/shadow_authorizations/cand-aaaa.json"


def test_other_checkpoint_cannot_reuse_the_authorization(tree):
    other = tree["serving_dir"] / "other-bbbb.json"
    other.write_text(json.dumps({**tree["record"], "checkpoint_sha256": "b" * 64}))
    with pytest.raises(ShadowAuthorizationError, match="outro manifesto"):
        validate_shadow_authorization(tree["authorization"], other, dev_ref=DEV)
    # Rewriting the authorized manifest itself is caught by its hash.
    _edit(tree["manifest"], checkpoint_sha256="b" * 64)
    with pytest.raises(ShadowAuthorizationError, match="alterado"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_tampered_onnx_is_refused(tree):
    tree["onnx"].write_bytes(b"tampered")
    with pytest.raises(ShadowAuthorizationError, match="ONNX ausente ou alterado"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_class_order_divergent_from_contract_is_refused(tree, monkeypatch):
    monkeypatch.setattr(
        serving, "load_model_config", lambda path: {"canonical_class_names": CLASSES[::-1]}
    )
    with pytest.raises(ShadowAuthorizationError, match="ordem de classes"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_evidence_from_another_run_is_refused(tree):
    _edit(tree["run_state"], contract_sha256="c" * 64)
    with pytest.raises(ShadowAuthorizationError, match="estado do run alterado"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_changed_inference_profile_is_refused(tree, monkeypatch):
    monkeypatch.setattr(
        serving,
        "shadow_inference_profile",
        lambda *args: {**tree["profile"], "sha256": "q" * 64},
    )
    with pytest.raises(ShadowAuthorizationError, match="perfil de inferência"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_revoked_authorization_is_refused_at_registration_and_runtime(tree):
    reference = {
        "shadow_authorization": {
            "path": "datasets/metadata/shadow_authorizations/cand-aaaa.json",
            "sha256": _sha(tree["authorization"]),
        }
    }
    assert shadow_authorization_current(reference) is True
    _edit(tree["authorization"], revoked=True)
    with pytest.raises(ShadowAuthorizationError, match="revogada"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)
    assert shadow_authorization_current(reference) is False
    assert shadow_authorization_current({}) is False


def test_other_environment_is_refused(tree):
    with pytest.raises(ShadowAuthorizationError, match="outro ambiente"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref="prod-ref")


def test_shadow_authorization_is_never_production_approval(tree):
    _edit(tree["authorization"], production_approved=True)
    with pytest.raises(ShadowAuthorizationError, match="produção"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)


def test_missing_evidence_names_the_expected_file(tree):
    tree["run_state"].unlink()
    with pytest.raises(ShadowAuthorizationError, match="estado do run de treino ausente: models"):
        validate_shadow_authorization(tree["authorization"], tree["manifest"], dev_ref=DEV)
    with pytest.raises(ShadowAuthorizationError, match="autorização shadow ausente"):
        validate_shadow_authorization(
            tree["authorization"].with_name("absent.json"), tree["manifest"], dev_ref=DEV
        )


def test_authorization_builder_refuses_a_checkpoint_that_is_not_the_export(tree):
    (tree["run_state"].parent / "best.pt").write_bytes(b"other checkpoint")
    with pytest.raises(ShadowAuthorizationError, match="best.pt"):
        build_shadow_authorization(
            tree["manifest"],
            run_state_path=tree["run_state"],
            calibration_path=None,
            authorized_by="x",
            approval_evidence="x",
            purpose="x",
            dev_ref=DEV,
        )


@pytest.mark.asyncio
async def test_shadow_dev_without_authorization_file_is_refused(monkeypatch):
    from types import SimpleNamespace

    from app.ml.serving import register_model

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            supabase_url=f"https://{DEV}.supabase.co",
            # The runtime runs as the least-privilege role; registration is admin-only.
            database_pooler_url=f"postgresql://urmind_runtime.{DEV}@invalid.example/postgres",
            migration_database_url=f"postgresql://postgres.{DEV}@invalid.example/postgres",
        ),
    )
    with pytest.raises(ShadowAuthorizationError, match="--shadow-authorization"):
        await register_model(Path("absent.json"), promote=False, shadow_dev=True)
    with pytest.raises(ValueError, match="somente no Urmind DEV"):
        await register_model(Path("absent.json"), promote=True, shadow_dev=True)


@pytest.mark.asyncio
async def test_shadow_registration_refuses_a_non_admin_migration_identity(monkeypatch):
    from types import SimpleNamespace

    from app.ml.serving import register_model

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            supabase_url=f"https://{DEV}.supabase.co",
            database_pooler_url=f"postgresql://urmind_runtime.{DEV}@invalid.example/postgres",
            migration_database_url=f"postgresql://urmind_runtime.{DEV}@invalid.example/postgres",
        ),
    )
    with pytest.raises(ValueError, match="somente no Urmind DEV"):
        await register_model(Path("absent.json"), promote=False, shadow_dev=True)


@pytest.mark.parametrize(("letterbox", "expected"), [({"upscale": False}, False), ({}, True)])
def test_inference_profile_carries_letterbox_upscale_to_the_worker(
    monkeypatch, tmp_path, letterbox, expected
):
    """Same profile hash must mean same letterbox: an absent key would silently upscale."""
    from app.ml import browser_model

    manifest = {
        "inference_profile": {"sha256": "p" * 64, "calibration_sha256": None},
        "postprocess": {"nms": "per_class"},
        "input": {"letterbox": {"pad_value": 114, "anchor": "top-left", **letterbox}},
    }
    monkeypatch.setattr(
        browser_model, "build_browser_manifest", lambda *args, **kwargs: (manifest, None)
    )
    profile = serving.shadow_inference_profile(tmp_path / "record.json", None)
    assert profile["letterbox_upscale"] is expected


def test_cada_perfil_autorizado_tem_arquivo_proprio_e_nada_e_sobrescrito(monkeypatch, tmp_path):
    documents = iter(
        [
            {"artifact": {"inference_profile_sha256": "d2b6e1ab" + "0" * 56}},
            {"artifact": {"inference_profile_sha256": "2702eb15" + "0" * 56}},
            {"artifact": {"inference_profile_sha256": "2702eb15" + "0" * 56}},
        ]
    )
    monkeypatch.setattr(serving, "build_shadow_authorization", lambda *a, **k: next(documents))
    monkeypatch.setattr(serving, "SHADOW_AUTHORIZATION_DIR", tmp_path)
    monkeypatch.setattr(serving, "PROJECT_ROOT", tmp_path)
    argv = [
        "authorize-shadow",
        "--manifest",
        str(tmp_path / "model-v2.json"),
        "--run-state",
        str(tmp_path / "run_state.json"),
        "--authorized-by",
        "owner",
        "--approval-evidence",
        "chat",
        "--purpose",
        "shadow",
    ]
    assert serving.main(argv) == 0
    assert serving.main(argv) == 0
    assert sorted(p.name for p in tmp_path.glob("*.json")) == [
        "model-v2-profile-2702eb15.json",
        "model-v2-profile-d2b6e1ab.json",
    ]
    with pytest.raises(ShadowAuthorizationError, match="não sobrescrevo"):
        serving.main(argv)
