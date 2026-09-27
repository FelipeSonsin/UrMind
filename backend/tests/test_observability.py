"""Observabilidade do §19: números medidos, e ausência declarada. Sem rede e sem banco."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.logging import configure_logging
from app.observability import slow_queries


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


def test_httpx_library_logs_cannot_bypass_external_url_redaction() -> None:
    configure_logging()

    assert logging.getLogger("httpx").level >= logging.WARNING


class FakeSession:
    """Devolve respostas na ordem em que as consultas aparecem na função."""

    def __init__(
        self, *, scalars: list[Any] | None = None, results: list[list[dict]] | None = None
    ):
        self._scalars = list(scalars or [])
        self._results = list(results or [])

    async def scalar(self, *_args: Any, **_kwargs: Any) -> Any:
        return self._scalars.pop(0)

    async def execute(self, *_args: Any, **_kwargs: Any) -> FakeResult:
        return FakeResult(self._results.pop(0))


@pytest.mark.asyncio
async def test_sem_pg_stat_statements_o_relatorio_diz_isso_em_vez_de_quebrar() -> None:
    report = await slow_queries(FakeSession(scalars=[0]))
    assert report["available"] is False and report["queries"] == []
    assert "pg_stat_statements" in report["reason"]


@pytest.mark.asyncio
async def test_sql_caro_vem_da_extensao_sem_infraestrutura_nova() -> None:
    rows = [{"calls": 3, "mean_ms": 120.5, "total_ms": 361.5, "rows": 9, "query": "select 1"}]
    report = await slow_queries(FakeSession(scalars=[1], results=[rows]))
    assert report["available"] is True
    assert report["queries"][0]["mean_ms"] == 120.5


@pytest.mark.asyncio
async def test_sem_captura_o_verificador_pede_foto_real_em_vez_de_simular() -> None:
    from app.pilot_check import inspect

    report = await inspect(FakeSession(results=[[]]), None)
    assert report["pronto"] is False
    assert "foto real" in report["motivo"]
    assert report["etapas"] == []


@pytest.mark.asyncio
async def test_captura_sem_evento_para_na_etapa_certa() -> None:
    from app.pilot_check import inspect

    capture = [
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "capture_key": "photo-abc",
            "source": "pwa_photo",
            "source_location": "gps_device",
            "captured_at": "2026-09-18T12:00:00Z",
            "storage_path": "user/2026/09/abc.jpg",
            "quality": {"human_review": {}},
            "tem_ponto": True,
        }
    ]
    session = FakeSession(
        results=[capture, []],
    )
    report = await inspect(session, None)
    assert report["pronto"] is False
    etapas = {step["etapa"]: step["status"] for step in report["etapas"]}
    assert etapas["captura"] == "ok" and etapas["storage"] == "ok"
    assert etapas["revisão humana"] == "pendente"
    assert etapas["evento"] == "pendente"
