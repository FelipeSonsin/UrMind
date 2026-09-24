"""EventContext (§13): providers falham isolados, têm cache e proveniência. Sem rede."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

import app.services.context as context_module
import app.worker as worker_module
from app.config import Settings
from app.services.context import (
    POI_RADIUS_M,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    NominatimReverse,
    OpenMeteoRain,
    OverpassPois,
    TTLCache,
    gather_context,
)
from app.services.core import context_input
from app.services.external_sources.sidra import IbgeSidraProvider, SidraQuery

WHEN = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    for provider in (
        NominatimReverse,
        OverpassPois,
        OpenMeteoRain,
        context_module.GeoSampaSidewalks,
        context_module.AdministrativeLocationProvider,
    ):
        monkeypatch.setattr(provider, "cache", TTLCache(ttl_s=3600))
    monkeypatch.setattr(IbgeSidraProvider, "cache", TTLCache(ttl_s=3600))
    monkeypatch.setattr(NominatimReverse, "_last_request", 0.0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_provider_indisponivel_vira_context_unavailable_sem_excecao() -> None:
    async with _client(lambda request: httpx.Response(503)) as client:
        results = await gather_context(-23.56, -46.65, WHEN, client=client)
    assert {r.source for r in results} == {
        "nominatim_reverse",
        "overpass_pois",
        "open_meteo_rain",
        "geosampa_sidewalks",
    }
    assert all(r.status == STATUS_UNAVAILABLE and r.error for r in results)
    assert all(r.provenance["url"].startswith("https://") for r in results)


@pytest.mark.asyncio
async def test_sidra_enters_event_context_only_with_explicit_territory() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"V": "Valor"}, {"V": "123"}])

    query = SidraQuery(
        table="6579",
        variable="9324",
        period="last 1",
        territorial_level="6",
        territorial_id="3550308",
    )
    async with _client(handler) as client:
        results = await gather_context(
            -23.56,
            -46.65,
            WHEN,
            providers=(),
            client=client,
            include_sidra=True,
            sidra_query=query,
        )

    assert len(results) == 1
    assert results[0].source == "ibge_sidra"
    assert results[0].status == STATUS_OK
    assert results[0].provenance["territorial_id"] == "3550308"
    assert results[0].provenance["correlation_id"] == requests[0].headers["X-Correlation-ID"]
    assert "/p/last%201" in str(requests[0].url)


@pytest.mark.asyncio
async def test_sidra_without_config_is_persistable_as_context_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with _client(handler) as client:
        results = await gather_context(
            -23.56,
            -46.65,
            WHEN,
            providers=(),
            client=client,
            include_sidra=True,
            sidra_query=None,
        )

    assert len(results) == 1
    assert results[0].source == "ibge_sidra"
    assert results[0].status == STATUS_UNAVAILABLE
    assert results[0].error == "territory_required"
    assert calls == 0


@pytest.mark.asyncio
async def test_nominatim_and_open_meteo_use_canonical_settings_urls(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "nominatim.example":
            return httpx.Response(
                200,
                json={
                    "display_name": "Praca da Se, Sao Paulo",
                    "address": {"city": "Sao Paulo", "postcode": "01001-000"},
                    "osm_type": "node",
                    "osm_id": 1,
                },
            )
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-16T12:00", "2026-09-17T12:00"],
                    "precipitation": [1.0, 2.0],
                }
            },
        )

    settings = Settings(
        NOMINATIM_BASE_URL="https://nominatim.example",
        OPEN_METEO_FORECAST_URL="https://forecast.example/v1",
        OPEN_METEO_ARCHIVE_URL="https://archive.example/v1",
    )
    monkeypatch.setattr(context_module, "get_settings", lambda: settings)
    async with _client(handler) as client:
        nominatim = await NominatimReverse(client).fetch(-23.55, -46.63, WHEN)
        forecast = await OpenMeteoRain(client).fetch(-23.55, -46.63, WHEN)
        archive = await OpenMeteoRain(client).fetch(
            -23.55,
            -46.63,
            datetime(2020, 1, 2, 12, 0, tzinfo=UTC),
        )

    assert nominatim.status == STATUS_OK
    assert forecast.status == STATUS_OK
    assert archive.status == STATUS_UNAVAILABLE  # fixture dates intentionally do not match 2020
    assert requests[0].url == httpx.URL(
        "https://nominatim.example/reverse?lat=-23.55&lon=-46.63&format=jsonv2&zoom=18&addressdetails=1"
    )
    assert requests[1].url.host == "forecast.example"
    assert requests[2].url.host == "archive.example"


@pytest.mark.asyncio
async def test_geosampa_sidewalk_context_is_limited_cached_and_factual(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    timeout_values: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        timeout_values.update(request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "numberMatched": 1,
                "features": [
                    {
                        "type": "Feature",
                        "id": "calcada.1",
                        "geometry": {"type": "Polygon", "coordinates": []},
                        "properties": {
                            "nm_logradouro": "Rua Exemplo",
                            "tx_situacao": "Regular",
                            "qt_largura_media_trecho": 2.1,
                            "pc_declividade_media_trecho": 3.5,
                        },
                    }
                ],
            },
        )

    settings = Settings(
        GEOSAMPA_WFS_URL="https://geosampa.example/wfs",
        URMIND_EXTERNAL_HTTP_TIMEOUT_SECONDS=7.5,
    )
    monkeypatch.setattr(context_module, "get_settings", lambda: settings)
    async with _client(handler) as client:
        provider = context_module.GeoSampaSidewalks(client)
        first = await provider.fetch(-23.5505, -46.6333, WHEN)
        second = await provider.fetch(-23.5505, -46.6333, WHEN)

    assert first.status == STATUS_OK
    assert first.data == {
        "layer": "geoportal:calcada",
        "bbox_radius_m": 75,
        "matched": 1,
        "features": [
            {
                "id": "calcada.1",
                "road": "Rua Exemplo",
                "situation": "Regular",
                "average_width_m": 2.1,
                "average_slope_percent": 3.5,
            }
        ],
    }
    assert first.provenance["correlation_id"] == requests[0].headers["X-Correlation-ID"]
    assert second.provenance["cache"] == "hit"
    assert len(requests) == 1
    assert context_module.GeoSampaSidewalks in context_module.DEFAULT_PROVIDERS
    assert requests[0].url.params["typeNames"] == "geoportal:calcada"
    assert requests[0].url.params["count"] == "10"
    assert set(timeout_values.values()) == {7.5}


@pytest.mark.asyncio
async def test_administrative_location_uses_brasilapi_then_viacep_fallback(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    brasil_status = 404

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "brasil.example":
            return httpx.Response(brasil_status, json={"message": "not found"})
        return httpx.Response(
            200,
            json={
                "cep": "01001000",
                "logradouro": "Praca da Se",
                "bairro": "Se",
                "localidade": "Sao Paulo",
                "uf": "SP",
                "ibge": "3550308",
            },
        )

    settings = Settings(
        BRASIL_API_BASE_URL="https://brasil.example/api",
        VIACEP_BASE_URL="https://viacep.example/ws",
    )
    monkeypatch.setattr(context_module, "get_settings", lambda: settings)
    async with _client(handler) as client:
        result = await context_module.AdministrativeLocationProvider(client).fetch("01001-000")

    assert result.status == STATUS_OK
    assert result.data["resolved_by"] == "viacep"
    assert result.data["municipality_code"] == "3550308"
    assert [request.url.host for request in requests] == ["brasil.example", "viacep.example"]
    assert result.provenance["correlation_id"] == requests[-1].headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_administrative_location_does_not_call_viacep_after_primary_success(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "cep": "01001000",
                "street": "Praca da Se",
                "neighborhood": "Se",
                "city": "Sao Paulo",
                "state": "SP",
            },
        )

    settings = Settings(
        BRASIL_API_BASE_URL="https://brasil.example/api",
        VIACEP_BASE_URL="https://viacep.example/ws",
    )
    monkeypatch.setattr(context_module, "get_settings", lambda: settings)
    async with _client(handler) as client:
        result = await context_module.AdministrativeLocationProvider(client).fetch("01001000")

    assert result.status == STATUS_OK
    assert result.data["resolved_by"] == "brasilapi"
    assert [request.url.host for request in requests] == ["brasil.example"]


@pytest.mark.asyncio
async def test_administrative_location_without_postcode_is_unavailable_without_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with _client(handler) as client:
        result = await context_module.AdministrativeLocationProvider(client).fetch(None)

    assert result.status == STATUS_UNAVAILABLE
    assert result.error == "postcode_required"
    assert result.provenance["retrieved_at"] == result.fetched_at
    assert result.provenance["cache"] == "miss"
    assert calls == 0


@pytest.mark.asyncio
async def test_administrative_cache_is_scoped_to_configured_endpoints() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(
            200,
            json={
                "cep": "01001000",
                "street": "Praca da Se",
                "neighborhood": "Se",
                "city": request.url.host,
                "state": "SP",
            },
        )

    async with _client(handler) as client:
        first = await context_module.AdministrativeLocationProvider(
            client,
            brasil_api_base_url="https://brasil-a.example/api",
            viacep_base_url="https://viacep-a.example/ws",
        ).fetch("01001000")
        second = await context_module.AdministrativeLocationProvider(
            client,
            brasil_api_base_url="https://brasil-b.example/api",
            viacep_base_url="https://viacep-b.example/ws",
        ).fetch("01001000")

    assert first.data["city"] == "brasil-a.example"
    assert second.data["city"] == "brasil-b.example"
    assert calls == ["brasil-a.example", "brasil-b.example"]


@pytest.mark.asyncio
async def test_overpass_usa_retry_http_compartilhado_ate_terceira_tentativa() -> None:
    statuses = iter((429, 503, 200))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = next(statuses)
        return httpx.Response(status, json={"elements": []})

    async with _client(handler) as client:
        (result,) = await gather_context(
            -23.56,
            -46.65,
            WHEN,
            providers=(OverpassPois,),
            client=client,
        )
    assert result.status == STATUS_OK
    assert len(requests) == 3
    assert result.provenance["correlation_id"] == requests[0].headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_overpass_cache_is_scoped_to_the_configured_endpoint() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={"elements": [], "osm3s": {"timestamp_osm_base": request.url.host}},
        )

    async with _client(handler) as client:
        first = await OverpassPois(client, url="https://overpass-a.example/interpreter").fetch(
            12.3456, -45.6789, WHEN
        )
        second = await OverpassPois(client, url="https://overpass-b.example/interpreter").fetch(
            12.3456, -45.6789, WHEN
        )

    assert first.data["osm_base"] == "overpass-a.example"
    assert second.data["osm_base"] == "overpass-b.example"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_nominatim_usa_cache_e_nao_substitui_coordenada() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"display_name": "Rua A, São Paulo", "address": {"road": "Rua A"}, "osm_id": 1},
        )

    async with _client(handler) as client:
        provider = NominatimReverse(client)
        first = await provider.fetch(-23.56131, -46.65601, WHEN)
        second = await provider.fetch(-23.56132, -46.65602, WHEN)  # mesmo ~11 m
    assert len(calls) == 1
    assert calls[0].headers["User-Agent"].startswith("UrMind/")
    assert (first.status, first.provenance["cache"], second.provenance["cache"]) == (
        STATUS_OK,
        "miss",
        "hit",
    )
    assert first.data["is_coordinate_source"] is False
    assert "OpenStreetMap" in first.data["attribution"]


@pytest.mark.asyncio
async def test_falha_nao_entra_no_cache() -> None:
    responses = iter(
        [
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"display_name": "X", "address": {}}),
        ]
    )
    async with _client(lambda request: next(responses)) as client:
        provider = NominatimReverse(client)
        assert (await provider.fetch(-23.5, -46.6, WHEN)).status == STATUS_UNAVAILABLE
        assert (await provider.fetch(-23.5, -46.6, WHEN)).status == STATUS_OK


def _row(source: str, status: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(source=source, payload={"status": status, "data": data})


def test_contexto_vira_entrada_de_risco_sem_inventar() -> None:
    rows = [
        _row(
            "overpass_pois",
            STATUS_OK,
            {
                "radius_m": POI_RADIUS_M,
                "nearest": {"school": {"distance_m": 42.0}, "health": None, "crossing": None},
            },
        ),
        _row("open_meteo_rain", STATUS_UNAVAILABLE, {}),
    ]
    context = context_input(rows, recurrence_on_segment=None)
    assert context.distance_to_school_m == 42.0
    # Nada no raio: distância é pelo menos o raio consultado, não desconhecida.
    assert context.distance_to_health_m == POI_RADIUS_M
    # Provider indisponível continua indisponível.
    assert context.rain_mm_24h is None


def test_sem_contexto_tudo_indisponivel() -> None:
    context = context_input([], recurrence_on_segment=3)
    assert context.nearest_sensitive_m is None
    assert context.rain_mm_24h is None
    assert context.recurrence_on_segment == 3


@pytest.mark.asyncio
async def test_worker_connects_configured_sidra_to_event_context(monkeypatch) -> None:
    captured: dict = {}
    applied: list = []

    class Session:
        async def commit(self) -> None:
            return None

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_exc) -> None:
            return None

    class Database:
        @staticmethod
        def sessionmaker() -> SessionContext:
            return SessionContext()

    capture = SimpleNamespace(quality={"inference": {"status": "enriching_context"}})

    class Captures:
        def __init__(self, _session) -> None:
            pass

        async def get(self, _capture_id):
            return capture

    class Inference:
        def __init__(self, _session) -> None:
            pass

        async def set_capture_inference(self, item, state):
            item.quality["inference"] = state

    class Service:
        def __init__(self, *_args) -> None:
            pass

        async def event_location(self, _event_id):
            return {"latitude": -23.56, "longitude": -46.65, "occurred_at": WHEN}

        async def apply_context(self, _event_id, results):
            applied.extend(results)
            return {"context": [result.source for result in results]}

    async def fake_gather_context(*args, **kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                source="ibge_sidra",
                status=STATUS_OK,
                provenance={"territorial_id": "3550308"},
            )
        ]

    monkeypatch.setattr(worker_module, "CoreService", Service)
    monkeypatch.setattr(worker_module, "CaptureRepository", Captures)
    monkeypatch.setattr(worker_module, "InferenceRepository", Inference)
    monkeypatch.setattr(worker_module, "gather_context", fake_gather_context)
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: Settings(IBGE_SIDRA_MUNICIPALITY_CODE="3550308"),
    )

    bound = SimpleNamespace(
        info=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None
    )
    worker = worker_module.Worker(Database(), SimpleNamespace())
    event_id = uuid.uuid4()
    await worker.enrich_event(event_id, bound, uuid.uuid4())

    assert captured["include_sidra"] is True
    assert captured["include_administrative"] is True
    assert captured["sidra_query"].territorial_id == "3550308"
    assert [result.source for result in applied] == ["ibge_sidra"]
    assert capture.quality["inference"]["context_done_event_ids"] == [str(event_id)]
