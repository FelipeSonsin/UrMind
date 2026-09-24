"""Canonical offline inventory; configuration is not evidence of service health."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import PROJECT_DIR, Settings

RDD_CLASSES = ["URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40"]


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


def _json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _registered_hashes(project_root: Path) -> dict[str, str]:
    registry = _json(project_root / "datasets/metadata/artifact_registry.json")
    return {
        item["path"]: item["sha256"]
        for item in registry.get("artifacts", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
    }


def _matches_registry(project_root: Path, relative: str, hashes: dict[str, str]) -> bool:
    path = project_root / relative
    if not path.is_file() or relative not in hashes:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == hashes[relative]


def _rdd_ready(project_root: Path, hashes: dict[str, str]) -> bool:
    relative = "datasets/manifests/rdd2022.json"
    authorization_relative = "datasets/reports/rdd2022_split_authorization_status.json"
    manifest = _json(project_root / relative)
    inventory = (manifest.get("split") or {}).get("inventory") or {}
    authorization = _json(project_root / authorization_relative)
    return (
        _matches_registry(project_root, relative, hashes)
        and _matches_registry(project_root, authorization_relative, hashes)
        and manifest.get("name") == "rdd2022"
        and manifest.get("license") == "CC BY 4.0"
        and manifest.get("classes") == RDD_CLASSES
        and inventory.get("checksums_verified") is True
        and inventory.get("state") == "ready"
        and authorization.get("status") == "AUTHORIZED_FOR_MODEL_V1"
    )


def _univali_state(project_root: Path, hashes: dict[str, str]) -> tuple[bool, bool]:
    manifest_relative = "datasets/manifests/univali_br.json"
    split_relative = "datasets/splits/univali_br_external_test_splits.json"
    manifest = _json(project_root / manifest_relative)
    split = _json(project_root / split_relative)
    evaluation = split.get("evaluation_readiness") or {}
    leakage = split.get("leakage_check") or {}
    prepared = (
        _matches_registry(project_root, manifest_relative, hashes)
        and _matches_registry(project_root, split_relative, hashes)
        and manifest.get("name") == "univali_br"
        and manifest.get("license") == "CC BY 4.0"
        and split.get("usage") == "CANDIDATE_EXTERNAL_TEST_BR"
        and evaluation.get("training_allowed") is False
        and leakage.get("passed") is True
    )
    return prepared, prepared and evaluation.get("is_detection_evaluation_ready") is True


def _yolox_ready(project_root: Path, hashes: dict[str, str]) -> bool:
    relative = "datasets/metadata/yolox_model_v1.json"
    metadata = _json(project_root / relative)
    return (
        _matches_registry(project_root, relative, hashes)
        and (project_root / "backend/third_party/YOLOX").exists()
        and metadata.get("model_id") == "yolox-s-model-v1"
        and metadata.get("architecture") == "YOLOX-s"
        and metadata.get("class_names") == ["D00", "D10", "D20", "D40"]
        and (metadata.get("readiness") or {}).get("training_engine_ready") is True
    )


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

    hashes = _registered_hashes(project_root)
    rdd_ready = _rdd_ready(project_root, hashes)
    univali_prepared, univali_ready = _univali_state(project_root, hashes)
    yolox_ready = _yolox_ready(project_root, hashes)
    hf_installed = (
        importlib.util.find_spec("huggingface_hub") is not None
        and (project_root / "scripts/datasets/acquire_registered.py").is_file()
    )
    webots_installed = shutil.which("webots") is not None
    geospatial_tool_installed = bool(shutil.which("osmium") or shutil.which("osm2pgsql"))
    kaggle_cli_installed = shutil.which("kaggle") is not None
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
            "RDD2022",
            "dataset",
            "DONE" if rdd_ready else "INVALID_OR_MISSING",
            None,
            False,
            rdd_ready,
            "model_v1_training",
            "CC BY 4.0",
            "dataset_manifest",
        ),
        Integration(
            "UNIVALI",
            "dataset",
            "READY" if univali_ready else "PARTIAL",
            None,
            False,
            univali_prepared,
            "external_test_br_only",
            "CC BY 4.0",
            "dataset_manifest",
        ),
        Integration(
            "timm",
            "ml_library",
            "PREPARED_FOR_FUTURE",
            "https://github.com/huggingface/pytorch-image-models",
            False,
            False,
            "optional_future_backbones",
            "Apache-2.0",
            "optional_dependency",
        ),
        Integration(
            "YOLOX",
            "model_tool",
            "DONE" if yolox_ready else "PARTIAL",
            "https://github.com/Megvii-BaseDetection/YOLOX",
            False,
            yolox_ready,
            "model_v1",
            "Apache-2.0",
            "model_registry",
        ),
        Integration(
            "SAM2",
            "model_tool",
            "PREPARED_FOR_V2",
            "https://github.com/facebookresearch/sam2",
            False,
            False,
            "future_dataset_tooling_wsl",
            "Apache-2.0",
            "future_tooling",
        ),
        Integration(
            "Hugging Face",
            "data_model_platform",
            "OK" if hf_installed else "NOT_INSTALLED",
            "https://huggingface.co",
            False,
            hf_installed,
            "public_dataset_downloads; private_requires_auth",
            None,
            "dataset_acquisition",
            "HF_TOKEN",
        ),
        Integration(
            "Kaggle",
            "training_platform",
            "PREPARED" if settings.kaggle_api_token else "KAGGLE_AUTH_REQUIRED",
            "https://www.kaggle.com",
            True,
            bool(settings.kaggle_api_token),
            "remote_training_same_trainer",
            None,
            "training_platform",
            "KAGGLE_API_TOKEN",
            "AVAILABLE" if kaggle_cli_installed else "TOOL_NOT_INSTALLED",
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
