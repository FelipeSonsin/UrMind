"""Observabilidade do §19: números medidos, e ausência declarada. Sem rede e sem banco."""

from __future__ import annotations

from typing import Any

import pytest

from app.observability import (
    MIN_SAMPLES_FOR_DRIFT,
    NOT_ENOUGH_DATA,
    drift_status,
    model_lineage,
    slow_queries,
)


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


class FakeSession:
    """Devolve respostas na ordem em que as consultas aparecem na função."""

    def __init__(self, *, scalars: list[Any] | None = None, results: list[list[dict]] | None = None):
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
async def test_sem_amostra_o_sistema_nao_afirma_drift() -> None:
    counts = [{"detections": 12, "events": 4, "reviews": 0, "models": 1}]
    report = await drift_status(FakeSession(results=[counts]))
    assert report["status"] == NOT_ENOUGH_DATA
    assert report["missing"] == MIN_SAMPLES_FOR_DRIFT - 12
    # Nenhuma distribuição é publicada enquanto a amostra não existir.
    assert "by_class" not in report


@pytest.mark.asyncio
async def test_com_amostra_suficiente_a_distribuicao_real_e_publicada() -> None:
    counts = [{"detections": MIN_SAMPLES_FOR_DRIFT, "events": 90, "reviews": 30, "models": 2}]
    by_class = [{"urmind_class": "URMIND_ROAD_D40", "total": 120, "mean_confidence": 0.61}]
    report = await drift_status(FakeSession(results=[counts, by_class]))
    assert report["status"] == "DRIFT_BASELINE_READY"
    assert report["by_class"][0]["total"] == 120


@pytest.mark.asyncio
async def test_lineage_aponta_o_elo_que_falta_em_vez_de_presumir() -> None:
    row = [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "yolox-s",
            "version": "baseline_early",
            "checksum": "abc",
            "promoted_at": "2026-09-17",
            "dataset_name": "rdd2022",
            "dataset_version": "v1",
            "metrics": {
                "stage": "baseline_early",
                "code": {"git_commit": "deadbeef"},
                "fingerprints": {"split_fingerprint": "fp"},
                "training": {"run": {}},  # sem run do MLflow
                "serving": {
                    "checkpoint_sha256": "ckpt",
                    "onnx_path": "models/serving/x.onnx",
                    "parity": {"passed": True},
                },
            },
        }
    ]
    report = await model_lineage(FakeSession(results=[row], scalars=[0]))
    assert report["complete"] is False
    assert report["missing_links"] == ["training_run"]


@pytest.mark.asyncio
async def test_sem_modelo_promovido_nao_ha_cadeia_inventada() -> None:
    report = await model_lineage(FakeSession(results=[[]]))
    assert report["promoted"] is False


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
            "quality": {"inference": {"status": "inference_completed", "latency_ms": 310}},
            "tem_ponto": True,
        }
    ]
    session = FakeSession(
        results=[capture, [{"urmind_class": "URMIND_ROAD_D40", "confidence": 0.61}], []],
        scalars=[0, 1],
    )
    report = await inspect(session, None)
    assert report["pronto"] is False
    etapas = {step["etapa"]: step["status"] for step in report["etapas"]}
    assert etapas["captura"] == "ok" and etapas["storage"] == "ok" and etapas["worker"] == "ok"
    assert etapas["detecção"] == "ok"
    assert etapas["evento"] == "pendente"
