from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.config import Settings
from app.services.external_sources import sidra as sidra_module
from app.services.external_sources.bulk import (
    BulkSelectionError,
    CnefeSelection,
    download_cnefe,
    download_geofabrik,
)
from app.services.external_sources.http import ExternalHttpClient, redacted_url
from app.services.external_sources.registry import integration_registry
from app.services.external_sources.sidra import IbgeSidraProvider, SidraQuery

EXPECTED_NAMES = {
    "Geofabrik",
    "Overpass",
    "OpenFreeMap",
    "CARTO",
    "IBGE SIDRA",
    "IBGE CNEFE",
    "Webots",
}


def test_settings_external_integrations_are_optional_and_canonical() -> None:
    settings = Settings()

    assert settings.overpass_api_url == "https://overpass-api.de/api/interpreter"
    assert settings.geofabrik_sudeste_pbf_url.endswith("/sudeste-latest.osm.pbf")
    assert settings.ibge_sidra_base_url == "https://apisidra.ibge.gov.br"
    assert settings.ibge_sidra_municipality_code is None
    assert settings.external_http_timeout_seconds == 15
    assert not hasattr(settings, "vite_carto_basemaps_api_key")


def test_settings_external_context_urls_accept_canonical_environment_overrides() -> None:
    settings = Settings(
        OPEN_METEO_FORECAST_URL="https://forecast.example/v1",
        OPEN_METEO_ARCHIVE_URL="https://archive.example/v1",
        NOMINATIM_BASE_URL="https://nominatim.example",
        GEOSAMPA_WFS_URL="https://geosampa.example/wfs",
        GEOSAMPA_WMS_URL="https://geosampa.example/wms",
        BRASIL_API_BASE_URL="https://brasil.example/api",
        VIACEP_BASE_URL="https://viacep.example/ws",
    )

    assert settings.open_meteo_forecast_url == "https://forecast.example/v1"
    assert settings.open_meteo_archive_url == "https://archive.example/v1"
    assert settings.nominatim_base_url == "https://nominatim.example"
    assert settings.geosampa_wfs_url == "https://geosampa.example/wfs"
    assert settings.geosampa_wms_url == "https://geosampa.example/wms"
    assert settings.brasil_api_base_url == "https://brasil.example/api"
    assert settings.viacep_base_url == "https://viacep.example/ws"

    official_http = Settings(
        GEOSAMPA_WFS_URL="http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs",
        GEOSAMPA_WMS_URL="http://wms.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wms",
    )
    assert official_http.geosampa_wfs_url.startswith("https://")
    assert official_http.geosampa_wms_url.startswith("https://")


def test_sidra_municipality_setting_is_explicit_and_validated() -> None:
    assert Settings(IBGE_SIDRA_MUNICIPALITY_CODE="3550308").ibge_sidra_municipality_code == (
        "3550308"
    )
    assert Settings(IBGE_SIDRA_MUNICIPALITY_CODE="").ibge_sidra_municipality_code is None
    with pytest.raises(ValidationError):
        Settings(IBGE_SIDRA_MUNICIPALITY_CODE="355030")


def test_external_http_redacts_secret_query_parameters() -> None:
    redacted = redacted_url(
        "https://example.test/style?key=carto-public&token=hf-private&visible=yes#fragment"
    )

    assert "carto-public" not in redacted
    assert "hf-private" not in redacted
    assert "visible=yes" in redacted
    assert "fragment" not in redacted


def test_registry_preserves_original_integrations_and_includes_runtime_sources(
    repo_root: Path,
) -> None:
    entries = integration_registry(Settings(), project_root=repo_root)

    names = {entry.name for entry in entries}
    assert EXPECTED_NAMES <= names
    assert {"Supabase", "Nominatim", "Open-Meteo", "GeoSampa", "BrasilAPI", "ViaCEP"} <= names
    assert len(entries) == len(names)
    assert {entry.name: entry.kind for entry in entries}["Geofabrik"] == "bulk_data_source"
    assert {entry.name: entry.kind for entry in entries}["Overpass"] == "runtime_api"
    assert {entry.name: entry.kind for entry in entries}["Webots"] == "simulator"


def test_runtime_registry_exposes_actual_policy_without_claiming_health(tmp_path: Path) -> None:
    from dataclasses import asdict

    from app.services.context import NominatimReverse

    settings = Settings(
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="must-not-leak",
        URMIND_EXTERNAL_HTTP_TIMEOUT_SECONDS=7,
    )
    entries = {entry.name: entry for entry in integration_registry(settings, project_root=tmp_path)}
    for name in ("Supabase", "Nominatim", "Open-Meteo", "GeoSampa", "BrasilAPI", "ViaCEP"):
        policy = entries[name].operations
        assert policy is not None
        assert policy.health_status == policy.last_success == policy.last_failure == "UNKNOWN"
        assert policy.privacy and policy.applicability and policy.implementation
        assert policy.retry_policy and policy.cache_policy
        assert "must-not-leak" not in str(asdict(entries[name]))
        if name != "Supabase":
            assert policy.timeout_seconds == 7
    assert entries["Nominatim"].operations.cache_ttl_seconds == NominatimReverse.cache.ttl_s
    assert entries["Supabase"].operations.timeout_seconds is None  # per subsystem, not invented
    assert all(entries[name].operations is None for name in EXPECTED_NAMES)


def test_registry_keeps_optional_auth_and_future_tools_non_blocking(repo_root: Path) -> None:
    entries = {
        entry.name: entry for entry in integration_registry(Settings(), project_root=repo_root)
    }

    assert entries["CARTO"].status == "FRONTEND_CONFIG_UNKNOWN"
    assert entries["CARTO"].configured is None
    assert entries["CARTO"].provenance == "browser_map; frontend_config_not_observable"
    assert entries["IBGE SIDRA"].status == "TERRITORY_CONFIG_REQUIRED"
    assert entries["IBGE SIDRA"].configured is False
    assert entries["Webots"].status in {"PREPARED", "NOT_INSTALLED_PREPARED"}
    assert entries["Geofabrik"].tool_status in {"AVAILABLE", "TOOL_NOT_INSTALLED"}
    assert entries["Webots"].tool_status in {"AVAILABLE", "TOOL_NOT_INSTALLED"}
    assert "RDD2022" not in entries

    configured = {
        entry.name: entry
        for entry in integration_registry(
            Settings(IBGE_SIDRA_MUNICIPALITY_CODE="3550308"),
            project_root=repo_root,
        )
    }
    assert configured["IBGE SIDRA"].status == "CONFIGURED"
    assert configured["IBGE SIDRA"].configured is True


def test_registry_does_not_promote_empty_manifests(tmp_path: Path) -> None:
    (tmp_path / "datasets/manifests").mkdir(parents=True)
    (tmp_path / "datasets/splits").mkdir(parents=True)
    (tmp_path / "datasets/metadata").mkdir(parents=True)
    (tmp_path / "datasets/manifests/univali_br.json").write_text("{}", encoding="utf-8")
    (tmp_path / "datasets/splits/univali_br_external_test_splits.json").write_text(
        "{}", encoding="utf-8"
    )

    entries = {
        entry.name: entry for entry in integration_registry(Settings(), project_root=tmp_path)
    }



@pytest.mark.asyncio
async def test_external_http_retries_429_and_5xx_with_correlation_id() -> None:
    statuses = iter((429, 503, 200))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(statuses), json={"ok": True})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(
        client=raw,
        timeout_seconds=1,
        user_agent="UrMind/Test",
        max_attempts=3,
        backoff_seconds=0,
    )
    try:
        with capture_logs() as logs:
            response = await client.get("https://example.test/data", provider="overpass")
    finally:
        await raw.aclose()

    assert response.status_code == 200
    assert len(requests) == 3
    assert all(request.headers["User-Agent"] == "UrMind/Test" for request in requests)
    correlation_ids = {request.headers["X-Correlation-ID"] for request in requests}
    assert len(correlation_ids) == 1
    correlation_id = next(iter(correlation_ids))
    retry_logs = [item for item in logs if item["event"] == "external_http_retry"]
    completed = [item for item in logs if item["event"] == "external_http_request_completed"]
    assert len(retry_logs) == 2
    assert len(completed) == 1
    assert completed[0]["attempt"] == 3
    assert completed[0]["outcome"] == "success"
    assert {item["correlation_id"] for item in [*retry_logs, *completed]} == {correlation_id}


@pytest.mark.asyncio
async def test_external_http_success_attempt_one_is_observable_without_secrets() -> None:
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    )
    client = ExternalHttpClient(client=raw)
    try:
        with capture_logs() as logs:
            response = await client.get(
                "https://example.test/style?key=do-not-log&visible=yes",
                provider="openfreemap",
            )
    finally:
        await raw.aclose()

    completed = [item for item in logs if item["event"] == "external_http_request_completed"]
    assert len(completed) == 1
    assert completed[0]["provider"] == "openfreemap"
    assert completed[0]["method"] == "GET"
    assert completed[0]["endpoint"] == "https://example.test/style"
    assert completed[0]["status_code"] == 200
    assert completed[0]["attempt"] == 1
    assert completed[0]["duration_ms"] >= 0
    assert completed[0]["outcome"] == "success"
    assert completed[0]["timestamp"]
    assert completed[0]["correlation_id"] == response.extensions["urmind_correlation_id"]
    assert "do-not-log" not in json.dumps(logs)


@pytest.mark.asyncio
async def test_external_http_reuses_one_supplied_correlation_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw)
    try:
        with capture_logs() as logs:
            response = await client.get(
                "https://example.test/data",
                provider="overpass",
                headers={"x-correlation-id": "upstream-request-id"},
            )
    finally:
        await raw.aclose()

    assert requests[0].headers.get_list("x-correlation-id") == ["upstream-request-id"]
    completed = next(item for item in logs if item["event"] == "external_http_request_completed")
    assert completed["correlation_id"] == "upstream-request-id"
    assert response.extensions["urmind_correlation_id"] == "upstream-request-id"


@pytest.mark.asyncio
async def test_external_http_final_failure_keeps_the_same_correlation_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("network unavailable", request=request)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, max_attempts=2, backoff_seconds=0)
    try:
        with capture_logs() as logs, pytest.raises(httpx.ConnectError):
            await client.get("https://example.test/failure?token=do-not-log", provider="sidra")
    finally:
        await raw.aclose()

    correlation_id = requests[0].headers["X-Correlation-ID"]
    assert {request.headers["X-Correlation-ID"] for request in requests} == {correlation_id}
    retry = next(item for item in logs if item["event"] == "external_http_retry")
    failure = next(item for item in logs if item["event"] == "external_http_request_completed")
    assert retry["correlation_id"] == correlation_id
    assert failure["correlation_id"] == correlation_id
    assert failure["outcome"] == "failure"
    assert failure["attempt"] == 2
    assert failure["status_code"] is None
    assert "do-not-log" not in json.dumps(logs)


@pytest.mark.asyncio
async def test_external_http_does_not_retry_ordinary_4xx() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, backoff_seconds=0)
    try:
        response = await client.get("https://example.test/missing")
    finally:
        await raw.aclose()

    assert response.status_code == 404
    assert calls == 1


@pytest.mark.asyncio
async def test_external_http_applies_the_central_timeout() -> None:
    seen_timeout: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeout.update(request.extensions["timeout"])
        return httpx.Response(200)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, timeout_seconds=7.5)
    try:
        await client.get("https://example.test/timeout")
    finally:
        await raw.aclose()

    assert set(seen_timeout.values()) == {7.5}


@pytest.mark.asyncio
async def test_external_download_removes_partial_file_on_checksum_failure(tmp_path: Path) -> None:
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"corrupt"))
    )
    client = ExternalHttpClient(client=raw, backoff_seconds=0)
    destination = tmp_path / "artifact.bin"
    try:
        with pytest.raises(ValueError, match="checksum"):
            await client.download(
                "https://example.test/artifact.bin",
                destination,
                hash_name="sha256",
                expected_digest="0" * 64,
            )
    finally:
        await raw.aclose()

    assert not destination.exists()
    assert not destination.with_name("artifact.bin.part").exists()


@pytest.mark.asyncio
async def test_sidra_without_explicit_territory_is_context_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = IbgeSidraProvider(ExternalHttpClient(client=raw))
    try:
        result = await provider.fetch(None)
    finally:
        await raw.aclose()

    assert result.status == "context_unavailable"
    assert result.error == "territory_required"
    assert calls == 0


def test_sidra_population_query_is_declarative_and_uses_latest_period() -> None:
    query = sidra_module.municipal_population_query("3550308")

    assert query == SidraQuery(
        table="6579",
        variable="9324",
        period="last 1",
        territorial_level="6",
        territorial_id="3550308",
    )
    with pytest.raises(ValueError, match="7 digitos"):
        sidra_module.municipal_population_query("355030")


@pytest.mark.asyncio
async def test_sidra_preserves_query_dimensions_and_raw_values() -> None:
    response_body = [
        {
            "NC": "Nivel Territorial (Codigo)",
            "NN": "Nivel Territorial",
            "MC": "Unidade de Medida (Codigo)",
            "MN": "Unidade de Medida",
            "V": "Valor",
            "D1C": "Municipio (Codigo)",
            "D1N": "Municipio",
            "D2C": "Variavel (Codigo)",
            "D2N": "Variavel",
            "D3C": "Ano (Codigo)",
            "D3N": "Ano",
        },
        {
            "NC": "6",
            "NN": "Municipio",
            "MC": "45",
            "MN": "Pessoas",
            "V": "123",
            "D1C": "3550308",
            "D1N": "Sao Paulo - SP",
            "D2C": "9324",
            "D2N": "Populacao residente",
            "D3C": "2022",
            "D3N": "2022",
        },
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response_body)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = IbgeSidraProvider(ExternalHttpClient(client=raw))
    query = SidraQuery(
        table="6579",
        variable="9324",
        period="2022",
        territorial_level="6",
        territorial_id="3550308",
    )
    try:
        result = await provider.fetch(query)
    finally:
        await raw.aclose()

    assert result.status == "ok"
    assert result.data["records"][0]["V"] == "123"
    assert result.provenance["table"] == "6579"
    assert result.provenance["territorial_id"] == "3550308"
    assert result.provenance["source_version"] == "2022"
    assert result.provenance["license"] == "IBGE - dados publicos"
    assert result.provenance["provider"] == "ibge_sidra"
    assert "/values/t/6579/n6/3550308/v/9324/p/2022" in str(seen[0].url)


@pytest.mark.asyncio
async def test_sidra_cache_is_scoped_to_the_configured_base_url() -> None:
    query = SidraQuery(
        table="6579",
        variable="9324",
        period="2022",
        territorial_level="6",
        territorial_id="3550308",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[{"V": "Valor"}, {"V": request.url.host}])

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw)
    try:
        first = await IbgeSidraProvider(client, "https://sidra-a.example").fetch(query)
        second = await IbgeSidraProvider(client, "https://sidra-b.example").fetch(query)
    finally:
        await raw.aclose()

    assert first.data["records"][0]["V"] == "sidra-a.example"
    assert second.data["records"][0]["V"] == "sidra-b.example"
    assert len(calls) == 2


def test_cnefe_refuses_download_without_territorial_selection() -> None:
    with pytest.raises(BulkSelectionError, match="UF ou municipio"):
        CnefeSelection()


def test_cnefe_municipality_must_match_selected_uf() -> None:
    with pytest.raises(BulkSelectionError, match="nao pertence"):
        CnefeSelection(uf="SP", municipality_code="3304557")


@pytest.mark.asyncio
async def test_cnefe_download_resolves_only_the_selected_municipality(tmp_path: Path) -> None:
    base = "https://ftp.example/Censo_Demografico_2022/"
    directory = base + "Arquivos_CNEFE/CSV/Municipio/35_SP/"
    archive_url = directory + "3550308_SAO_PAULO.zip"
    archive = b"zip-fixture"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == directory:
            return httpx.Response(
                200,
                text='<a href="3550308_SAO_PAULO.zip">arquivo</a>',
            )
        if str(request.url) == archive_url:
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, backoff_seconds=0)
    try:
        result = await download_cnefe(
            destination_dir=tmp_path,
            base_url=base,
            selection=CnefeSelection(uf="SP", municipality_code="3550308"),
            client=client,
        )
    finally:
        await raw.aclose()

    assert result.path.name == "3550308_SAO_PAULO.zip"
    assert result.path.read_bytes() == archive
    metadata = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert metadata["selection"] == {"uf": "SP", "municipality_code": "3550308"}
    assert metadata["sha256_local"] == hashlib.sha256(archive).hexdigest()


@pytest.mark.asyncio
async def test_geofabrik_download_is_streamed_verified_and_atomically_published(
    tmp_path: Path,
) -> None:
    payload = b"osm-pbf-fixture" * 257
    digest = hashlib.md5(payload).hexdigest()  # formato oficial da fonte
    pbf_url = "https://download.example/sudeste-latest.osm.pbf"
    md5_url = pbf_url + ".md5"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == md5_url:
            return httpx.Response(200, text=f"{digest}  sudeste-latest.osm.pbf\n")
        if str(request.url) == pbf_url:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExternalHttpClient(client=raw, backoff_seconds=0)
    try:
        result = await download_geofabrik(
            destination_dir=tmp_path,
            pbf_url=pbf_url,
            md5_url=md5_url,
            client=client,
        )
    finally:
        await raw.aclose()

    destination = tmp_path / "sudeste-latest.osm.pbf"
    manifest = tmp_path / "sudeste-latest.osm.pbf.provenance.json"
    assert destination.read_bytes() == payload
    assert not (tmp_path / "sudeste-latest.osm.pbf.part").exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["md5"] == digest
    assert result.file_size == len(payload)
