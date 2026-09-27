"""Live checks pequenos e opt-in; nenhum deles baixa artefato bulk."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.config import PROJECT_DIR, Settings
from app.repositories.core import GeocodingRepository
from app.services.external_sources.http import ExternalHttpClient
from app.services.external_sources.registry import integration_registry
from app.services.external_sources.sidra import IbgeSidraProvider, municipal_population_query

OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"


@dataclass(frozen=True)
class LiveCheck:
    name: str
    status: str
    detail: str
    checked_at: str
    latency_ms: float | None = None


async def run_live_checks(
    settings: Settings,
    *,
    client: ExternalHttpClient | None = None,
    openfreemap_style_url: str = OPENFREEMAP_STYLE_URL,
    geocoding: GeocodingRepository | None = None,
) -> tuple[LiveCheck, ...]:
    owned = client is None
    http = client or ExternalHttpClient.from_settings(settings)
    try:
        return await _run_live_checks(
            settings,
            client=http,
            openfreemap_style_url=openfreemap_style_url,
            geocoding=geocoding,
        )
    finally:
        if owned:
            await http.aclose()


async def _run_live_checks(
    settings: Settings,
    *,
    client: ExternalHttpClient,
    openfreemap_style_url: str,
    geocoding: GeocodingRepository | None,
) -> tuple[LiveCheck, ...]:
    http = client
    checked_at = datetime.now(UTC).isoformat()
    statuses = {
        item.name: (
            ("UNKNOWN", "not probed; configuration is not service health")
            if item.operations is not None
            else (item.status, "offline registry")
        )
        for item in integration_registry(settings, project_root=PROJECT_DIR)
    }
    statuses["CARTO"] = (
        "FRONTEND_CONFIG_UNKNOWN",
        "frontend configuration is not observable from backend",
    )
    latencies: dict[str, float] = {}
    # Availability probes only. The postal code / weather coordinate below are
    # fixed public smoke-test inputs, never Capture locations or Event evidence.
    probes = [
        (
            "Supabase",
            f"{settings.supabase_url}/auth/v1/health" if settings.supabase_url else None,
            {},
            {"apikey": settings.supabase_publishable_key}
            if settings.supabase_publishable_key
            else {},
        ),
        ("Nominatim", settings.nominatim_base_url.rstrip("/") + "/status", {"format": "json"}, {}),
        (
            "Open-Meteo",
            settings.open_meteo_forecast_url,
            {
                "latitude": -23.55,
                "longitude": -46.63,
                "current": "temperature_2m",
                "forecast_days": 1,
            },
            {},
        ),
        (
            "GeoSampa",
            settings.geosampa_wfs_url,
            {"service": "WFS", "request": "GetCapabilities"},
            {},
        ),
        ("BrasilAPI", settings.brasil_api_base_url.rstrip("/") + "/cep/v2/01015000", {}, {}),
        ("ViaCEP", settings.viacep_base_url.rstrip("/") + "/01015000/json/", {}, {}),
    ]
    for name, url, params, headers in probes:
        if not url:
            statuses[name] = ("UNAVAILABLE", "not configured")
            continue
        start = time.perf_counter()
        token = uuid.uuid4()
        reserved = False
        if name == "Nominatim":
            if geocoding is None or not await geocoding.reserve(token):
                statuses[name] = ("DEGRADED", "shared lease unavailable; probe not executed")
                continue
            reserved = True
        try:
            response = await http.get(
                url, params=params, headers=headers, timeout=5, attempts=1, provider=name
            )
            response.raise_for_status()
            if name == "GeoSampa":
                if "WFS_Capabilities" not in response.text:
                    raise ValueError("invalid WFS capabilities")
            else:
                body = response.json()
                if not isinstance(body, dict) or body.get("erro") or body.get("error"):
                    raise ValueError("invalid health payload")
                if name == "Nominatim" and body.get("status") != 0:
                    raise ValueError("Nominatim unhealthy")
                if name == "Open-Meteo" and "current" not in body:
                    raise ValueError("weather unavailable")
                if name in {"BrasilAPI", "ViaCEP"} and not body.get("cep"):
                    raise ValueError("postal provider unavailable")
            statuses[name] = ("OK", f"HTTP {response.status_code}")
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            statuses[name] = ("UNAVAILABLE", type(exc).__name__)
        finally:
            if reserved and geocoding is not None:
                await geocoding.release(token)
        latencies[name] = round((time.perf_counter() - start) * 1000, 3)
    try:
        response = await http.post(
            settings.overpass_api_url,
            data={"data": "[out:json][timeout:5];node(0,0,0,0);out 1;"},
            provider="overpass",
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
            raise TypeError("resposta Overpass sem elements")
        statuses["Overpass"] = ("OK", f"HTTP {response.status_code}")
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        statuses["Overpass"] = ("UNAVAILABLE", type(exc).__name__)

    try:
        result = await IbgeSidraProvider(
            http,
            settings.ibge_sidra_base_url,
        ).fetch(municipal_population_query("3550308"), use_cache=False)
        if result.status != "ok" or not result.data.get("records"):
            raise ValueError("resposta SIDRA vazia")
        runtime = (
            f"runtime territory {settings.ibge_sidra_municipality_code} configured"
            if settings.ibge_sidra_municipality_code
            else "runtime territory not configured"
        )
        statuses["IBGE SIDRA"] = ("OK_PROVIDER", runtime)
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        statuses["IBGE SIDRA"] = ("UNAVAILABLE", type(exc).__name__)

    try:
        response = await http.get(openfreemap_style_url, provider="openfreemap")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("version") != 8:
            raise TypeError("style MapLibre invalido")
        statuses["OpenFreeMap"] = ("OK", f"HTTP {response.status_code}")
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        statuses["OpenFreeMap"] = ("UNAVAILABLE", type(exc).__name__)

    try:
        response = await http.get(
            settings.geofabrik_sudeste_md5_url,
            provider="geofabrik",
        )
        response.raise_for_status()
        if re.search(r"\b[0-9a-fA-F]{32}\b", response.text) is None:
            raise ValueError("MD5 malformado")
        statuses["Geofabrik"] = ("AVAILABLE", f"metadata HTTP {response.status_code}")
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        statuses["Geofabrik"] = ("UNAVAILABLE", type(exc).__name__)

    return tuple(
        LiveCheck(
            name=name,
            status=status,
            detail=detail,
            checked_at=checked_at,
            latency_ms=latencies.get(name),
        )
        for name, (status, detail) in statuses.items()
    )
