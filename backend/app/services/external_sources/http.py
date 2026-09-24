"""Cliente HTTP compartilhado para serviços públicos externos."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import structlog

from app.config import Settings

log = structlog.get_logger()
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SECRET_QUERY_KEYS = frozenset({"key", "token", "api_key", "access_token"})
CORRELATION_ID_EXTENSION = "urmind_correlation_id"
_current_correlation_id: ContextVar[str | None] = ContextVar(
    "external_http_correlation_id", default=None
)


def redacted_url(url: str) -> str:
    """Mantém a URL útil no log sem expor credenciais em query string."""
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "[REDACTED]" if key.lower() in SECRET_QUERY_KEYS else value)
            for key, value in parse_qsl(parts.query)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def safe_endpoint(url: str) -> str:
    """Remove query e fragment; logs nunca recebem parâmetros de credencial."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


@dataclass(frozen=True)
class DownloadReceipt:
    destination: Path
    digest: str
    size_bytes: int
    correlation_id: str


class ExternalHttpClient:
    """Um client por operação, com retry finito e lifecycle explícito."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
        user_agent: str = "UrMind/1.0",
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts deve ser positivo")
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> ExternalHttpClient:
        return cls(
            timeout_seconds=settings.external_http_timeout_seconds,
            user_agent=settings.external_http_user_agent,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def correlation_id(self) -> str | None:
        """ID da operação externa mais recente na task assíncrona atual."""
        return _current_correlation_id.get()

    @staticmethod
    def _correlation_id() -> str:
        current = structlog.contextvars.get_contextvars().get("correlation_id")
        return str(current or uuid4())

    def _headers(self, supplied: dict[str, str] | None, correlation_id: str) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        if supplied:
            headers.update(supplied)
        for key in tuple(headers):
            if key.lower() == "x-correlation-id":
                del headers[key]
        headers["X-Correlation-ID"] = correlation_id
        return headers

    @staticmethod
    def _supplied_correlation_id(headers: dict[str, str] | None) -> str | None:
        if not headers:
            return None
        return next(
            (str(value) for key, value in headers.items() if key.lower() == "x-correlation-id"),
            None,
        )

    @staticmethod
    def _provider(url: str, supplied: str | None) -> str:
        return supplied or urlsplit(url).hostname or "external"

    @staticmethod
    def _log_fields(
        *,
        provider: str,
        method: str,
        url: str,
        status_code: int | None,
        attempt: int,
        started_at: float,
        correlation_id: str,
        outcome: str,
    ) -> dict[str, object]:
        parts = urlsplit(url)
        return {
            "provider": provider,
            "method": method.upper(),
            "host": parts.hostname or "",
            "endpoint": safe_endpoint(url),
            "status_code": status_code,
            "attempt": attempt,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            "correlation_id": correlation_id,
            "outcome": outcome,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def request(
        self,
        method: str,
        url: str,
        *,
        provider: str | None = None,
        attempts: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        supplied_headers = kwargs.pop("headers", None)
        correlation_id = self._supplied_correlation_id(supplied_headers) or self._correlation_id()
        _current_correlation_id.set(correlation_id)
        headers = self._headers(supplied_headers, correlation_id)
        timeout = kwargs.pop("timeout", self.timeout_seconds)
        provider_name = self._provider(url, provider)
        started_at = time.perf_counter()
        max_attempts = self.max_attempts if attempts is None else attempts
        if not 1 <= max_attempts <= self.max_attempts:
            raise ValueError("attempts must respect the configured retry bound")
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._client.request(
                    method, url, headers=headers, timeout=timeout, **kwargs
                )
            except httpx.TransportError as exc:
                if attempt == max_attempts:
                    log.error(
                        "external_http_request_completed",
                        **self._log_fields(
                            provider=provider_name,
                            method=method,
                            url=url,
                            status_code=None,
                            attempt=attempt,
                            started_at=started_at,
                            correlation_id=correlation_id,
                            outcome="failure",
                        ),
                        error=type(exc).__name__,
                    )
                    raise
                log.warning(
                    "external_http_retry",
                    **self._log_fields(
                        provider=provider_name,
                        method=method,
                        url=url,
                        status_code=None,
                        attempt=attempt,
                        started_at=started_at,
                        correlation_id=correlation_id,
                        outcome="retry",
                    ),
                    error=type(exc).__name__,
                )
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == max_attempts:
                    response.extensions[CORRELATION_ID_EXTENSION] = correlation_id
                    outcome = "success" if response.status_code < 400 else "failure"
                    log.info(
                        "external_http_request_completed",
                        **self._log_fields(
                            provider=provider_name,
                            method=method,
                            url=url,
                            status_code=response.status_code,
                            attempt=attempt,
                            started_at=started_at,
                            correlation_id=correlation_id,
                            outcome=outcome,
                        ),
                    )
                    return response
                log.warning(
                    "external_http_retry",
                    **self._log_fields(
                        provider=provider_name,
                        method=method,
                        url=url,
                        status_code=response.status_code,
                        attempt=attempt,
                        started_at=started_at,
                        correlation_id=correlation_id,
                        outcome="retry",
                    ),
                )
            await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise RuntimeError("retry loop terminou sem resposta")

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        hash_name: str,
        expected_digest: str | None = None,
        timeout: float | None = None,
        provider: str | None = None,
    ) -> DownloadReceipt:
        """Transfere em streaming para ``.part`` e publica por rename atômico."""
        destination = destination.resolve()
        temporary = destination.with_name(destination.name + ".part")
        if destination.exists():
            raise FileExistsError(destination)
        if temporary.exists():
            raise FileExistsError(f"download parcial já existe: {temporary}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        correlation_id = self._correlation_id()
        _current_correlation_id.set(correlation_id)
        headers = self._headers(None, correlation_id)
        request_timeout = timeout or self.timeout_seconds
        provider_name = self._provider(url, provider)
        started_at = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            digest = hashlib.new(hash_name)
            size = 0
            created = False
            status_code: int | None = None
            try:
                async with self._client.stream(
                    "GET", url, headers=headers, timeout=request_timeout
                ) as response:
                    status_code = response.status_code
                    if response.status_code in RETRYABLE_STATUS and attempt < self.max_attempts:
                        log.warning(
                            "external_download_retry",
                            **self._log_fields(
                                provider=provider_name,
                                method="GET",
                                url=url,
                                status_code=response.status_code,
                                attempt=attempt,
                                started_at=started_at,
                                correlation_id=correlation_id,
                                outcome="retry",
                            ),
                        )
                        await response.aclose()
                    else:
                        response.raise_for_status()
                        with temporary.open("xb") as output:
                            created = True
                            async for chunk in response.aiter_bytes():
                                output.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        actual = digest.hexdigest()
                        if expected_digest and actual.lower() != expected_digest.lower():
                            raise ValueError("checksum do download diverge da fonte")
                        os.replace(temporary, destination)
                        log.info(
                            "external_http_request_completed",
                            **self._log_fields(
                                provider=provider_name,
                                method="GET",
                                url=url,
                                status_code=response.status_code,
                                attempt=attempt,
                                started_at=started_at,
                                correlation_id=correlation_id,
                                outcome="success",
                            ),
                        )
                        return DownloadReceipt(destination, actual, size, correlation_id)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if created:
                    temporary.unlink(missing_ok=True)
                if attempt == self.max_attempts:
                    log.error(
                        "external_http_request_completed",
                        **self._log_fields(
                            provider=provider_name,
                            method="GET",
                            url=url,
                            status_code=status_code,
                            attempt=attempt,
                            started_at=started_at,
                            correlation_id=correlation_id,
                            outcome="failure",
                        ),
                        error=type(exc).__name__,
                    )
                    raise
                log.warning(
                    "external_download_retry",
                    **self._log_fields(
                        provider=provider_name,
                        method="GET",
                        url=url,
                        status_code=status_code,
                        attempt=attempt,
                        started_at=started_at,
                        correlation_id=correlation_id,
                        outcome="retry",
                    ),
                    error=type(exc).__name__,
                )
            except Exception as exc:
                if created:
                    temporary.unlink(missing_ok=True)
                log.error(
                    "external_http_request_completed",
                    **self._log_fields(
                        provider=provider_name,
                        method="GET",
                        url=url,
                        status_code=status_code,
                        attempt=attempt,
                        started_at=started_at,
                        correlation_id=correlation_id,
                        outcome="failure",
                    ),
                    error=type(exc).__name__,
                )
                raise
            await asyncio.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise RuntimeError("download terminou sem artefato")
