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
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx

USER_AGENT = "UrMind/0.1 (FECAP urban-maintenance research prototype)"
TIMEOUT_S = 10.0
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "context_unavailable"
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
POI_RADIUS_M = 300


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


class ContextProvider:
    source: str
    url: str
    cache: TTLCache

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    def cache_key(self, latitude: float, longitude: float, occurred_at: datetime) -> Any:
        # ~11 m: mesmo lugar para efeito de contexto, sem reconsultar a API.
        return (round(latitude, 4), round(longitude, 4))

    async def query(self, latitude: float, longitude: float, occurred_at: datetime) -> dict[str, Any]:
        raise NotImplementedError

    async def fetch(self, latitude: float, longitude: float, occurred_at: datetime) -> ContextResult:
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
        provenance = {"url": self.url, "user_agent": USER_AGENT, "cache": "miss"}
        try:
            data = await self.query(latitude, longitude, occurred_at)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Falha não entra no cache: a próxima ocorrência tenta de novo.
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=_now(),
                provenance=provenance,
                error=type(exc).__name__,
            )
        result = ContextResult(
            source=self.source, status=STATUS_OK, fetched_at=_now(), provenance=provenance, data=data
        )
        self.cache.put(key, result)
        return result

    async def _get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.client.get(url, timeout=TIMEOUT_S, **kwargs)
        response.raise_for_status()
        return response.json()


class NominatimReverse(ContextProvider):
    """Reverse geocoding de baixo volume (política pública do Nominatim/OSMF)."""

    source = "nominatim_reverse"
    url = "https://nominatim.openstreetmap.org/reverse"
    cache = TTLCache(ttl_s=7 * 24 * 3600)
    _locks: ClassVar[dict[int, asyncio.Lock]] = {}
    _last_request = 0.0

    @classmethod
    def _lock(cls) -> asyncio.Lock:
        # Uma trava por event loop: asyncio.Lock não pode ser compartilhado entre loops.
        loop_id = id(asyncio.get_running_loop())
        return cls._locks.setdefault(loop_id, asyncio.Lock())

    async def query(self, latitude: float, longitude: float, occurred_at: datetime) -> dict[str, Any]:
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
                headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"},
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

    async def query(self, latitude: float, longitude: float, occurred_at: datetime) -> dict[str, Any]:
        around = f"(around:{POI_RADIUS_M},{latitude},{longitude})"
        parts = "".join(
            f"{selector}{around};" for selectors in self.CATEGORIES.values() for selector in selectors
        )
        query = f"[out:json][timeout:25];({parts});out tags center;"
        for attempt in range(2):
            response = await self.client.post(
                self.url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S * 3
            )
            # Instância pública sobrecarregada responde 429/504: uma nova tentativa, sem insistir.
            if response.status_code not in (429, 502, 503, 504) or attempt == 1:
                break
            await asyncio.sleep(2.0)
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

    def cache_key(self, latitude: float, longitude: float, occurred_at: datetime) -> Any:
        # Chuva depende do instante: a hora da ocorrência entra na chave (~1 km de grade).
        hour = occurred_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return (round(latitude, 2), round(longitude, 2), hour.isoformat())

    async def query(self, latitude: float, longitude: float, occurred_at: datetime) -> dict[str, Any]:
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
        body = await self._get_json(url, params=params, headers={"User-Agent": USER_AGENT})
        hours = body["hourly"]["time"]
        values = body["hourly"]["precipitation"]
        window = [
            value
            for stamp, value in zip(hours, values, strict=True)
            if start <= datetime.fromisoformat(stamp).replace(tzinfo=UTC) <= end and value is not None
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


ProviderFactory = Callable[[httpx.AsyncClient], ContextProvider]
DEFAULT_PROVIDERS: tuple[ProviderFactory, ...] = (NominatimReverse, OverpassPois, OpenMeteoRain)


async def gather_context(
    latitude: float,
    longitude: float,
    occurred_at: datetime,
    *,
    providers: tuple[ProviderFactory, ...] = DEFAULT_PROVIDERS,
    client: httpx.AsyncClient | None = None,
) -> list[ContextResult]:
    """Consulta todos os providers; cada um falha isoladamente."""
    owned = client is None
    http = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        calls: list[Awaitable[ContextResult]] = [
            factory(http).fetch(latitude, longitude, occurred_at) for factory in providers
        ]
        return list(await asyncio.gather(*calls))
    finally:
        if owned:
            await http.aclose()
