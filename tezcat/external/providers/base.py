"""Read-only provider contracts and bounded HTTP transport."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol

import httpx

from tezcat.external.schemas import ExternalDataError, ProviderSchemaError


class ProviderHttpError(ExternalDataError):
    """A provider request failed or returned an invalid transport response."""


class ProviderRateLimitError(ProviderHttpError):
    """The provider or local budget rejected a request for rate reasons."""


@dataclass(frozen=True)
class ProviderPage:
    items: List[Dict[str, Any]]
    cursor: Optional[str] = None
    raw: Dict[str, Any] = None


class EventMarketProvider(Protocol):
    provider_id: str
    adapter_version: str

    def list_events(self, *, cursor: Optional[str] = None, limit: int = 100,
                    status: Optional[str] = None) -> ProviderPage: ...

    def list_markets(self, *, cursor: Optional[str] = None, limit: int = 100,
                     status: Optional[str] = None, event_id: Optional[str] = None) -> ProviderPage: ...

    def get_market(self, market_id: str) -> Dict[str, Any]: ...

    def get_orderbook(self, market_id: str, **kwargs: Any) -> Dict[str, Any]: ...

    def get_trades(self, market_id: str, **kwargs: Any) -> ProviderPage: ...

    def get_history(self, market_id: str, **kwargs: Any) -> ProviderPage: ...


class ReadOnlyHttpClient:
    """Small, bounded transport: no auth signing and no mutating methods."""

    def __init__(self, base_url: str, *, timeout: float = 20.0,
                 min_interval: float = 0.05, max_retries: int = 2,
                 client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval = max(0.0, min_interval)
        self.max_retries = max(0, max_retries)
        self._last_request = 0.0
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def _wait(self) -> None:
        delay = self.min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        if not path.startswith("/"):
            raise ValueError("provider path must start with '/'")
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            try:
                response = self._client.get(self.base_url + path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                raise ProviderHttpError(f"GET {path} failed: {exc}") from exc
            if response.status_code == 429:
                if attempt < self.max_retries:
                    retry_after = response.headers.get("retry-after", "0.5")
                    try:
                        wait = min(10.0, max(0.05, float(retry_after)))
                    except ValueError:
                        wait = 0.5
                    time.sleep(wait)
                    continue
                raise ProviderRateLimitError(f"GET {path} rate limited")
            if response.status_code >= 500 and attempt < self.max_retries:
                time.sleep(0.2 * (2 ** attempt))
                continue
            if response.status_code >= 400:
                raise ProviderHttpError(f"GET {path} returned HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderSchemaError(f"GET {path} returned malformed JSON") from exc
            if not isinstance(body, (dict, list)):
                raise ProviderSchemaError(f"GET {path} returned non-object/list JSON")
            return body
        raise ProviderHttpError(f"GET {path} failed: {last_error}")

    def get_text(self, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
        self._wait()
        try:
            response = self._client.get(self.base_url + path, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderHttpError(f"GET {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderHttpError(f"GET {path} returned HTTP {response.status_code}")
        return response.text
