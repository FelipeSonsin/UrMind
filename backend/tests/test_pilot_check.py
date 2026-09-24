"""The existing E2E inspector must follow the current worker completion contract."""

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
async def test_pilot_inspector_accepts_analysis_completed_and_unavailable_context():
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
                            "quality": {"inference": {"status": "analysis_completed"}},
                            "tem_ponto": True,
                        }
                    ]
                )
            if "from public.detections" in query:
                return _Rows([{"urmind_class": "URMIND_ROAD_D40"}])
            if "from public.events e" in query:
                return _Rows(
                    [
                        {
                            "id": event_id,
                            "urmind_class": "URMIND_ROAD_D40",
                            "status": "detected",
                            "road_segment_id": uuid.uuid4(),
                            "via": "real road",
                            "distance_to_road_m": 2.0,
                        }
                    ]
                )
            if "from public.event_context" in query:
                return _Rows([{"source": "open_meteo_rain", "status": "context_unavailable"}])
            if "from public.risk_assessments" in query:
                return _Rows(
                    [
                        {
                            "severity": "low",
                            "priority_score": None,
                            "uncertainty": None,
                            "factors": {
                                "phase4_snapshot": {"features": {}},
                                "decision_trace": {"rules": []},
                            },
                            "responsibility_rule_id": uuid.uuid4(),
                            "action_id": uuid.uuid4(),
                        }
                    ]
                )
            if "from public.responsibility_rules" in query:
                return _Rows([{"responsible": "teste", "version": "v1"}])
            if "from public.actions_catalog" in query:
                return _Rows([{"label": "inspecao"}])
            raise AssertionError(query)

        async def scalar(self, _statement, _params=None):
            return 1

    report = await inspect(Session(), capture_id)
    status = {step["etapa"]: step["status"] for step in report["etapas"]}
    assert status["worker"] == "ok"
    assert status["contexto"] == "ok"
    assert status["risco"] == "ok"
    assert report["pronto"] is True
