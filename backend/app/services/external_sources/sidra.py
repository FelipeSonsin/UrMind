"""Provider territorial do IBGE SIDRA com território explícito e cache longo."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.services.context import STATUS_OK, STATUS_UNAVAILABLE, ContextResult, TTLCache
from app.services.external_sources.http import ExternalHttpClient


@dataclass(frozen=True)
class SidraQuery:
    table: str
    variable: str
    period: str
    territorial_level: str
    territorial_id: str

    def __post_init__(self) -> None:
        numeric = (self.table, self.variable, self.territorial_level, self.territorial_id)
        if not all(re.fullmatch(r"\d+", value) for value in numeric):
            raise ValueError(
                "tabela, variavel, nivel e territorio SIDRA devem ser codigos numericos"
            )
        if not re.fullmatch(r"(?:\d{4}|last [1-9]\d*)", self.period):
            raise ValueError("periodo SIDRA invalido")


def municipal_population_query(municipality_code: str) -> SidraQuery:
    """Estimativa populacional municipal mais recente publicada na tabela 6579."""
    if not re.fullmatch(r"\d{7}", municipality_code):
        raise ValueError("codigo de municipio SIDRA deve ter 7 digitos")
    return SidraQuery(
        table="6579",
        variable="9324",
        period="last 1",
        territorial_level="6",
        territorial_id=municipality_code,
    )


class IbgeSidraProvider:
    source = "ibge_sidra"
    cache = TTLCache(ttl_s=30 * 24 * 3600, max_items=256)

    def __init__(
        self,
        client: ExternalHttpClient,
        base_url: str = "https://apisidra.ibge.gov.br",
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    async def fetch(self, query: SidraQuery | None) -> ContextResult:
        fetched_at = datetime.now(UTC).isoformat()
        if query is None:
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=fetched_at,
                provenance={
                    "provider": self.source,
                    "source": "IBGE SIDRA",
                    "retrieved_at": fetched_at,
                },
                error="territory_required",
            )
        cache_key = (self.base_url, query)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return ContextResult(
                source=cached.source,
                status=cached.status,
                fetched_at=cached.fetched_at,
                provenance={**cached.provenance, "cache": "hit"},
                data=cached.data,
            )
        url = (
            f"{self.base_url}/values/t/{quote(query.table)}/"
            f"n{quote(query.territorial_level)}/{quote(query.territorial_id)}/"
            f"v/{quote(query.variable)}/p/{quote(query.period)}"
        )
        provenance: dict[str, Any] = {
            "provider": self.source,
            "source": "IBGE SIDRA",
            "source_url": url,
            "source_version": query.period,
            "license": "IBGE - dados publicos",
            "table": query.table,
            "variable": query.variable,
            "period": query.period,
            "territorial_level": query.territorial_level,
            "territorial_id": query.territorial_id,
            "retrieved_at": fetched_at,
            "cache": "miss",
        }
        try:
            response = await self.client.get(
                url,
                params={"formato": "json"},
                provider=self.source,
            )
            if correlation_id := response.extensions.get("urmind_correlation_id"):
                provenance["correlation_id"] = correlation_id
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, list)
                or len(payload) < 2
                or not isinstance(payload[0], dict)
            ):
                raise ValueError("resposta SIDRA sem cabecalho e registros")
            columns = payload[0]
            records = payload[1:]
            if not all(isinstance(row, dict) for row in records):
                raise ValueError("registro SIDRA malformado")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            if correlation_id := self.client.correlation_id:
                provenance["correlation_id"] = correlation_id
            return ContextResult(
                source=self.source,
                status=STATUS_UNAVAILABLE,
                fetched_at=fetched_at,
                provenance=provenance,
                error=type(exc).__name__,
            )
        result = ContextResult(
            source=self.source,
            status=STATUS_OK,
            fetched_at=fetched_at,
            provenance=provenance,
            data={"columns": columns, "records": records},
        )
        self.cache.put(cache_key, result)
        return result
