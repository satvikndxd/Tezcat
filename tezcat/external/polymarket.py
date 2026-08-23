"""Read-only Polymarket adapter (Phase S3).

Built against Polymarket's **current public API surfaces** — the same ones
the unified Polymarket SDK wraps (https://docs.polymarket.com). This is
deliberately *not* an integration with the archived legacy
``@polymarket/clob-client``.

Conceptual layers (all public, all read-only):

    Gamma API   (gamma-api.polymarket.com)  events + market metadata,
                                            resolution state
    CLOB API    (clob.polymarket.com)       order books, prices,
                                            price history — read endpoints
                                            require no credentials
    Data API    (data-api.polymarket.com)   executed trades per market

No wallet, no private key, no API credential is required or accepted by
this adapter: it has no authenticated surface at all.

Identity model
--------------
Polymarket separates the *market* (a Gamma market / on-chain condition id)
from its *outcome tokens* (CLOB token ids, one per outcome). The canonical
:class:`ExternalMarket` records both: ``market_id`` is the Gamma market id,
and each :class:`OutcomeSpec` carries its CLOB ``token_id``. Order books
and price history are per-token — those methods take the YES token id and
say so; trades are per-condition.

Prices are decimal strings in [0, 1] (USDC per share); the canonical
conversion is the identity, recorded as ``decimal_string/1.0``. Wallet
addresses present in Data-API trade rows are **not** carried into
normalized trades — they are not needed for research and are dropped at
the adapter boundary (the raw artifact, if retained, is the place for
verbatim rows).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from tezcat.external.provider import (
    ProviderSchemaError, RateLimiter, RequestPolicy, Transport,
    UrllibTransport, request_json, require,
)
from tezcat.external.schema import (
    BookLevel, ExternalEvent, ExternalMarket, MarketStatus, NormalizedTrade,
    OrderbookSnapshot, OutcomeSpec, Provenance, Resolution,
)

ADAPTER_VERSION = "polymarket-adapter/1.0.0"
DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_DATA_URL = "https://data-api.polymarket.com"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_string_list(row: Dict[str, Any], field_name: str,
                      context: str) -> List[str]:
    """Gamma encodes some list fields as JSON strings — parse strictly."""
    raw = row.get(field_name)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderSchemaError(
                f"polymarket: {field_name} in {context} is not valid "
                f"JSON-encoded list: {raw!r}") from exc
        if not isinstance(parsed, list):
            raise ProviderSchemaError(
                f"polymarket: {field_name} in {context} decodes to "
                f"{type(parsed).__name__}, expected list")
        return [str(v) for v in parsed]
    raise ProviderSchemaError(
        f"polymarket: {field_name} in {context} has unexpected type "
        f"{type(raw).__name__}")


def _decimal_prob(raw: Any, context: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderSchemaError(
            f"polymarket: {context} is not a decimal price: {raw!r}") from exc
    if not 0.0 <= v <= 1.0:
        raise ProviderSchemaError(
            f"polymarket: {context} price {v} outside [0, 1] — refusing to "
            "reinterpret")
    return v


class PolymarketAdapter:
    """Read-only ``EventMarketProvider`` for Polymarket."""

    provider_id = "polymarket"
    adapter_version = ADAPTER_VERSION

    def __init__(self, transport: Optional[Transport] = None,
                 gamma_url: Optional[str] = None,
                 clob_url: Optional[str] = None,
                 data_url: Optional[str] = None,
                 limiter: Optional[RateLimiter] = None,
                 policy: Optional[RequestPolicy] = None,
                 source_kind: str = "live",
                 now: Callable[[], str] = _utc_now_iso):
        self.transport = transport or UrllibTransport()
        self.gamma_url = gamma_url or os.environ.get("POLYMARKET_GAMMA_BASE",
                                                     DEFAULT_GAMMA_URL)
        self.clob_url = clob_url or os.environ.get("POLYMARKET_CLOB_BASE",
                                                   DEFAULT_CLOB_URL)
        self.data_url = data_url or os.environ.get("POLYMARKET_DATA_BASE",
                                                   DEFAULT_DATA_URL)
        self.limiter = limiter or RateLimiter(rate_per_second=5.0, burst=5)
        self.policy = policy or RequestPolicy()
        self.source_kind = source_kind
        self._now = now

    # -- plumbing ------------------------------------------------------
    def _get(self, base: str, path: str,
             params: Optional[Dict[str, Any]] = None) -> Any:
        return request_json(self.transport, self.provider_id, base, path,
                            params=params, limiter=self.limiter,
                            policy=self.policy)

    def _provenance(self, endpoint: str) -> Provenance:
        return Provenance(provider=self.provider_id,
                          adapter_version=self.adapter_version,
                          endpoint=endpoint, retrieved_at=self._now(),
                          source_kind=self.source_kind)

    # -- events (Gamma) ------------------------------------------------
    def list_events(self, limit: int = 100, cursor: Optional[str] = None,
                    closed: Optional[bool] = None
                    ) -> Tuple[List[ExternalEvent], Optional[str]]:
        """Gamma events; ``cursor`` is a stringified offset."""
        offset = int(cursor) if cursor else 0
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if closed is not None:
            params["closed"] = str(closed).lower()
        rows = self._get(self.gamma_url, "/events", params)
        if not isinstance(rows, list):
            raise ProviderSchemaError(
                f"polymarket: /events returned {type(rows).__name__}, "
                "expected a list — provider schema may have changed")
        prov = self._provenance("/events")
        events = [ExternalEvent(
            provider=self.provider_id,
            event_id=str(require(row, "id", self.provider_id, "event")),
            title=row.get("title", ""),
            category=row.get("category") or "",
            close_time=row.get("endDate"),
            metadata={k: row[k] for k in ("slug", "liquidity", "volume")
                      if k in row},
            provenance=prov) for row in rows]
        next_cursor = str(offset + len(rows)) if len(rows) == limit else None
        return events, next_cursor

    # -- markets (Gamma) -----------------------------------------------
    def _parse_market(self, row: Dict[str, Any], endpoint: str) -> ExternalMarket:
        market_id = str(require(row, "id", self.provider_id, "market"))
        outcomes = _json_string_list(row, "outcomes", "market")
        token_ids = _json_string_list(row, "clobTokenIds", "market")
        if token_ids and len(token_ids) != len(outcomes):
            raise ProviderSchemaError(
                f"polymarket: market {market_id} has {len(outcomes)} outcomes "
                f"but {len(token_ids)} clob token ids — refusing to pair them")
        specs = [OutcomeSpec(name=name,
                             token_id=token_ids[i] if token_ids else None)
                 for i, name in enumerate(outcomes)]

        active = row.get("active")
        closed = row.get("closed")
        uma = str(row.get("umaResolutionStatus") or "").lower()
        if uma.startswith("resolved") or row.get("hasResolutionData") and closed:
            status = MarketStatus.RESOLVED
        elif closed:
            status = MarketStatus.CLOSED
        elif active:
            status = MarketStatus.OPEN
        elif active is None and closed is None:
            status = MarketStatus.UNKNOWN
        else:
            status = MarketStatus.PAUSED
        provider_status = (f"active={active} closed={closed}"
                           + (f" uma={uma}" if uma else ""))

        resolution = None
        if status is MarketStatus.RESOLVED:
            prices = _json_string_list(row, "outcomePrices", "market")
            outcome = None
            if prices and outcomes and len(prices) == len(outcomes):
                winners = [o for o, p in zip(outcomes, prices)
                           if _decimal_prob(p, "outcomePrices") >= 0.999]
                outcome = winners[0] if len(winners) == 1 else None
            resolution = Resolution(
                resolved=True, outcome=outcome.lower() if outcome else None,
                resolved_at=row.get("closedTime") or row.get("endDate"),
                source="polymarket uma resolution status + outcomePrices")

        tick = None
        if row.get("orderPriceMinTickSize") is not None:
            tick = float(row["orderPriceMinTickSize"])

        keep = ("conditionId", "slug", "volumeNum", "volume", "liquidityNum",
                "liquidity", "outcomePrices", "lastTradePrice", "bestBid",
                "bestAsk", "umaResolutionStatus", "negRisk")
        return ExternalMarket(
            provider=self.provider_id, market_id=market_id,
            event_id=str(row.get("events", [{}])[0].get("id", "")
                         if isinstance(row.get("events"), list) and row.get("events")
                         else row.get("eventId", "") or ""),
            question=row.get("question", ""),
            outcomes=specs, status=status, provider_status=provider_status,
            resolution=resolution,
            open_time=row.get("startDate"), close_time=row.get("endDate"),
            tick_size=tick,
            metadata={k: row[k] for k in keep if k in row},
            provenance=self._provenance(endpoint))

    def list_markets(self, event_id: Optional[str] = None, limit: int = 100,
                     cursor: Optional[str] = None
                     ) -> Tuple[List[ExternalMarket], Optional[str]]:
        offset = int(cursor) if cursor else 0
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if event_id is not None:
            params["event_id"] = event_id
        rows = self._get(self.gamma_url, "/markets", params)
        if not isinstance(rows, list):
            raise ProviderSchemaError(
                f"polymarket: /markets returned {type(rows).__name__}, "
                "expected a list")
        markets = [self._parse_market(r, "/markets") for r in rows]
        next_cursor = str(offset + len(rows)) if len(rows) == limit else None
        return markets, next_cursor

    def get_market(self, market_id: str) -> ExternalMarket:
        row = self._get(self.gamma_url, f"/markets/{market_id}")
        if isinstance(row, list):  # some gamma queries return single-item lists
            if len(row) != 1:
                raise ProviderSchemaError(
                    f"polymarket: /markets/{market_id} returned "
                    f"{len(row)} rows, expected exactly 1")
            row = row[0]
        return self._parse_market(row, f"/markets/{market_id}")

    # -- order book (CLOB, per outcome token) --------------------------
    def get_orderbook(self, token_id: str) -> OrderbookSnapshot:
        """Order book for one **outcome token** (pass the YES token id for
        the canonical YES-side book)."""
        payload = self._get(self.clob_url, "/book", {"token_id": token_id})
        raw_bids = require(payload, "bids", self.provider_id, "/book")
        raw_asks = require(payload, "asks", self.provider_id, "/book")

        def levels(rows: Any, side: str) -> List[Tuple[float, float]]:
            out = []
            for lv in rows:
                price = _decimal_prob(
                    require(lv, "price", self.provider_id, f"book {side}"),
                    f"book {side}")
                size = float(require(lv, "size", self.provider_id,
                                     f"book {side}"))
                if size < 0:
                    raise ProviderSchemaError(
                        f"polymarket: negative size in book {side}: {lv!r}")
                out.append((price, size))
            return out

        bids = sorted(levels(raw_bids, "bids"), key=lambda t: -t[0])
        asks = sorted(levels(raw_asks, "asks"), key=lambda t: t[0])
        ts = payload.get("timestamp")
        iso_ts = None
        if ts is not None:
            try:
                ms = int(ts)
                iso_ts = datetime.fromtimestamp(
                    ms / 1000 if ms > 10 ** 11 else ms, tz=timezone.utc).isoformat()
            except (ValueError, OSError, OverflowError):
                iso_ts = str(ts)
        return OrderbookSnapshot(
            provider=self.provider_id, market_id=token_id,
            timestamp=iso_ts,
            bids=[BookLevel(price=p, size=s) for p, s in bids],
            asks=[BookLevel(price=p, size=s) for p, s in asks],
            native={"book": payload},
            book_transform=("per-token bids/asks verbatim; prices "
                            "decimal_string/1.0 (identity)"),
            provenance=self._provenance("/book"))

    # -- trades (Data API, per condition id) ---------------------------
    def get_trades(self, condition_id: str, limit: int = 100,
                   cursor: Optional[str] = None
                   ) -> Tuple[List[NormalizedTrade], Optional[str]]:
        """Executed trades for a market **condition id**. Wallet addresses
        are dropped at this boundary (see module docstring)."""
        offset = int(cursor) if cursor else 0
        rows = self._get(self.data_url, "/trades",
                         {"market": condition_id, "limit": limit,
                          "offset": offset})
        if not isinstance(rows, list):
            raise ProviderSchemaError(
                f"polymarket: /trades returned {type(rows).__name__}, "
                "expected a list")
        trades = []
        for i, row in enumerate(rows):
            price = _decimal_prob(
                require(row, "price", self.provider_id, "trade"), "trade")
            size = float(require(row, "size", self.provider_id, "trade"))
            if size < 0:
                raise ProviderSchemaError(
                    f"polymarket: negative trade size: {row.get('size')!r}")
            ts = require(row, "timestamp", self.provider_id, "trade")
            try:
                iso_ts = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError) as exc:
                raise ProviderSchemaError(
                    f"polymarket: unparseable trade timestamp {ts!r}") from exc
            trade_id = str(row.get("transactionHash")
                           or f"{condition_id}:{ts}:{offset + i}")
            trades.append(NormalizedTrade(
                provider=self.provider_id, market_id=condition_id,
                trade_id=trade_id, timestamp=iso_ts, price=price, size=size,
                taker_side=str(row.get("side") or "") or None,
                provider_price=str(row["price"]),
                price_transform="decimal_string/1.0",
                metadata={k: row[k] for k in ("outcome", "outcomeIndex",
                                              "asset") if k in row}))
        next_cursor = str(offset + len(rows)) if len(rows) == limit else None
        return trades, next_cursor

    # -- history (CLOB prices-history, per outcome token) --------------
    def get_history(self, token_id: str, start_ts: int, end_ts: int,
                    period_minutes: int = 60) -> List[Dict[str, Any]]:
        """CLOB price history for one outcome token → observation rows.

        Only the traded/quoted price series is available here; bid, ask,
        depth, and volume are ``None`` in these rows (not observed via this
        endpoint — never fabricated).
        """
        payload = self._get(self.clob_url, "/prices-history",
                            {"market": token_id, "startTs": start_ts,
                             "endTs": end_ts, "fidelity": period_minutes})
        rows = require(payload, "history", self.provider_id, "/prices-history")
        out = []
        for row in rows:
            t = require(row, "t", self.provider_id, "history point")
            p = _decimal_prob(require(row, "p", self.provider_id,
                                      "history point"), "history point")
            try:
                iso_ts = datetime.fromtimestamp(int(t), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError) as exc:
                raise ProviderSchemaError(
                    f"polymarket: unparseable history timestamp {t!r}") from exc
            out.append({
                "market_id": token_id, "timestamp": iso_ts,
                "bid": None, "ask": None, "mid": None,
                "implied_probability": p,
                "spread": None, "depth": None, "volume": None,
                "trade_count": None,
                "status": MarketStatus.UNKNOWN.value,
            })
        return out
