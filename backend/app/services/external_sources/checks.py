"""Live checks pequenos e opt-in; nenhum deles baixa artefato bulk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.config import PROJECT_DIR, Settings
from app.services.external_sources.http import ExternalHttpClient
from app.services.external_sources.registry import integration_registry
from app.services.external_sources.sidra import IbgeSidraProvider, municipal_population_query

OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"
HF_PUBLIC_DATASETS_API = "https://huggingface.co/api/datasets?limit=1&sort=downloads"


@dataclass(frozen=True)
class LiveCheck:
    name: str
    status: str
    detail: str
    checked_at: str


async def run_live_checks(
    settings: Settings,
    *,
    client: ExternalHttpClient | None = None,
    openfreemap_style_url: str = OPENFREEMAP_STYLE_URL,
) -> tuple[LiveCheck, ...]:
    owned = client is None
    http = client or ExternalHttpClient.from_settings(settings)
    try:
        return await _run_live_checks(
            settings,
            client=http,
            openfreemap_style_url=openfreemap_style_url,
        )
    finally:
        if owned:
            await http.aclose()


async def _run_live_checks(
    settings: Settings,
    *,
    client: ExternalHttpClient,
    openfreemap_style_url: str,
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
        ).fetch(municipal_population_query("3550308"))
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

    try:
        response = await http.get(HF_PUBLIC_DATASETS_API, provider="hugging_face")
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, list)
            or not payload
            or not isinstance(payload[0], dict)
            or not isinstance(payload[0].get("id"), str)
        ):
            raise TypeError("catalogo publico Hugging Face sem id")
        statuses["Hugging Face"] = ("OK_PUBLIC", f"HTTP {response.status_code}; token not used")
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        statuses["Hugging Face"] = ("UNAVAILABLE", type(exc).__name__)

    return tuple(
        LiveCheck(name=name, status=status, detail=detail, checked_at=checked_at)
        for name, (status, detail) in statuses.items()
    )
