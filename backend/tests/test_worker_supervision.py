"""Supervised local Worker: heartbeat file and graceful stop between jobs."""

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import worker as worker_module
from app.worker import Supervision, Worker


def _fake_worker():
    return SimpleNamespace()


def test_heartbeat_registra_pid_e_ultimo_job(tmp_path):
    supervision = Supervision(tmp_path)
    supervision.beat(_fake_worker(), processed=True)
    supervision.beat(_fake_worker(), processed=False, error="StorageError")
    state = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert state["queue"] == "inference_jobs"
    assert state["jobs_processed"] == 1 and state["iteration_errors"] == 1
    assert state["last_error"] == "StorageError" and state["last_job_at"]
    assert not (tmp_path / "heartbeat.json.tmp").exists()


def test_sem_diretorio_nao_escreve_nada_e_nunca_para():
    supervision = Supervision(None)
    supervision.beat(_fake_worker(), processed=True)
    assert supervision.jobs == 1 and not supervision.stop_requested()


@pytest.mark.asyncio
async def test_stop_request_encerra_o_loop_entre_jobs(tmp_path, monkeypatch):
    processed = []

    class FakeWorker:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("point", "expected_status"),
    [(None, "location_required"), (object(), "needs_review")],
)
async def test_worker_encaminha_captura_sem_detector_e_arquiva_apos_salvar(
    monkeypatch, point, expected_status
):
    capture_id = uuid.uuid4()
    capture = SimpleNamespace(id=capture_id, quality={}, source="pwa_photo", point=point)
    calls: list[str] = []

    class FakeSession:
        async def commit(self):
            calls.append("commit")

    @asynccontextmanager
    async def sessionmaker():
        yield FakeSession()

    class FakeInferenceRepository:
        def __init__(self, _session):
            pass

        async def read_job(self, _timeout):
            calls.append("read")
            return {"msg_id": 7, "message": {"capture_id": str(capture_id)}}

        async def set_capture_inference(self, item, state):
            calls.append("save")
            item.quality["inference"] = state

        async def archive_job(self, msg_id):
            assert msg_id == 7
            calls.append("archive")

    class FakeCaptureRepository:
        def __init__(self, _session):
            pass

        async def get(self, requested_id):
            assert requested_id == capture_id
            calls.append("capture")
            return capture

    monkeypatch.setattr(worker_module, "InferenceRepository", FakeInferenceRepository)
    monkeypatch.setattr(worker_module, "CaptureRepository", FakeCaptureRepository)

    worker = Worker(SimpleNamespace(sessionmaker=sessionmaker))
    assert await worker.process_one() is True
    assert capture.quality["inference"]["status"] == expected_status
    assert capture.quality["inference"]["reason"] == "manual_review_required"
    assert calls == ["read", "commit", "capture", "save", "archive", "commit"]
