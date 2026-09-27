from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services.external_sources.checks import run_live_checks
from app.services.external_sources.http import ExternalHttpClient


@pytest.mark.asyncio
async def test_live_checks_use_only_small_requests_and_skip_optional_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        host = request.url.host
        if host == "overpass.example":
            return httpx.Response(200, json={"elements": []})
        if host == "sidra.example":
            return httpx.Response(200, json=[{"V": "Valor"}, {"V": "1"}])
        if host == "maps.example":
            return httpx.Response(200, json={"version": 8, "sources": {}, "layers": []})
        if host == "geofabrik.example":
            return httpx.Response(200, text="0123456789abcdef0123456789abcdef  sudeste.osm.pbf")
        return httpx.Response(404)

    settings = Settings(
        OVERPASS_API_URL="https://overpass.example/interpreter",
        IBGE_SIDRA_BASE_URL="https://sidra.example",
        GEOFABRIK_SUDESTE_MD5_URL="https://geofabrik.example/sudeste.osm.pbf.md5",
    )
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, backoff_seconds=0)
    try:
        results = await run_live_checks(
            settings,
            client=client,
            openfreemap_style_url="https://maps.example/liberty",
        )
    finally:
        await raw.aclose()

    by_name = {result.name: result for result in results}
    assert by_name["Overpass"].status == "OK"
    assert by_name["IBGE SIDRA"].status == "OK_PROVIDER"
    assert "runtime territory not configured" in by_name["IBGE SIDRA"].detail
    assert by_name["OpenFreeMap"].status == "OK"
    assert by_name["Geofabrik"].status == "AVAILABLE"
    assert by_name["CARTO"].status == "FRONTEND_CONFIG_UNKNOWN"
    assert by_name["CARTO"].detail == "frontend configuration is not observable from backend"
    assert set(by_name) >= {
        "Supabase",
        "Nominatim",
        "Open-Meteo",
        "GeoSampa",
        "BrasilAPI",
        "ViaCEP",
    }
    assert all(
        by_name[name].status == "UNAVAILABLE"
        for name in (
            "Supabase",
            "Open-Meteo",
            "GeoSampa",
            "BrasilAPI",
            "ViaCEP",
        )
    )
    assert by_name["Nominatim"].status == "DEGRADED"
    assert not any(request.url.host == "nominatim.openstreetmap.org" for request in requests)
    assert all(request.method in {"GET", "POST"} for request in requests)
    sidra_request = next(request for request in requests if request.url.host == "sidra.example")
    assert "/n6/3550308/" in str(sidra_request.url)
    assert "/p/last%201" in str(sidra_request.url)
    assert not any(request.url.path.endswith(".osm.pbf") for request in requests)
    assert not any("CNEFE" in str(request.url) for request in requests)


@pytest.mark.asyncio
async def test_carto_live_check_does_not_claim_to_know_frontend_configuration() -> None:
    seen_carto: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "basemaps.cartocdn.com":
            seen_carto.append(request)
            return httpx.Response(200, json={"version": 8})
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json={"elements": []})
        if request.url.host == "apisidra.ibge.gov.br":
            return httpx.Response(200, json=[{"V": "Valor"}, {"V": "1"}])
        if request.url.host == "tiles.openfreemap.org":
            return httpx.Response(200, json={"version": 8})
        if request.url.host == "download.geofabrik.de":
            return httpx.Response(200, text="0123456789abcdef0123456789abcdef file")
        if request.url.host == "huggingface.co":
            return httpx.Response(200, json=[{"id": "public/example"}])
        return httpx.Response(404)

    settings = Settings()
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await run_live_checks(settings, client=ExternalHttpClient(client=raw))
    finally:
        await raw.aclose()

    carto = {result.name: result for result in results}["CARTO"]
    assert carto.status == "FRONTEND_CONFIG_UNKNOWN"
    assert carto.detail == "frontend configuration is not observable from backend"
    assert seen_carto == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["502", "timeout"])
async def test_runtime_health_degrades_without_inventing_success(failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("isolated timeout", request=request)
        return httpx.Response(502)

    lease = AsyncMock()
    lease.reserve.return_value = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
        results = await run_live_checks(
            Settings(SUPABASE_URL="https://fixture.supabase.co"),
            client=ExternalHttpClient(client=raw, max_attempts=1, backoff_seconds=0),
            geocoding=lease,
        )
    by_name = {row.name: row for row in results}
    for name in (
        "Supabase",
        "Nominatim",
        "Open-Meteo",
        "GeoSampa",
        "BrasilAPI",
        "ViaCEP",
        "Overpass",
        "IBGE SIDRA",
        "OpenFreeMap",
    ):
        assert by_name[name].status == "UNAVAILABLE"
    lease.release.assert_awaited_once()
    assert len(by_name) == 13
