"""Provider abstraction for external event markets (Phase S3).

An :class:`EventMarketProvider` adapter turns one provider's HTTP API into
the canonical schema of ``tezcat.external.schema``. Provider-specific
response shapes must never leak past an adapter.

Design rules
------------
* **Read-only.** The protocol has no order, account, or wallet surface —
  deliberately. Even where a provider SDK exposes trading, adapters must
  not use it.
* **Transport injection.** Adapters speak to a :class:`Transport`, not to
  the network directly. Production uses :class:`UrllibTransport`; the test
  suite uses a fixture transport — the whole layer runs offline
  (``pytest`` never touches the network).
* **Explicit failure taxonomy.** HTTP errors, timeouts, rate limits,
  malformed payloads, and schema drift raise distinct exceptions.
  Adapters never fabricate missing observations.
* **Rate-limit respect.** Every adapter throttles through a token bucket
  and backs off exponentially on retryable failures. Never hammer the
  providers.

Credentials: public market-data endpoints require none, and the default
configuration sends none. Credentialed access (if a provider ever requires
it for a read-only endpoint) comes from environment variables server-side
only — never from experiment JSON, research hashes, artifacts, or logs.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from tezcat.external.schema import (
    ExternalEvent, ExternalMarket, NormalizedTrade, OrderbookSnapshot,
)


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------
class ProviderError(RuntimeError):
    """Base class for all external-provider failures."""


class ProviderHTTPError(ProviderError):
    def __init__(self, provider: str, endpoint: str, status: int, detail: str = ""):
        super().__init__(f"{provider}: HTTP {status} from {endpoint}"
                         + (f" — {detail}" if detail else ""))
        self.provider, self.endpoint, self.status = provider, endpoint, status


class ProviderTimeout(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    """Provider signalled rate limiting (HTTP 429) after retries."""


class ProviderSchemaError(ProviderError):
    """The provider response does not match the documented schema.

    Raised on missing fields, unexpected types, and value ranges the
    adapter does not recognize. This is the schema-drift tripwire: a
    changed provider field fails loudly here instead of being silently
    reinterpreted.
    """


class ProviderDataError(ProviderError):
    """Response parsed, but the data violates basic validity (e.g. a
    probability outside [0, 1])."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
@runtime_checkable
class Transport(Protocol):
    """Minimal HTTP-GET abstraction so adapters never own a socket."""

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            timeout: float = 10.0) -> Tuple[int, str]:
        """Return (status_code, body_text). Raises ProviderTimeout on timeout."""
        ...


class UrllibTransport:
    """Stdlib GET transport (no new runtime dependency)."""

    def __init__(self, user_agent: str = "tezcat-research/read-only"):
        self.user_agent = user_agent

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            timeout: float = 10.0) -> Tuple[int, str]:
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
            url = f"{url}?{qs}" if qs else url
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except TimeoutError as exc:
            raise ProviderTimeout(f"timeout after {timeout}s: {url}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout(f"timeout after {timeout}s: {url}") from exc
            raise ProviderError(f"network error for {url}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Rate limiting + retries
# ---------------------------------------------------------------------------
@dataclass
class RateLimiter:
    """Token bucket with injectable clock/sleep (deterministic in tests)."""

    rate_per_second: float = 5.0
    burst: int = 5
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _tokens: float = field(default=0.0, init=False)
    _last: Optional[float] = field(default=None, init=False)

    def acquire(self) -> None:
        now = self.clock()
        if self._last is None:
            self._tokens = float(self.burst)
        else:
            self._tokens = min(float(self.burst),
                               self._tokens + (now - self._last) * self.rate_per_second)
        self._last = now
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self.rate_per_second
            self.sleep(wait)
            self._tokens = 1.0
            self._last = self.clock()
        self._tokens -= 1.0


@dataclass
class RequestPolicy:
    """Retry/backoff policy for GET requests (safe to retry)."""

    max_attempts: int = 3
    backoff_base: float = 0.5
    timeout: float = 10.0
    sleep: Callable[[float], None] = time.sleep


def request_json(transport: Transport, provider: str, base_url: str,
                 path: str, params: Optional[Dict[str, Any]] = None,
                 limiter: Optional[RateLimiter] = None,
                 policy: Optional[RequestPolicy] = None) -> Any:
    """Rate-limited, retrying GET returning parsed JSON.

    Retries only on 429/5xx/timeout with exponential backoff; 4xx client
    errors and malformed JSON fail immediately and explicitly.
    """
    policy = policy or RequestPolicy()
    endpoint = path
    url = base_url.rstrip("/") + path
    last_exc: Optional[ProviderError] = None
    for attempt in range(policy.max_attempts):
        if limiter is not None:
            limiter.acquire()
        try:
            status, body = transport.get(url, params=params, timeout=policy.timeout)
        except ProviderTimeout as exc:
            last_exc = exc
            status, body = None, ""
        if status is not None:
            if status == 429:
                last_exc = ProviderRateLimited(
                    f"{provider}: rate limited on {endpoint}")
            elif status >= 500:
                last_exc = ProviderHTTPError(provider, endpoint, status)
            elif status >= 400:
                raise ProviderHTTPError(provider, endpoint, status, body[:200])
            else:
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise ProviderSchemaError(
                        f"{provider}: malformed JSON from {endpoint}: "
                        f"{exc}") from exc
        if attempt < policy.max_attempts - 1:
            policy.sleep(policy.backoff_base * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Strict field access (the schema-drift tripwire)
# ---------------------------------------------------------------------------
def require(payload: Dict[str, Any], field_name: str, provider: str,
            context: str) -> Any:
    """Fetch a required field or raise ProviderSchemaError naming it."""
    if not isinstance(payload, dict) or field_name not in payload:
        raise ProviderSchemaError(
            f"{provider}: expected field {field_name!r} missing in {context} "
            f"response — provider schema may have changed; refusing to "
            f"reinterpret (got keys: "
            f"{sorted(payload) if isinstance(payload, dict) else type(payload).__name__})")
    return payload[field_name]


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class EventMarketProvider(Protocol):
    """Read-only view of one external event-market provider.

    Deliberately has no trading surface. ``get_history`` returns canonical
    :class:`MarketObservation`-shaped dict rows (one per sampled period)
    where the provider legitimately exposes history, and raises
    ``ProviderError`` otherwise — it never synthesizes history.
    """

    provider_id: str
    adapter_version: str

    def list_events(self, limit: int = 100,
                    cursor: Optional[str] = None) -> Tuple[List[ExternalEvent], Optional[str]]: ...

    def list_markets(self, event_id: Optional[str] = None, limit: int = 100,
                     cursor: Optional[str] = None) -> Tuple[List[ExternalMarket], Optional[str]]: ...

    def get_market(self, market_id: str) -> ExternalMarket: ...

    def get_orderbook(self, market_id: str) -> OrderbookSnapshot: ...

    def get_trades(self, market_id: str, limit: int = 100,
                   cursor: Optional[str] = None) -> Tuple[List[NormalizedTrade], Optional[str]]: ...

    def get_history(self, market_id: str, start_ts: int, end_ts: int,
                    period_minutes: int = 60) -> List[Dict[str, Any]]: ...
