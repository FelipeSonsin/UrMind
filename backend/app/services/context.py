"""Contexto externo do Event (MASTER_PLAN §12.1, §13, §25 passo 10).

APIs públicas **enriquecem** o evento e nunca são requisito para ele existir. Cada
provider devolve um `ContextResult` com fonte, instante, proveniência e estado; falha
de rede ou de formato vira `context_unavailable`, nunca exceção para quem chama.

Regras de uso respeitadas aqui: timeout curto, cache por coordenada arredondada,
User-Agent identificando o aplicativo, atribuição OSM e — para o Nominatim — no
máximo 1 requisição por segundo, sem uso em lote. Endereço é contexto: a coordenada
do evento nunca é substituída por ele.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import structlog

from app.config import get_settings
from app.services.external_sources.http import ExternalHttpClient

if TYPE_CHECKING:
    from app.services.external_sources.sidra import SidraQuery

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "context_unavailable"
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
POI_RADIUS_M = 300
GEOSAMPA_BBOX_RADIUS_M = 75
log = structlog.get_logger()


@dataclass(frozen=True)
class ContextResult:
    source: str
    status: str
    fetched_at: str
    provenance: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fetched_at": self.fetched_at,
            "provenance": self.provenance,
            "data": self.data,
            "error": self.error,
        }


class TTLCache:
    """Cache em processo por chave, com validade e tamanho máximo."""

    def __init__(self, ttl_s: float, max_items: int = 512) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._items: OrderedDict[Any, tuple[float, ContextResult]] = OrderedDict()

    def get(self, key: Any) -> ContextResult | None:
        item = self._items.get(key)
        if item is None or time.monotonic() - item[0] > self.ttl_s:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return item[1]

    def put(self, key: Any, value: ContextResult) -> None:
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _external_client(client: httpx.AsyncClient | ExternalHttpClient) -> ExternalHttpClient:
    settings = get_settings()
    return (
        client
        if isinstance(client, ExternalHttpClient)
        else ExternalHttpClient(
            client=client,
            timeout_seconds=settings.external_http_timeout_seconds,
            user_agent=settings.external_http_user_agent,
        )
    )


class ContextProvider:
    source: str
    url: str
    cache: TTLCache

    def __init__(self, client: httpx.AsyncClient | ExternalHttpClient) -> None:
        self.client = _external_client(client)

    def cache_key(self, latitude: float, longitude: float, occurred_at: datetime) -> Any:
        # ~11 m: mesmo lugar para efeito de contexto, sem reconsultar a API.
        return (self.url, round(latitude, 4), round(longitude, 4))

    async def query(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def fetch(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> ContextResult:
        key = self.cache_key(latitude, longitude, occurred_at)
        cached = self.cache.get(key)
        if cached is not None:
            return ContextResult(
                source=cached.source,
                status=cached.status,
                fetched_at=cached.fetched_at,
                provenance={**cached.provenance, "cache": "hit"},
                data=cached.data,
            )
        retrieved_at = _now()
        provenance = {
            "provider": self.source,
            "source": self.source,
            "url": self.url,
            "user_agent": self.client.user_agent,
            "retrieved_at": retrieved_at,
            "cache": "miss",
        }
        try:
            data = await self.query(latitude, longitude, occurred_at)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            if correlation_id := self.client.correlation_id:
                provenance["correlation_id"] = correlation_id
            # Falha não entra no cache: a próxima ocorrência tenta de novo.
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=retrieved_at,
                provenance=provenance,
                error=type(exc).__name__,
            )
        if correlation_id := self.client.correlation_id:
            provenance["correlation_id"] = correlation_id
        result = ContextResult(
            source=self.source,
            status=STATUS_OK,
            fetched_at=retrieved_at,
            provenance=provenance,
            data=data,
        )
        self.cache.put(key, result)
        return result

    async def _get_json(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("provider", self.source)
        response = await self.client.get(url, **kwargs)
        response.raise_for_status()
        return response.json()


class NominatimReverse(ContextProvider):
    """Reverse geocoding de baixo volume (política pública do Nominatim/OSMF)."""

    source = "nominatim_reverse"
    url = "https://nominatim.openstreetmap.org/reverse"
    cache = TTLCache(ttl_s=7 * 24 * 3600)
    _locks: ClassVar[dict[int, asyncio.Lock]] = {}
    _last_request = 0.0

    def __init__(
        self,
        client: httpx.AsyncClient | ExternalHttpClient,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(client)
        base = (base_url or get_settings().nominatim_base_url).rstrip("/")
        self.url = base if base.endswith("/reverse") else f"{base}/reverse"

    @classmethod
    def _lock(cls) -> asyncio.Lock:
        # Uma trava por event loop: asyncio.Lock não pode ser compartilhado entre loops.
        loop_id = id(asyncio.get_running_loop())
        return cls._locks.setdefault(loop_id, asyncio.Lock())

    async def query(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> dict[str, Any]:
        async with NominatimReverse._lock():
            wait = 1.0 - (time.monotonic() - NominatimReverse._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            NominatimReverse._last_request = time.monotonic()
            body = await self._get_json(
                self.url,
                params={
                    "lat": latitude,
                    "lon": longitude,
                    "format": "jsonv2",
                    "zoom": 18,
                    "addressdetails": 1,
                },
                headers={"Accept-Language": "pt-BR"},
            )
        if "error" in body:
            raise ValueError("nominatim sem resultado")
        address = body.get("address") or {}
        return {
            "display_name": body["display_name"],
            "road": address.get("road"),
            "suburb": address.get("suburb"),
            "city": address.get("city") or address.get("town") or address.get("municipality"),
            "state": address.get("state"),
            "postcode": address.get("postcode"),
            "osm_type": body.get("osm_type"),
            "osm_id": body.get("osm_id"),
            "attribution": OSM_ATTRIBUTION,
            "is_coordinate_source": False,
        }


class OverpassPois(ContextProvider):
    """Escola, saúde e travessia num raio curto (recorte pequeno, com cache)."""

    source = "overpass_pois"
    url = "https://overpass-api.de/api/interpreter"
    cache = TTLCache(ttl_s=24 * 3600)
    CATEGORIES: ClassVar[dict[str, tuple[str, ...]]] = {
        "school": ('nwr["amenity"~"^(school|kindergarten)$"]',),
        "health": ('nwr["amenity"~"^(hospital|clinic|doctors)$"]', 'nwr["healthcare"]'),
        "crossing": ('node["highway"="crossing"]', 'node["crossing"]'),
    }

    def __init__(
        self,
        client: httpx.AsyncClient | ExternalHttpClient,
        *,
        url: str | None = None,
    ) -> None:
        super().__init__(client)
        self.url = url or get_settings().overpass_api_url

    async def query(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> dict[str, Any]:
        around = f"(around:{POI_RADIUS_M},{latitude},{longitude})"
        parts = "".join(
            f"{selector}{around};"
            for selectors in self.CATEGORIES.values()
            for selector in selectors
        )
        query = f"[out:json][timeout:25];({parts});out tags center;"
        response = await self.client.post(
            self.url,
            data={"data": query},
            timeout=get_settings().external_http_timeout_seconds * 3,
            provider="overpass",
        )
        response.raise_for_status()
        body = response.json()
        nearest: dict[str, dict[str, Any] | None] = dict.fromkeys(self.CATEGORIES)
        for element in body["elements"]:
            lat = element.get("lat", (element.get("center") or {}).get("lat"))
            lon = element.get("lon", (element.get("center") or {}).get("lon"))
            if lat is None or lon is None:
                continue
            tags = element.get("tags") or {}
            category = (
                "school"
                if tags.get("amenity") in ("school", "kindergarten")
                else "health"
                if tags.get("amenity") in ("hospital", "clinic", "doctors") or "healthcare" in tags
                else "crossing"
            )
            distance = _distance_m(latitude, longitude, lat, lon)
            current = nearest[category]
            if current is None or distance < current["distance_m"]:
                nearest[category] = {
                    "distance_m": round(distance, 1),
                    "name": tags.get("name"),
                    "osm": f"{element['type']}/{element['id']}",
                }
        return {
            "radius_m": POI_RADIUS_M,
            "nearest": nearest,
            "osm_base": (body.get("osm3s") or {}).get("timestamp_osm_base"),
            "attribution": OSM_ATTRIBUTION,
        }


class OpenMeteoRain(ContextProvider):
    """Chuva acumulada nas 24 h anteriores à ocorrência (Open-Meteo, sem chave)."""

    source = "open_meteo_rain"
    url = "https://api.open-meteo.com/v1/forecast"
    archive_url = "https://archive-api.open-meteo.com/v1/archive"
    cache = TTLCache(ttl_s=3600)

    def __init__(
        self,
        client: httpx.AsyncClient | ExternalHttpClient,
        *,
        forecast_url: str | None = None,
        archive_url: str | None = None,
    ) -> None:
        super().__init__(client)
        settings = get_settings()
        self.url = (forecast_url or settings.open_meteo_forecast_url).rstrip("/")
        self.archive_url = (archive_url or settings.open_meteo_archive_url).rstrip("/")

    def cache_key(self, latitude: float, longitude: float, occurred_at: datetime) -> Any:
        # Chuva depende do instante: a hora da ocorrência entra na chave (~1 km de grade).
        hour = occurred_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return (
            self.url,
            self.archive_url,
            round(latitude, 2),
            round(longitude, 2),
            hour.isoformat(),
        )

    async def query(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> dict[str, Any]:
        end = occurred_at.astimezone(UTC)
        start = end - timedelta(hours=24)
        recent = datetime.now(UTC) - end < timedelta(days=80)
        url = self.url if recent else self.archive_url
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation",
            "timezone": "UTC",
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        body = await self._get_json(url, params=params)
        hours = body["hourly"]["time"]
        values = body["hourly"]["precipitation"]
        window = [
            value
            for stamp, value in zip(hours, values, strict=True)
            if start <= datetime.fromisoformat(stamp).replace(tzinfo=UTC) <= end
            and value is not None
        ]
        if not window:
            raise ValueError("sem horas de precipitação na janela")
        return {
            "rain_mm_24h": round(sum(window), 2),
            "hours_observed": len(window),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "endpoint": "forecast" if recent else "archive",
            "attribution": "Open-Meteo.com (CC BY 4.0)",
        }


class GeoSampaSidewalks(ContextProvider):
    """Calçadas oficiais próximas ao evento, sem inferência ou peso de risco."""

    source = "geosampa_sidewalks"
    url = "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs"
    layer = "geoportal:calcada"
    cache = TTLCache(ttl_s=24 * 3600)

    def __init__(
        self,
        client: httpx.AsyncClient | ExternalHttpClient,
        *,
        url: str | None = None,
    ) -> None:
        super().__init__(client)
        self.url = (url or get_settings().geosampa_wfs_url).rstrip("/")

    async def query(
        self, latitude: float, longitude: float, occurred_at: datetime
    ) -> dict[str, Any]:
        lat_delta = GEOSAMPA_BBOX_RADIUS_M / 111_320
        lon_scale = max(math.cos(math.radians(latitude)), 0.01)
        lon_delta = GEOSAMPA_BBOX_RADIUS_M / (111_320 * lon_scale)
        bbox = (
            f"{longitude - lon_delta},{latitude - lat_delta},"
            f"{longitude + lon_delta},{latitude + lat_delta},EPSG:4326"
        )
        body = await self._get_json(
            self.url,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": self.layer,
                "srsName": "EPSG:4326",
                "bbox": bbox,
                "count": "10",
                "outputFormat": "application/json",
            },
        )
        if not isinstance(body, dict) or not isinstance(body.get("features"), list):
            raise TypeError("resposta GeoSampa sem FeatureCollection")
        features = []
        for feature in body["features"][:10]:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise TypeError("feature GeoSampa malformada")
            properties = feature["properties"]
            features.append(
                {
                    "id": feature.get("id"),
                    "road": properties.get("nm_logradouro"),
                    "situation": properties.get("tx_situacao"),
                    "average_width_m": properties.get("qt_largura_media_trecho"),
                    "average_slope_percent": properties.get("pc_declividade_media_trecho"),
                }
            )
        matched = body.get("numberMatched")
        return {
            "layer": self.layer,
            "bbox_radius_m": GEOSAMPA_BBOX_RADIUS_M,
            "matched": matched if isinstance(matched, int) else len(features),
            "features": features,
        }


class AdministrativeLocationProvider:
    """Valida CEP existente: BrasilAPI primária e ViaCEP apenas como fallback."""

    source = "administrative_location"
    cache = TTLCache(ttl_s=30 * 24 * 3600)

    def __init__(
        self,
        client: httpx.AsyncClient | ExternalHttpClient,
        *,
        brasil_api_base_url: str | None = None,
        viacep_base_url: str | None = None,
    ) -> None:
        self.client = _external_client(client)
        settings = get_settings()
        self.brasil_api_base_url = (brasil_api_base_url or settings.brasil_api_base_url).rstrip("/")
        self.viacep_base_url = (viacep_base_url or settings.viacep_base_url).rstrip("/")

    @staticmethod
    def _result_data(payload: dict[str, Any], provider: str) -> dict[str, Any]:
        if provider == "brasilapi":
            city, state = payload.get("city"), payload.get("state")
            data = {
                "postcode": payload.get("cep"),
                "road": payload.get("street"),
                "neighborhood": payload.get("neighborhood"),
                "city": city,
                "state": state,
                "municipality_code": None,
                "resolved_by": provider,
            }
        else:
            city, state = payload.get("localidade"), payload.get("uf")
            data = {
                "postcode": payload.get("cep"),
                "road": payload.get("logradouro"),
                "neighborhood": payload.get("bairro"),
                "city": city,
                "state": state,
                "municipality_code": payload.get("ibge"),
                "resolved_by": provider,
            }
        if not city or not state:
            raise ValueError("resposta administrativa sem municipio e UF")
        return data

    async def fetch(self, postcode: str | None) -> ContextResult:
        fetched_at = _now()
        normalized = re.sub(r"\D", "", postcode or "")
        if not normalized:
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=fetched_at,
                provenance={
                    "provider": self.source,
                    "source": self.source,
                    "retrieved_at": fetched_at,
                    "cache": "miss",
                },
                error="postcode_required",
            )
        if not re.fullmatch(r"\d{8}", normalized):
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=fetched_at,
                provenance={
                    "provider": self.source,
                    "source": self.source,
                    "retrieved_at": fetched_at,
                    "cache": "miss",
                },
                error="invalid_postcode",
            )
        cache_key = (self.brasil_api_base_url, self.viacep_base_url, normalized)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return ContextResult(
                source=cached.source,
                status=cached.status,
                fetched_at=cached.fetched_at,
                provenance={**cached.provenance, "cache": "hit"},
                data=cached.data,
            )
        provenance: dict[str, Any] = {
            "provider": self.source,
            "source": self.source,
            "retrieved_at": fetched_at,
            "cache": "miss",
            "attempted": [],
        }
        last_error: Exception | None = None
        sources = (
            (
                "brasilapi",
                f"{self.brasil_api_base_url}/cep/v2/{normalized}",
            ),
            ("viacep", f"{self.viacep_base_url}/{normalized}/json/"),
        )
        for provider, url in sources:
            provenance["attempted"].append(provider)
            try:
                response = await self.client.get(url, provider=provider)
                if correlation_id := response.extensions.get("urmind_correlation_id"):
                    provenance["correlation_id"] = correlation_id
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("erro") is True:
                    raise ValueError("resposta administrativa invalida")
                data = self._result_data(payload, provider)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = exc
                continue
            provenance["resolved_by"] = provider
            result = ContextResult(
                source=self.source,
                status=STATUS_OK,
                fetched_at=fetched_at,
                provenance=provenance,
                data=data,
            )
            self.cache.put(cache_key, result)
            return result
        if correlation_id := self.client.correlation_id:
            provenance["correlation_id"] = correlation_id
        return ContextResult(
            source=self.source,
            status=STATUS_UNAVAILABLE,
            fetched_at=fetched_at,
            provenance=provenance,
            error=type(last_error).__name__ if last_error else "unavailable",
        )


ProviderFactory = Callable[[ExternalHttpClient], ContextProvider]
DEFAULT_PROVIDERS: tuple[ProviderFactory, ...] = (
    NominatimReverse,
    OverpassPois,
    OpenMeteoRain,
    GeoSampaSidewalks,
)


async def gather_context(
    latitude: float,
    longitude: float,
    occurred_at: datetime,
    *,
    providers: tuple[ProviderFactory, ...] = DEFAULT_PROVIDERS,
    client: httpx.AsyncClient | ExternalHttpClient | None = None,
    include_sidra: bool = False,
    sidra_query: SidraQuery | None = None,
    include_administrative: bool = False,
) -> list[ContextResult]:
    """Consulta todos os providers; cada um falha isoladamente."""
    owned = client is None
    settings = get_settings()
    if client is None:
        http = ExternalHttpClient.from_settings(settings)
    elif isinstance(client, ExternalHttpClient):
        http = client
    else:
        http = ExternalHttpClient(
            client=client,
            timeout_seconds=settings.external_http_timeout_seconds,
            user_agent=settings.external_http_user_agent,
        )
    try:
        calls: list[Awaitable[ContextResult]] = [
            factory(http).fetch(latitude, longitude, occurred_at) for factory in providers
        ]
        if include_sidra:
            from app.services.external_sources.sidra import IbgeSidraProvider

            calls.append(IbgeSidraProvider(http, settings.ibge_sidra_base_url).fetch(sidra_query))
        results = list(await asyncio.gather(*calls))
        if include_administrative:
            nominatim = next(
                (
                    result
                    for result in results
                    if result.source == "nominatim_reverse" and result.status == STATUS_OK
                ),
                None,
            )
            postcode = nominatim.data.get("postcode") if nominatim is not None else None
            results.append(await AdministrativeLocationProvider(http).fetch(postcode))
        for result in results:
            log.info(
                "external_context_result",
                provider=result.source,
                correlation_id=result.provenance.get("correlation_id"),
                cache=result.provenance.get("cache"),
                result_status=result.status,
            )
        return results
    finally:
        if owned:
            await http.aclose()
