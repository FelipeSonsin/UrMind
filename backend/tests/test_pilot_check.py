"""The pilot inspector follows human review and publication."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.pilot_check import inspect


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_pilot_inspector_accepts_confirmed_human_review_and_publication():
    capture_id, event_id = uuid.uuid4(), uuid.uuid4()

    class Session:
        async def execute(self, statement, _params=None):
            query = str(statement)
            if "from public.captures" in query:
                return _Rows(
                    [
                        {
                            "id": capture_id,
                            "source": "pwa_photo",
                            "source_location": "manual",
                            "captured_at": datetime.now(UTC),
                            "storage_path": "private/path.jpg",
                            "quality": {"human_review": {"status": "confirmed"}},
                            "tem_ponto": True,
                        }
                    ]
                )
            if "from public.events e" in query:
                return _Rows(
                    [
                        {
                            "id": event_id,
                            "urmind_class": "URMIND_ROAD_D40",
                            "status": "confirmed",
                        }
                    ]
                )
            raise AssertionError(query)

        async def scalar(self, _statement, _params=None):
            return 1

    report = await inspect(Session(), capture_id)
    status = {step["etapa"]: step["status"] for step in report["etapas"]}
    assert status["revisão humana"] == "ok"
    assert status["evento"] == "ok"
    assert status["painel público"] == "ok"
    assert "worker" not in status and "fila" not in status and "detecção" not in status
    assert report["pronto"] is True
