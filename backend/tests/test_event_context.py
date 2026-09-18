"""EventContext (§13): providers falham isolados, têm cache e proveniência. Sem rede."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

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

WHEN = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    for provider in (NominatimReverse, OverpassPois, OpenMeteoRain):
        monkeypatch.setattr(provider, "cache", TTLCache(ttl_s=3600))
    monkeypatch.setattr(NominatimReverse, "_last_request", 0.0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_provider_indisponivel_vira_context_unavailable_sem_excecao() -> None:
    async with _client(lambda request: httpx.Response(503)) as client:
        results = await gather_context(-23.56, -46.65, WHEN, client=client)
    assert {r.source for r in results} == {"nominatim_reverse", "overpass_pois", "open_meteo_rain"}
    assert all(r.status == STATUS_UNAVAILABLE and r.error for r in results)
    assert all(r.provenance["url"].startswith("https://") for r in results)


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
        [httpx.Response(500), httpx.Response(200, json={"display_name": "X", "address": {}})]
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
