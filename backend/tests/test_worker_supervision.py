"""Supervised local Worker: heartbeat file and graceful stop between jobs."""

import json
import uuid
from types import SimpleNamespace

import pytest

from app import worker as worker_module
from app.worker import Supervision


def _fake_worker():
    return SimpleNamespace(_detector_version=uuid.UUID(int=7), _inference_profile_sha256="p" * 64)


def test_heartbeat_registra_pid_modelo_perfil_e_ultimo_job(tmp_path):
    supervision = Supervision(tmp_path)
    supervision.beat(_fake_worker(), processed=True)
    supervision.beat(_fake_worker(), processed=False, error="StorageError")
    state = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert state["queue"] == "inference_jobs"
    assert state["jobs_processed"] == 1 and state["iteration_errors"] == 1
    assert state["last_error"] == "StorageError" and state["last_job_at"]
    assert state["model_version_id"] == str(uuid.UUID(int=7))
    assert state["inference_profile_sha256"] == "p" * 64
    assert not (tmp_path / "heartbeat.json.tmp").exists()


def test_sem_diretorio_nao_escreve_nada_e_nunca_para():
    supervision = Supervision(None)
    supervision.beat(_fake_worker(), processed=True)
    assert supervision.jobs == 1 and not supervision.stop_requested()


@pytest.mark.asyncio
async def test_stop_request_encerra_o_loop_entre_jobs(tmp_path, monkeypatch):
    processed = []

    class FakeWorker:
        _detector_version = None
        _inference_profile_sha256 = None

        def __init__(self, *args):
            pass

        async def process_pending_address(self):
            return False

        async def process_one(self):
            processed.append(True)
            if len(processed) == 2:
                (tmp_path / "stop.request").write_text("", encoding="utf-8")
            return True

    closed = []

    class FakeDatabase:
        def __init__(self, *args):
            pass

        async def close(self):
            closed.append(True)

    monkeypatch.setenv("URMIND_WORKER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(worker_module, "Worker", FakeWorker)
    monkeypatch.setattr(worker_module, "Database", FakeDatabase)
    monkeypatch.setattr(worker_module, "StorageClient", lambda settings: None)
    monkeypatch.setattr(worker_module, "configure_logging", lambda: None)

    assert await worker_module.run(once=False) == 0
    assert len(processed) == 2 and closed == [True]
    state = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert state["jobs_processed"] == 2


def test_falha_ao_gravar_heartbeat_nao_derruba_o_worker(tmp_path, monkeypatch):
    supervision = Supervision(tmp_path)

    def locked(*_args, **_kwargs):
        raise PermissionError("heartbeat.json aberto por outro processo")

    monkeypatch.setattr(worker_module.os, "replace", locked)
    supervision.beat(_fake_worker(), processed=True)
    assert supervision.jobs == 1  # counted; the next beat retries the write
