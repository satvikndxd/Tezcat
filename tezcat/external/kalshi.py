"""Read-only Kalshi adapter (Phase S3).

Speaks the current documented Kalshi Trade API v2
(https://docs.kalshi.com). Public market-data endpoints only — events,
markets, order books, trades, candlesticks — none of which require
credentials. No order placement, no portfolio access, ever.

Price representation
--------------------
Kalshi markets are binary YES/NO contracts settling at $1. Current
responses carry fixed-point dollar strings (``yes_bid_dollars`` = "0.6700");
older payloads carry integer cents (``yes_bid`` = 67). The adapter accepts
both **explicitly** — every normalized value records which rule produced it
(``dollars_string/1.0`` or ``cents_int/100``) — and raises
``ProviderSchemaError`` when neither documented form is present. A price
is normalized to the canonical implied-probability proxy p ∈ [0, 1].

Order book semantics
--------------------
Kalshi returns **YES bids and NO bids only** (no asks): a YES bid at p is
identical to a NO ask at 1−p. The canonical YES-side book is therefore:

    canonical bids = YES bids (verbatim probability)
    canonical asks = 1 − NO bid price, same size

This identity is exact for binary markets and is recorded in
``book_transform`` on every snapshot. The provider-native book is always
preserved in ``native``.

Tick precision is read from market metadata where reported
(``tick_size``/``notional`` fields), never assumed.

Base URL comes from ``$KALSHI_API_BASE`` (default: the documented public
production host). No API key is sent for any endpoint this adapter uses.
"""

from __future__ import annotations

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

ADAPTER_VERSION = "kalshi-adapter/1.0.0"
DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

#: Documented Kalshi market statuses → canonical. Anything undocumented maps
#: to UNKNOWN with the provider-native string preserved — visible, not silent.
_STATUS_MAP = {
    "open": MarketStatus.OPEN,
    "active": MarketStatus.OPEN,
    "paused": MarketStatus.PAUSED,
    "unopened": MarketStatus.PAUSED,
    "closed": MarketStatus.CLOSED,
    "settled": MarketStatus.RESOLVED,
    "finalized": MarketStatus.RESOLVED,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_to_iso(ts: Any, context: str) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError) as exc:
        raise ProviderSchemaError(
            f"kalshi: unparseable timestamp {ts!r} in {context}") from exc


class KalshiAdapter:
    """Read-only ``EventMarketProvider`` for Kalshi."""

    provider_id = "kalshi"
    adapter_version = ADAPTER_VERSION

    def __init__(self, transport: Optional[Transport] = None,
                 base_url: Optional[str] = None,
                 limiter: Optional[RateLimiter] = None,
                 policy: Optional[RequestPolicy] = None,
                 source_kind: str = "live",
                 now: Callable[[], str] = _utc_now_iso):
        self.transport = transport or UrllibTransport()
        self.base_url = base_url or os.environ.get("KALSHI_API_BASE",
                                                   DEFAULT_BASE_URL)
        self.limiter = limiter or RateLimiter(rate_per_second=5.0, burst=5)
        self.policy = policy or RequestPolicy()
        self.source_kind = source_kind
        self._now = now

    # -- plumbing ------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return request_json(self.transport, self.provider_id, self.base_url,
                            path, params=params, limiter=self.limiter,
                            policy=self.policy)

    def _provenance(self, endpoint: str) -> Provenance:
        return Provenance(provider=self.provider_id,
                          adapter_version=self.adapter_version,
                          endpoint=endpoint, retrieved_at=self._now(),
                          source_kind=self.source_kind)

    # -- price normalization (both documented representations) ---------
    def _prob(self, payload: Dict[str, Any], dollars_field: str,
              cents_field: str, context: str,
              required: bool = False) -> Tuple[Optional[float], Optional[str], str]:
        """Return (canonical probability, verbatim value, transform rule)."""
        if dollars_field in payload and payload[dollars_field] is not None:
            raw = payload[dollars_field]
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ProviderSchemaError(
                    f"kalshi: {dollars_field} in {context} is not a dollar "
                    f"string: {raw!r}") from exc
            transform = "dollars_string/1.0"
        elif cents_field in payload and payload[cents_field] is not None:
            raw = payload[cents_field]
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise ProviderSchemaError(
                    f"kalshi: {cents_field} in {context} is not numeric "
                    f"cents: {raw!r}")
            value = float(raw) / 100.0
            transform = "cents_int/100"
        else:
            if required:
                raise ProviderSchemaError(
                    f"kalshi: neither {dollars_field!r} nor {cents_field!r} "
                    f"present in {context} — provider schema may have changed")
            return None, None, "absent"
        if not 0.0 <= value <= 1.0:
            raise ProviderSchemaError(
                f"kalshi: {context} price {value} outside [0, 1] after "
                f"{transform} — refusing to reinterpret")
        return value, str(raw), transform

    def _size(self, payload: Dict[str, Any], fp_field: str, int_field: str,
              context: str) -> Optional[float]:
        for f in (fp_field, int_field):
            if f in payload and payload[f] is not None:
                try:
                    v = float(payload[f])
                except (TypeError, ValueError) as exc:
                    raise ProviderSchemaError(
                        f"kalshi: {f} in {context} is not numeric: "
                        f"{payload[f]!r}") from exc
                if v < 0:
                    raise ProviderSchemaError(
                        f"kalshi: negative size {v} in {context}")
                return v
        return None

    # -- events --------------------------------------------------------
    def list_events(self, limit: int = 100, cursor: Optional[str] = None,
                    status: Optional[str] = None
                    ) -> Tuple[List[ExternalEvent], Optional[str]]:
        payload = self._get("/events", {"limit": limit, "cursor": cursor,
                                        "status": status})
        rows = require(payload, "events", self.provider_id, "/events")
        prov = self._provenance("/events")
        events = []
        for row in rows:
            events.append(ExternalEvent(
                provider=self.provider_id,
                event_id=require(row, "event_ticker", self.provider_id, "event"),
                title=row.get("title", ""),
                category=row.get("category", ""),
                metadata={k: row[k] for k in ("series_ticker", "sub_title",
                                              "mutually_exclusive")
                          if k in row},
                provenance=prov))
        next_cursor = payload.get("cursor") or None
        return events, next_cursor

    def get_event(self, event_id: str) -> ExternalEvent:
        payload = self._get(f"/events/{event_id}")
        row = require(payload, "event", self.provider_id, f"/events/{event_id}")
        return ExternalEvent(
            provider=self.provider_id,
            event_id=require(row, "event_ticker", self.provider_id, "event"),
            title=row.get("title", ""), category=row.get("category", ""),
            metadata={k: row[k] for k in ("series_ticker", "sub_title",
                                          "mutually_exclusive") if k in row},
            provenance=self._provenance(f"/events/{event_id}"))

    # -- markets -------------------------------------------------------
    def _parse_market(self, row: Dict[str, Any], endpoint: str) -> ExternalMarket:
        ticker = require(row, "ticker", self.provider_id, "market")
        provider_status = str(require(row, "status", self.provider_id, "market"))
        status = _STATUS_MAP.get(provider_status, MarketStatus.UNKNOWN)

        resolution = None
        if status is MarketStatus.RESOLVED or row.get("result"):
            settle, _, _ = self._prob(row, "settlement_value_dollars",
                                      "settlement_value", "market settlement")
            resolution = Resolution(
                resolved=True, outcome=str(row.get("result") or "") or None,
                resolved_at=row.get("settlement_ts") and _ts_to_iso(
                    row["settlement_ts"], "settlement_ts") or
                    row.get("settled_time"),
                settlement_value=settle,
                source="kalshi settlement fields")

        tick = None
        if row.get("tick_size_dollars") is not None:
            tick = float(row["tick_size_dollars"])
        elif row.get("tick_size") is not None:
            tick = float(row["tick_size"]) / 100.0

        keep = ("market_type", "volume", "volume_fp", "volume_24h",
                "volume_24h_fp", "open_interest", "open_interest_fp",
                "liquidity", "liquidity_dollars", "last_price",
                "last_price_dollars", "yes_bid", "yes_bid_dollars",
                "yes_ask", "yes_ask_dollars", "rules_primary")
        return ExternalMarket(
            provider=self.provider_id, market_id=ticker,
            event_id=str(row.get("event_ticker", "")),
            question=row.get("title") or row.get("subtitle", ""),
            outcomes=[OutcomeSpec(name="yes"), OutcomeSpec(name="no")],
            status=status, provider_status=provider_status,
            resolution=resolution,
            open_time=row.get("open_time"), close_time=row.get("close_time"),
            tick_size=tick,
            metadata={k: row[k] for k in keep if k in row},
            provenance=self._provenance(endpoint))

    def list_markets(self, event_id: Optional[str] = None, limit: int = 100,
                     cursor: Optional[str] = None, status: Optional[str] = None
                     ) -> Tuple[List[ExternalMarket], Optional[str]]:
        payload = self._get("/markets", {"limit": limit, "cursor": cursor,
                                         "event_ticker": event_id,
                                         "status": status})
        rows = require(payload, "markets", self.provider_id, "/markets")
        markets = [self._parse_market(r, "/markets") for r in rows]
        return markets, payload.get("cursor") or None

    def get_market(self, market_id: str) -> ExternalMarket:
        payload = self._get(f"/markets/{market_id}")
        row = require(payload, "market", self.provider_id,
                      f"/markets/{market_id}")
        return self._parse_market(row, f"/markets/{market_id}")

    # -- order book ----------------------------------------------------
    def _parse_levels(self, levels: Any, context: str) -> List[Tuple[float, float]]:
        out = []
        if levels is None:
            return out
        if not isinstance(levels, list):
            raise ProviderSchemaError(
                f"kalshi: {context} book side is not a list: {type(levels).__name__}")
        for lv in levels:
            if not isinstance(lv, (list, tuple)) or len(lv) != 2:
                raise ProviderSchemaError(
                    f"kalshi: {context} level is not a [price, size] pair: {lv!r}")
            price_raw, size_raw = lv
            if isinstance(price_raw, str):
                price = float(price_raw)          # dollars string
            elif isinstance(price_raw, (int, float)):
                price = float(price_raw) / 100.0  # integer cents
            else:
                raise ProviderSchemaError(
                    f"kalshi: {context} level price {price_raw!r} unrecognized")
            size = float(size_raw)
            if not 0.0 <= price <= 1.0 or size < 0:
                raise ProviderSchemaError(
                    f"kalshi: {context} level out of range: {lv!r}")
            out.append((price, size))
        return out

    def get_orderbook(self, market_id: str) -> OrderbookSnapshot:
        endpoint = f"/markets/{market_id}/orderbook"
        payload = self._get(endpoint)
        book = payload.get("orderbook_fp") or payload.get("orderbook")
        if book is None:
            raise ProviderSchemaError(
                "kalshi: neither 'orderbook_fp' nor 'orderbook' present in "
                f"{endpoint} response — provider schema may have changed")
        yes_levels = self._parse_levels(
            book.get("yes_dollars", book.get("yes")), "yes")
        no_levels = self._parse_levels(
            book.get("no_dollars", book.get("no")), "no")
        # YES bids verbatim; YES asks derived from NO bids via the exact
        # binary identity ask = 1 − no_bid.
        bids = sorted(yes_levels, key=lambda t: -t[0])
        asks = sorted(((1.0 - p, s) for p, s in no_levels), key=lambda t: t[0])
        return OrderbookSnapshot(
            provider=self.provider_id, market_id=market_id,
            bids=[BookLevel(price=p, size=s) for p, s in bids],
            asks=[BookLevel(price=p, size=s) for p, s in asks],
            native={"orderbook": book},
            book_transform=("yes bids verbatim; asks = 1 - no_bid price "
                            "(Kalshi binary YES/NO identity); prices "
                            "dollars_string/1.0 or cents_int/100"),
            provenance=self._provenance(endpoint))

    # -- trades --------------------------------------------------------
    def get_trades(self, market_id: str, limit: int = 100,
                   cursor: Optional[str] = None,
                   min_ts: Optional[int] = None, max_ts: Optional[int] = None
                   ) -> Tuple[List[NormalizedTrade], Optional[str]]:
        payload = self._get("/markets/trades",
                            {"ticker": market_id, "limit": limit,
                             "cursor": cursor, "min_ts": min_ts,
                             "max_ts": max_ts})
        rows = require(payload, "trades", self.provider_id, "/markets/trades")
        trades = []
        for row in rows:
            price, raw, transform = self._prob(
                row, "yes_price_dollars", "yes_price", "trade", required=True)
            size = self._size(row, "count_fp", "count", "trade")
            if size is None:
                raise ProviderSchemaError(
                    "kalshi: trade has neither 'count_fp' nor 'count'")
            ts = row.get("created_time")
            trades.append(NormalizedTrade(
                provider=self.provider_id, market_id=market_id,
                trade_id=str(require(row, "trade_id", self.provider_id, "trade")),
                timestamp=str(ts) if ts is not None else None,
                price=price, size=size,
                taker_side=str(row.get("taker_side")
                               or row.get("taker_outcome_side") or "") or None,
                provider_price=raw, price_transform=transform,
                metadata={k: row[k] for k in ("is_block_trade",
                                              "taker_book_side") if k in row}))
        return trades, payload.get("cursor") or None

    # -- history (candlesticks) ----------------------------------------
    def get_history(self, market_id: str, start_ts: int, end_ts: int,
                    period_minutes: int = 60,
                    series_ticker: Optional[str] = None) -> List[Dict[str, Any]]:
        """Candlestick history → canonical observation rows.

        ``implied_probability`` is the close of the traded-price candle
        (None when the period had no trades — never fabricated); bid/ask
        are the closes of the yes_bid/yes_ask candles. Requires the series
        ticker; when not supplied it is resolved via the market's event.
        """
        if series_ticker is None:
            market = self.get_market(market_id)
            event = self.get_event(market.event_id)
            series_ticker = event.metadata.get("series_ticker")
            if not series_ticker:
                raise ProviderSchemaError(
                    f"kalshi: cannot resolve series ticker for {market_id} "
                    "(event carries no 'series_ticker')")
        endpoint = f"/series/{series_ticker}/markets/{market_id}/candlesticks"
        payload = self._get(endpoint, {"start_ts": start_ts, "end_ts": end_ts,
                                       "period_interval": period_minutes})
        rows = require(payload, "candlesticks", self.provider_id, endpoint)

        def close_of(c: Optional[Dict[str, Any]], ctx: str) -> Optional[float]:
            if not c:
                return None
            if c.get("close_dollars") is not None:
                v = float(c["close_dollars"])       # dollars string
            elif c.get("close") is not None:
                raw = c["close"]
                v = float(raw) if isinstance(raw, str) else float(raw) / 100.0
            else:
                return None
            if not 0.0 <= v <= 1.0:
                raise ProviderSchemaError(
                    f"kalshi: {ctx} close {v} outside [0, 1] — refusing to "
                    "reinterpret")
            return v

        out = []
        for c in rows:
            ts = require(c, "end_period_ts", self.provider_id, "candlestick")
            bid = close_of(c.get("yes_bid"), "candle yes_bid")
            ask = close_of(c.get("yes_ask"), "candle yes_ask")
            out.append({
                "market_id": market_id,
                "timestamp": _ts_to_iso(ts, "end_period_ts"),
                "bid": bid, "ask": ask,
                "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
                "implied_probability": close_of(c.get("price"), "candle price"),
                "spread": (ask - bid) if bid is not None and ask is not None else None,
                "depth": None,  # not part of candlestick payloads
                "volume": self._size(c, "volume_fp", "volume", "candlestick"),
                "trade_count": None,
                "status": MarketStatus.UNKNOWN.value,
            })
        return out
