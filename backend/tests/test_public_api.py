"""Painel público: allowlist, privacidade, honestidade de estado. Sem rede e sem banco."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import JWKS_PATH, Settings, get_settings
from app.schemas.public import EventDetailPublic, EventSummaryPublic, ScoutCameraPublic
from app.services import public_view
from app.services.risk import explain_priority, uncertainty_band

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("enabled", [False, True])
def test_generic_capture_layer_is_explicitly_opt_in(client, monkeypatch, enabled):
    from app.api.v1 import public as public_api

    service = SimpleNamespace(
        capture_markers=AsyncMock(
            return_value=[
                {
                    "id": str(uuid.uuid4()),
                    "latitude": -23,
                    "longitude": -46,
                    "report_status": "received",
                }
            ]
        )
    )
    monkeypatch.setattr(
        public_api, "get_settings", lambda: Settings(PUBLIC_CAPTURE_MARKERS_ENABLED=enabled)
    )
    client.app.dependency_overrides[public_api.repositories] = lambda: {"service": service}
    try:
        response = client.get("/api/v1/public/capture-markers")
        assert response.status_code == 200
        if enabled:
            assert set(response.json()[0]) == {"id", "latitude", "longitude", "report_status"}
            service.capture_markers.assert_awaited_once_with("", public=True)
        else:
            assert response.json() == []
            service.capture_markers.assert_not_awaited()
    finally:
        client.app.dependency_overrides.pop(public_api.repositories)


def test_public_image_requires_shared_quota_persistence(client):
    from app.api.v1 import public as public_api

    client.app.dependency_overrides[public_api.repositories] = lambda: {
        "public": SimpleNamespace(event=AsyncMock(return_value=None))
    }
    client.app.dependency_overrides[public_api.get_storage] = lambda: SimpleNamespace()
    try:
        response = client.get(f"/api/v1/public/events/{uuid.uuid4()}/image")
        assert response.status_code == 503
    finally:
        client.app.dependency_overrides.pop(public_api.repositories)
        client.app.dependency_overrides.pop(public_api.get_storage)


def test_public_image_ignores_untrusted_forwarded_identity(client):
    import hashlib

    from app.api.v1 import public as public_api

    quota = SimpleNamespace(admit=AsyncMock())
    client.app.dependency_overrides[public_api.image_quota] = lambda: quota
    client.app.dependency_overrides[public_api.repositories] = lambda: {
        "public": SimpleNamespace(event=AsyncMock(return_value=None))
    }
    client.app.dependency_overrides[public_api.get_storage] = lambda: SimpleNamespace()
    try:
        for spoofed in ("192.0.2.1", "192.0.2.2"):
            response = client.get(
                f"/api/v1/public/events/{uuid.uuid4()}/image", headers={"X-Forwarded-For": spoofed}
            )
            assert response.status_code == 404
        assert [call.args for call in quota.admit.await_args_list] == [
            ("lookup", hashlib.sha256(b"peer:testclient").hexdigest(), None)
        ] * 2
    finally:
        for dependency in (public_api.image_quota, public_api.repositories, public_api.get_storage):
            client.app.dependency_overrides.pop(dependency)


def test_public_image_db_quota_failure_is_closed_and_redacted(client):
    from app.api.v1 import public as public_api
    from app.repositories.core import QuotaUnavailableError

    quota = SimpleNamespace(
        admit=AsyncMock(side_effect=QuotaUnavailableError("sensitive-db-detail"))
    )
    repo = SimpleNamespace(event=AsyncMock(return_value=None))
    client.app.dependency_overrides[public_api.image_quota] = lambda: quota
    client.app.dependency_overrides[public_api.repositories] = lambda: {"public": repo}
    client.app.dependency_overrides[public_api.get_storage] = lambda: SimpleNamespace()
    try:
        response = client.get(f"/api/v1/public/events/{uuid.uuid4()}/image")
        assert response.status_code == 503
        assert "sensitive-db-detail" not in response.text
        repo.event.assert_not_awaited()
    finally:
        for dependency in (public_api.image_quota, public_api.repositories, public_api.get_storage):
            client.app.dependency_overrides.pop(dependency)


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
    legacy = public_view.image_availability(capture, {"privacy_redacted": True})
    assert legacy.available is False
    released = public_view.image_availability(
        capture,
        {
            "sha256": "b" * 64,
            "public_image": {
                "storage_path": "public-images/example.jpg",
                "sha256": "a" * 64,
                "content_type": "image/jpeg",
                "metadata_stripped": True,
                "visible_content_reviewed": True,
                "source_sha256": "b" * 64,
                "review_id": "review-1",
            },
        },
    )
    assert released.available is True and released.privacy_redacted is True
    assert public_view.image_availability(None, None).available is False


@pytest.mark.parametrize(
    "change",
    [
        {"metadata_stripped": False},
        {"visible_content_reviewed": False},
        {"storage_path": ""},
        {"sha256": None},
        {"content_type": "application/octet-stream"},
    ],
)
def test_public_derivative_requires_complete_privacy_evidence(change):
    derivative = {
        "storage_path": "public-images/example.jpg",
        "sha256": "a" * 64,
        "content_type": "image/jpeg",
        "metadata_stripped": True,
        "visible_content_reviewed": True,
    }
    assert not public_view.image_availability(
        {"id": "capture"}, {"public_image": {**derivative, **change}}
    ).available


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


def test_phase5_publica_niveis_ordinais_sem_inventar_score() -> None:
    factors = {
        "phase5": {
            "ruleset_version": "urmind-risk-rules-v1",
            "risk": {"ordinal_level": "medium"},
            "priority": {"attention_lane": "elevated"},
            "decision_trace": {"provisional_parameters": {"calibration_required": True}},
        }
    }
    summary = public_view.summary({**ROW, "priority_score": None, "factors": factors})
    risk = public_view.risk_public(
        {"severity": "low", "priority_score": None, "uncertainty": None, "factors": factors}
    )
    assert summary.risk_level == "medium"
    assert summary.priority_lane == "elevated"
    assert summary.priority_score is None
    assert risk is not None
    assert risk.risk_level == "medium"
    assert risk.priority_lane == "elevated"
    assert risk.ruleset_version == "urmind-risk-rules-v1"
    assert risk.thresholds_are_calibrated is False
    assert risk.priority_score is None


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


def test_public_risk_exposes_only_declared_impact_domains():
    risk = public_view.risk_public(
        {
            "severity": "medium",
            "priority_score": None,
            "uncertainty": None,
            "factors": {
                "phase5": {
                    "impact": {"potential_domains": ["mobility", "infrastructure"]},
                    "risk": {"ordinal_level": "high"},
                    "priority": {"attention_lane": "review_required"},
                    "ruleset_version": "urmind-risk-rules-v1",
                }
            },
        }
    )
    assert risk is not None
    assert risk.impact == ["mobility", "infrastructure"]
    assert risk.risk_level == "high"
    assert risk.priority_lane == "review_required"
    assert risk.assessment_source == "phase5"
    assert "phase5" not in risk.model_dump()


def test_public_context_uses_assessment_snapshot_not_newer_provider_data():
    old = [{"source": "open_meteo_rain", "payload": {"status": "context_unavailable"}}]
    new = [{"source": "open_meteo_rain", "payload": {"status": "ok"}}]
    assert (
        public_view.assessed_context_records({"phase4_snapshot": {"context_records": old}}, new)
        == old
    )
    assert (
        public_view.assessed_context_records({"phase4_snapshot": {"context_records": []}}, new)
        == []
    )
    assert public_view.assessed_context_records({}, new) == []


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


@pytest.mark.asyncio
async def test_public_repository_filters_list_detail_and_overview_by_review_publication():
    from app.repositories.core import PublicRepository

    class Result:
        def mappings(self):
            return self

        def __iter__(self):
            return iter([])

        def first(self):
            return None

        def one(self):
            return {}

    session = SimpleNamespace(execute=AsyncMock(return_value=Result()))
    repo = PublicRepository(session)
    await repo.events(limit=20)
    await repo.event(uuid.UUID(ROW["id"]))
    await repo.overview()
    for call in session.execute.call_args_list:
        sql = str(call.args[0])
        assert PublicRepository._PUBLISHED in sql
        assert "urmind-publication-v1" in sql
        assert "pr.event_id = e.id" in sql
        assert "pr.reviewer = e.factors->'publication'->>'reviewer'" in sql
        assert "order by lr.commit_order" in sql
    await repo.event(uuid.UUID(ROW["id"]), owner_id="owner-value-not-SQL")
    call = session.execute.call_args
    assert "owner-value-not-SQL" not in str(call.args[0])
    assert call.args[1]["owner_id"] == "owner-value-not-SQL"
    assert "oc.quality->>'uploaded_by' = :owner_id" in str(call.args[0])


@pytest.mark.parametrize(
    ("published", "viewer", "expected"),
    [
        (False, None, 404),
        (True, None, 200),
        (False, "owner", 200),
        (False, "other", 404),
        (True, "other", 200),
    ],
)
def test_detail_requires_publication_or_capture_owner(client, published, viewer, expected):
    from app.api.v1 import public as public_api
    from app.auth import AuthenticatedUser

    # Repository result is authorization-scoped; the handler must never load
    # the internal dossier before that boundary returns an accessible row.
    async def event(_event_id, *, owner_id=None):
        return ROW if published or owner_id == "owner" else None

    service = SimpleNamespace(event_dossier=AsyncMock(return_value={}))
    repos = {
        "public": SimpleNamespace(
            event=AsyncMock(side_effect=event), prediction=AsyncMock(return_value=None)
        ),
        "service": service,
        "decisions": SimpleNamespace(latest_risk=AsyncMock(return_value=None)),
        "inference": SimpleNamespace(),
    }
    user = AuthenticatedUser(id=viewer, email=None, role="authenticated") if viewer else None
    client.app.dependency_overrides[public_api.repositories] = lambda: repos
    client.app.dependency_overrides[public_api.optional_user] = lambda: user
    try:
        response = client.get(f"/api/v1/public/events/{ROW['id']}")
    finally:
        client.app.dependency_overrides.pop(public_api.repositories)
        client.app.dependency_overrides.pop(public_api.optional_user)
    assert response.status_code == expected
    assert repos["public"].event.await_args_list[0].kwargs == {"owner_id": viewer}
    if expected == 404:
        service.event_dossier.assert_not_awaited()
    else:
        body = response.json()
        assert body["analysis"]["schema_version"] == "urmind-urban-analysis-v1"
        assert body["analysis"]["severity"] is None
        assert body["image"]["available"] is False
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["vary"] == "Authorization"
        assert "uploaded_by" not in body and "publication" not in body


@pytest.mark.asyncio
async def test_optional_auth_validates_supplied_bearer(monkeypatch):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.api.v1 import public as public_api

    validator = AsyncMock(side_effect=HTTPException(status_code=401))
    monkeypatch.setattr(public_api, "require_user", validator)
    assert await public_api.optional_user(None) is None
    validator.assert_not_awaited()
    with pytest.raises(HTTPException) as failure:
        await public_api.optional_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")
        )
    assert failure.value.status_code == 401


@pytest.mark.parametrize(
    "case",
    [
        "unpublished",
        "raw",
        "hash",
        "mime",
        "source",
        "review",
        "withdrawn_during_download",
        "replaced_during_download",
        "other_event_same_capture",
        "too_large",
        "concurrency_busy",
        "attempt_budget",
        "unknown_ids",
        "ok",
    ],
)
def test_public_image_only_serves_current_verified_derivative(client, case, monkeypatch):
    import hashlib
    import io

    from PIL import Image

    from app.api.v1 import public as public_api
    from app.repositories.core import QuotaExceededError

    async def admit(stage, caller, resource=None):
        if stage == "download":
            repo.session.rollback.assert_awaited_once()
        if case == "attempt_budget" and stage == "download":
            raise QuotaExceededError("limit")

    quota = SimpleNamespace(admit=AsyncMock(side_effect=admit))
    client.app.dependency_overrides[public_api.image_quota] = lambda: quota
    if case == "concurrency_busy":
        from threading import BoundedSemaphore

        monkeypatch.setattr(public_api, "_public_image_slots", BoundedSemaphore(0))

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG" if case == "mime" else "JPEG")
    data = buffer.getvalue()
    if case == "too_large":
        monkeypatch.setattr(public_api, "MAX_BYTES", len(data) - 1)
    path = f"public-derived/{ROW['id']}/sanitized.jpg"
    derivative = {
        "storage_path": "private/original.jpg" if case == "raw" else path,
        "sha256": "0" * 64 if case == "hash" else hashlib.sha256(data).hexdigest(),
        "source_sha256": "c" * 64 if case == "source" else "b" * 64,
        "content_type": "image/jpeg",
        "metadata_stripped": True,
        "visible_content_reviewed": True,
        "review_id": "stale-review" if case == "review" else "current-review",
    }
    capture = SimpleNamespace(
        id=uuid.uuid4(),
        storage_path="private/original.jpg",
        quality={"sha256": "b" * 64, "public_image": derivative},
    )
    if case == "other_event_same_capture":
        # A second published Event from this Capture must not replace the
        # first Event's approved image. Legacy singleton metadata is ignored.
        capture.quality["public_image"] = {
            **derivative,
            "review_id": "second-event-review",
            "storage_path": f"public-derived/{uuid.uuid4()}/other.jpg",
        }
    repo = SimpleNamespace(
        session=SimpleNamespace(rollback=AsyncMock()),
        event=AsyncMock(
            return_value=None
            if case == "unpublished"
            else {
                **ROW,
                "capture_id": capture.id,
                "publication_review_id": "current-review",
                "publication_image": derivative,
            }
        ),
    )
    if case in {"withdrawn_during_download", "replaced_during_download"}:
        initial = repo.event.return_value
        after = (
            None
            if case == "withdrawn_during_download"
            else {
                **initial,
                "publication_image": {**derivative, "sha256": "f" * 64},
            }
        )
        repo.event.side_effect = [initial, after]
    storage = SimpleNamespace(download=AsyncMock(return_value=data))
    captures = SimpleNamespace(get=AsyncMock(return_value=capture))
    client.app.dependency_overrides[public_api.repositories] = lambda: {
        "public": repo,
        "service": SimpleNamespace(captures=captures),
    }
    client.app.dependency_overrides[public_api.get_storage] = lambda: storage
    try:
        if case == "unknown_ids":
            published = repo.event.return_value
            repo.event.return_value = None
            for _ in range(60):
                missing = client.get(f"/api/v1/public/events/{uuid.uuid4()}/image")
                assert missing.status_code == 404
            repo.event.return_value = published
        response = client.get(f"/api/v1/public/events/{ROW['id']}/image")
    finally:
        client.app.dependency_overrides.pop(public_api.repositories)
        client.app.dependency_overrides.pop(public_api.get_storage)
        client.app.dependency_overrides.pop(public_api.image_quota)
    downloads = [call for call in quota.admit.await_args_list if call.args[0] == "download"]
    if case in {"unpublished", "raw", "source", "review"}:
        assert not downloads
    if case == "unknown_ids":
        assert len(downloads) == 1
    if case in {"ok", "other_event_same_capture", "unknown_ids"}:
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        storage.download.assert_awaited_once_with(path)
    elif case in {"concurrency_busy", "attempt_budget"}:
        assert response.status_code == 429
        assert response.headers["retry-after"]
        storage.download.assert_not_awaited()
    else:
        assert response.status_code == 404
        assert "private/" not in response.text and "public-derived/" not in response.text
        if case not in {
            "hash",
            "mime",
            "too_large",
            "withdrawn_during_download",
            "replaced_during_download",
        }:
            storage.download.assert_not_awaited()
