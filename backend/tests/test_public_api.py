"""Painel público: allowlist, privacidade, honestidade de estado. Sem rede e sem banco."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.config import JWKS_PATH, Settings, get_settings
from app.schemas.public import EventDetailPublic, EventSummaryPublic, ScoutCameraPublic
from app.services import public_view
from app.services.risk import explain_priority, uncertainty_band

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
ROW = {
    "id": "0b0e4c7e-1111-4222-8333-944445555666",
    "occurred_at": NOW,
    "urmind_class": "URMIND_ROAD_D40",
    "status": "detected",
    "evidence_mode": "photo",
    "visual_confidence": 0.61,
    "severity": "low",
    "priority_score": 0.42,
    "latitude": -23.5506,
    "longitude": -46.6348,
    "snapped_latitude": -23.5507,
    "snapped_longitude": -46.6349,
    "road_name": "Rua Senador Feijó",
}
FACTORS = {
    "ruleset_version": "urmind-risk-v1",
    "coverage": 0.55,
    "limitations": ["precisão da localização desconhecida"],
    "thresholds_are_calibrated": False,
    "weights": {"severity": 0.4, "confidence": 0.15, "recurrence": 0.1},
    "contributions": {"severity": 0.25, "confidence": 0.61, "recurrence": 0.0},
    "unavailable": {"environment": "indisponível: sem dado meteorológico"},
}


def _settings(**overrides: str) -> Settings:
    base = {
        "SUPABASE_URL": "https://refficticio.supabase.co",
        "SUPABASE_JWKS_URL": "https://refficticio.supabase.co" + JWKS_PATH,
    }
    return Settings.model_validate({**base, **overrides})


# ------------------------------------------------------------------ explicação


def test_explicacao_separa_o_que_subiu_e_o_que_desceu() -> None:
    explanation = explain_priority(FACTORS)
    increased = [item["factor"] for item in explanation["increased"]]
    decreased = [item["factor"] for item in explanation["decreased"]]
    assert increased == ["confidence"]  # 0,61 acima da média ponderada
    assert set(decreased) == {"severity", "recurrence"}
    assert explanation["unavailable"][0]["factor"] == "environment"
    assert explanation["baseline"] == pytest.approx((0.4 * 0.25 + 0.15 * 0.61) / 0.65, abs=1e-4)


def test_explicacao_sem_fatores_nao_inventa_nada() -> None:
    explanation = explain_priority({"unavailable": {"severity": "indisponível"}})
    assert explanation["increased"] == [] and explanation["decreased"] == []
    assert explanation["baseline"] is None


@pytest.mark.parametrize(
    ("value", "band"), [(None, "desconhecida"), (0.1, "baixa"), (0.4, "média"), (0.9, "alta")]
)
def test_faixa_de_incerteza(value, band) -> None:
    assert uncertainty_band(value) == band


# ------------------------------------------------------------------ privacidade


def test_imagem_so_sai_com_sanitizacao_registrada() -> None:
    capture = {"id": "c1", "capture_key": "photo-x"}
    blocked = public_view.image_availability(capture, {"sha256": "abc"})
    assert blocked.available is False and blocked.privacy_redacted is False
    assert "sanitiza" in (blocked.reason or "")
    released = public_view.image_availability(capture, {"privacy_redacted": True})
    assert released.available is True and released.privacy_redacted is True
    assert public_view.image_availability(None, None).available is False


def test_contexto_publico_nao_expoe_payload_cru() -> None:
    rows = [
        {
            "source": "nominatim_reverse",
            "fetched_at": NOW,
            "payload": {
                "status": "ok",
                "data": {
                    "display_name": "1578, Avenida Paulista, Bela Vista, São Paulo",
                    "road": "Avenida Paulista",
                    "suburb": "Bela Vista",
                    "city": "São Paulo",
                    "postcode": "01310-200",
                    "attribution": "© OpenStreetMap contributors (ODbL 1.0)",
                },
            },
        },
        {
            "source": "open_meteo_rain",
            "fetched_at": NOW,
            "payload": {"status": "context_unavailable", "data": {}},
        },
    ]
    address, rain = public_view.context_public(rows)
    assert address.summary == {
        "road": "Avenida Paulista",
        "suburb": "Bela Vista",
        "city": "São Paulo",
    }
    assert "display_name" not in address.summary and "postcode" not in address.summary
    # O rótulo legível é responsabilidade do backend: o painel não traduz chave técnica.
    assert address.label == "Endereço aproximado (Nominatim/OpenStreetMap)"
    assert rain.status == "context_unavailable" and rain.summary == {}


def test_detalhe_publico_recusa_campo_fora_do_contrato() -> None:
    base = public_view.summary(ROW)
    with pytest.raises(ValueError):
        EventDetailPublic(
            **base.model_dump(),
            distance_to_road_m=1.2,
            location_accuracy_m=6.0,
            road=None,
            image=public_view.image_availability(None, None),
            risk=None,
            action=None,
            responsibility=public_view.responsibility_public(None),
            prediction=public_view.prediction_public(None),
            model_version=None,
            model_stage=None,
            dataset_version=None,
            reviewed=False,
            reviewer_email="pessoa@example.invalid",  # campo interno: precisa ser recusado
        )


def test_resumo_publico_nao_tem_campos_internos() -> None:
    fields = set(EventSummaryPublic.model_fields)
    assert not fields & {"capture_id", "storage_path", "reviewer", "factors", "uploaded_by"}


# ------------------------------------------------------------------ estados honestos


def test_previsao_ausente_e_declarada_sem_inventar() -> None:
    prediction = public_view.prediction_public(None)
    assert prediction.available is False
    assert "histórico validado" in (prediction.reason or "")
    assert prediction.value is None and prediction.model_version is None


def test_previsao_presente_usa_o_registro_real() -> None:
    prediction = public_view.prediction_public(
        {
            "task": "recurrence",
            "horizon_days": 30,
            "value": 0.7,
            "uncertainty": 0.2,
            "version": "m1",
            "created_at": NOW,
        }
    )
    assert (prediction.available, prediction.task, prediction.value) == (True, "recurrence", 0.7)


def test_responsavel_sem_regra_fica_em_triagem() -> None:
    triage = public_view.responsibility_public(None)
    assert triage.status == "requires_triage" and triage.responsible is None
    assigned = public_view.responsibility_public(
        {"responsible": "DNIT", "source": "Lei 10.233/2001, art. 82, IV", "version": "v1"}
    )
    assert (assigned.status, assigned.responsible) == ("assigned", "DNIT")


def test_camera_sem_fonte_nao_diz_live() -> None:
    from app.api.v1.public import camera_state

    unavailable = camera_state(_settings())
    assert unavailable.mode == "unavailable" and unavailable.stream_url is None
    assert (
        camera_state(_settings(SCOUT_FRAME_URL="http://scout.local/frame.jpg")).mode
        == "live_snapshots"
    )
    assert (
        camera_state(_settings(SCOUT_STREAM_URL="http://scout.local/stream")).mode == "live_video"
    )


def test_scout_sem_dispositivo_e_no_device() -> None:
    camera = ScoutCameraPublic(mode="unavailable", reason="sem fonte")
    scout = public_view.scout_public(None, camera)
    assert scout.status == "no_device" and scout.telemetry == {}
    degraded = public_view.scout_public(
        {"code": "scout-01", "kind": "scout", "last_capture_at": NOW}, camera
    )
    assert degraded.status == "degraded" and degraded.last_seen == NOW
    assert "battery" not in degraded.telemetry  # sensor que não existe não vira número


def test_trace_marca_etapa_sem_dado_como_indisponivel() -> None:
    base = public_view.summary(ROW)
    trace = public_view.decision_trace(
        capture=None,
        detections=[],
        context=[],
        risk=None,
        responsibility=public_view.responsibility_public(None),
        action=None,
        model_version=None,
        event=base,
    )
    assert [step.step for step in trace] == [
        "capture",
        "detection",
        "context",
        "risk",
        "decision",
        "action",
    ]
    assert {step.status for step in trace} == {"unavailable"}


def test_transparencia_declara_estagio_do_baseline() -> None:
    model = {
        "name": "yolox-s-model-v1",
        "version": "baseline_early-epoch9-abc",
        "metrics": {
            "stage": "baseline_early",
            "stage_note": "treino interrompido antes do contrato",
            "metrics_not_computed": ["ap75"],
            "serving": {
                "class_names": ["URMIND_ROAD_D00"],
                "input_size": [640, 640],
                "score_threshold": 0.25,
            },
            "evaluation_full": {
                "map50": 0.11,
                "map50_95": 0.036,
                "precision": 0.05,
                "recall": 0.46,
                "f1": 0.09,
                "samples": 3858,
                "per_class": {},
            },
            "benchmark": {
                "latency_ms": {"mean": 45.6, "p50": 45.5, "p95": 47.4},
                "fps_approx": 21.9,
                "execution_provider": "CPUExecutionProvider",
                "hardware": {"cpu": "Intel64"},
            },
        },
    }
    transparency = public_view.transparency_public(
        model, {"name": "rdd2022", "version": "abc", "license": "CC BY 4.0", "source": "figshare"}
    )
    assert transparency.stage == "baseline_early"
    assert transparency.metrics.not_computed == ["ap75"]
    assert transparency.latency.p95_ms == 47.4
    assert any(
        "não é o modelo final" in item or "interrompido" in item
        for item in transparency.limitations
    )


def test_rotas_publicas_nao_exigem_autenticacao(client) -> None:
    for path in ("/api/v1/public/status", "/api/v1/public/events", "/api/v1/public/transparency"):
        assert client.get(path).status_code in (200, 503), path


def test_camera_sem_fonte_responde_503(client) -> None:
    for path in ("/api/v1/public/scout/frame", "/api/v1/public/scout/stream"):
        response = client.get(path)
        assert response.status_code == 503
        assert "câmera" in response.json()["detail"]


def test_detalhe_publico_nao_tem_relatorio_operacional() -> None:
    # O relatório carrega event_key e o id da versão de modelo: fica fora do público.
    assert "report" not in EventDetailPublic.model_fields


def test_stream_recusa_espectador_alem_do_limite(client, monkeypatch) -> None:
    """Sem vaga, a resposta é 503 imediato — nunca uma requisição pendurada."""
    from app.api.v1 import public as public_api

    with_camera = get_settings().model_copy(
        update={"scout_stream_url": "http://camera.invalid/stream"}
    )
    monkeypatch.setattr(public_api, "get_settings", lambda: with_camera)
    monkeypatch.setattr(public_api, "_stream_slots", asyncio.Semaphore(0))
    response = client.get("/api/v1/public/scout/stream")
    assert response.status_code == 503
    assert response.json()["detail"] == public_api.STREAM_BUSY
