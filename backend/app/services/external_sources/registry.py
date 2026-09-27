"""Canonical offline inventory; configuration is not evidence of service health."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import PROJECT_DIR, Settings

@dataclass(frozen=True)
class RuntimeOperations:
    """Description of implemented policy, not a second executable configuration."""

    timeout_seconds: float | None
    retry_policy: str
    cache_ttl_seconds: float | None
    cache_policy: str
    privacy: str
    applicability: str
    implementation: str
    health_status: Literal["UNKNOWN"] = "UNKNOWN"
    last_success: Literal["UNKNOWN"] = "UNKNOWN"
    last_failure: Literal["UNKNOWN"] = "UNKNOWN"


@dataclass(frozen=True)
class Integration:
    name: str
    kind: str
    status: str
    base_url: str | None
    auth_required: bool
    configured: bool | None
    runtime_role: str
    license: str | None
    provenance: str
    env: str | None = None
    tool_status: str | None = None
    operations: RuntimeOperations | None = None


def integration_registry(
    settings: Settings,
    *,
    project_root: Path = PROJECT_DIR,
) -> tuple[Integration, ...]:
    from app.services.context import (
        AdministrativeLocationProvider,
        GeoSampaSidewalks,
        NominatimReverse,
        OpenMeteoRain,
    )

    webots_installed = shutil.which("webots") is not None
    geospatial_tool_installed = bool(shutil.which("osmium") or shutil.which("osm2pgsql"))
    sidra_configured = settings.ibge_sidra_municipality_code is not None
    return (
        Integration(
            "Geofabrik",
            "bulk_data_source",
            "AVAILABLE",
            settings.geofabrik_sudeste_pbf_url,
            False,
            True,
            "explicit_bulk_download",
            "ODbL 1.0",
            "external_bulk",
            "GEOFABRIK_SUDESTE_PBF_URL, GEOFABRIK_SUDESTE_MD5_URL",
            "AVAILABLE" if geospatial_tool_installed else "TOOL_NOT_INSTALLED",
        ),
        Integration(
            "Overpass",
            "runtime_api",
            "AVAILABLE",
            settings.overpass_api_url,
            False,
            True,
            "event_context_and_small_osm_areas",
            "ODbL 1.0",
            "external_runtime",
            "OVERPASS_API_URL",
        ),
        Integration(
            "OpenFreeMap",
            "map_provider",
            "AVAILABLE",
            "https://tiles.openfreemap.org/styles/liberty",
            False,
            True,
            "primary_maplibre_style",
            "MIT style / OSM ODbL data",
            "browser_map",
            "VITE_MAP_STYLE_URL",
        ),
        Integration(
            "CARTO",
            "map_provider",
            "FRONTEND_CONFIG_UNKNOWN",
            "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
            True,
            None,
            "optional_map_fallback",
            "CARTO Basemaps terms / OSM attribution",
            "browser_map; frontend_config_not_observable",
            "VITE_CARTO_BASEMAPS_API_KEY",
        ),
        Integration(
            "IBGE SIDRA",
            "runtime_api",
            "CONFIGURED" if sidra_configured else "TERRITORY_CONFIG_REQUIRED",
            settings.ibge_sidra_base_url,
            False,
            sidra_configured,
            "event_context_population_with_explicit_municipality",
            "IBGE public data",
            "external_runtime",
            "IBGE_SIDRA_BASE_URL, IBGE_SIDRA_MUNICIPALITY_CODE",
        ),
        Integration(
            "IBGE CNEFE",
            "bulk_data_source",
            "PREPARED",
            settings.ibge_cnefe_2022_base_url,
            False,
            True,
            "explicit_territorial_subset",
            "IBGE public data",
            "external_bulk",
            "IBGE_CNEFE_2022_BASE_URL",
        ),
        Integration(
            "Webots",
            "simulator",
            "PREPARED" if webots_installed else "NOT_INSTALLED_PREPARED",
            "https://cyberbotics.com",
            False,
            webots_installed,
            "future_gateway_camera_source",
            "Apache-2.0",
            "simulation",
            None,
            "AVAILABLE" if webots_installed else "TOOL_NOT_INSTALLED",
        ),
        Integration(
            "Supabase",
            "runtime_platform",
            "CONFIGURED" if settings.supabase_url else "NOT_CONFIGURED",
            settings.supabase_url,
            True,
            bool(settings.supabase_url),
            "Auth, private Storage, PostgreSQL/PostGIS, Queue and Realtime",
            None,
            "runtime_configuration",
            "SUPABASE_URL; server and browser credentials are separate",
            operations=RuntimeOperations(
                timeout_seconds=None,
                retry_policy="No shared policy: Storage makes one HTTP request; other subsystems differ",
                cache_ttl_seconds=None,
                cache_policy="Subsystem-specific: Auth JWKS caches keys; no shared response cache",
                privacy="Server-only secret key; private originals; public derivative requires reviewer publication",
                applicability="Configured deployment; URL presence does not verify Auth, Storage or database",
                implementation="app/auth.py; app/services/storage.py; app/db/session.py",
            ),
        ),
        *(
            Integration(
                name,
                "runtime_api",
                "CONFIGURED",
                url,
                False,
                bool(url),
                role,
                license_name,
                "external_runtime",
                env,
                operations=RuntimeOperations(
                    timeout_seconds=settings.external_http_timeout_seconds,
                    retry_policy="ExternalHttpClient default: 3 attempts; bounded backoff; transient errors only",
                    cache_ttl_seconds=provider.cache.ttl_s,
                    cache_policy=cache_policy,
                    privacy=privacy,
                    applicability=applicability,
                    implementation=f"app/services/context.py:{provider.__name__}",
                ),
            )
            for name, url, role, license_name, env, provider, cache_policy, privacy, applicability in (
                (
                    "Nominatim",
                    settings.nominatim_base_url,
                    "approximate_reverse_address",
                    "ODbL 1.0",
                    "NOMINATIM_BASE_URL",
                    NominatimReverse,
                    "In-process success cache keyed by endpoint and rounded coordinates; failures not cached",
                    "Sends event latitude/longitude; no image or owner identity",
                    "Event coordinates; approximate cartographic address, not coordinate authority",
                ),
                (
                    "Open-Meteo",
                    settings.open_meteo_forecast_url,
                    "rain_context_forecast_or_archive",
                    "CC BY 4.0",
                    "OPEN_METEO_FORECAST_URL, OPEN_METEO_ARCHIVE_URL",
                    OpenMeteoRain,
                    "In-process success cache keyed by endpoints, rounded coordinates and event hour",
                    "Sends event coordinates and time window; no image or owner identity",
                    "Weather context; historical archive selected for older events; never causal evidence",
                ),
                (
                    "GeoSampa",
                    settings.geosampa_wfs_url,
                    "official_sidewalk_context",
                    None,
                    "GEOSAMPA_WFS_URL",
                    GeoSampaSidewalks,
                    "In-process success cache keyed by endpoint and rounded coordinates",
                    "Sends a local coordinate bbox; no image or owner identity",
                    "Sao Paulo sidewalk layer; empty response is not evidence of absent infrastructure",
                ),
                (
                    "BrasilAPI",
                    settings.brasil_api_base_url,
                    "primary_postcode_validation",
                    None,
                    "BRASIL_API_BASE_URL",
                    AdministrativeLocationProvider,
                    "Shared administrative success cache keyed by provider endpoints and normalized CEP",
                    "Sends only normalized CEP; no image, exact coordinates or owner identity",
                    "Only when an existing valid CEP is available; does not create coordinates",
                ),
                (
                    "ViaCEP",
                    settings.viacep_base_url,
                    "fallback_postcode_validation",
                    None,
                    "VIACEP_BASE_URL",
                    AdministrativeLocationProvider,
                    "Shared administrative success cache keyed by provider endpoints and normalized CEP",
                    "Sends only normalized CEP; no image, exact coordinates or owner identity",
                    "Fallback after BrasilAPI fails, with an existing valid CEP; does not create coordinates",
                ),
            )
        ),
    )
