"""Read-only Kalshi market-data adapter.

Only public GET endpoints are used.  Authenticated order entry, portfolio,
settlement, and account APIs are intentionally absent from this module.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from tezcat.external.schemas import (
    Event, Market, OrderbookSnapshot, Outcome, ProviderSchemaError, Quote,
    Trade, normalize_timestamp, probability, utc_now,
)
from .base import EventMarketProvider, ProviderPage, ReadOnlyHttpClient


class KalshiAdapter:
    provider_id = "kalshi"
    adapter_version = "kalshi-rest-v1"
    base_url = "https://external-api.kalshi.com/trade-api/v2"

    def __init__(self, *, client: Optional[ReadOnlyHttpClient] = None,
                 base_url: Optional[str] = None):
        self.http = client or ReadOnlyHttpClient(base_url or self.base_url)

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _page(body: Dict[str, Any], key: str) -> ProviderPage:
        if not isinstance(body, dict):
            raise ProviderSchemaError("Kalshi response must be an object")
        items = body.get(key)
        if not isinstance(items, list):
            raise ProviderSchemaError(f"Kalshi response missing list field {key!r}")
        cursor = body.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ProviderSchemaError("Kalshi cursor must be a string or null")
        return ProviderPage(items=items, cursor=cursor or None, raw=body)

    def list_events(self, *, cursor: Optional[str] = None, limit: int = 100,
                    status: Optional[str] = None) -> ProviderPage:
        params: Dict[str, Any] = {"limit": max(1, min(200, limit))}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        return self._page(self.http.get_json("/events", params), "events")

    def list_markets(self, *, cursor: Optional[str] = None, limit: int = 100,
                     status: Optional[str] = None, event_id: Optional[str] = None) -> ProviderPage:
        params: Dict[str, Any] = {"limit": max(1, min(1000, limit))}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if event_id:
            params["event_ticker"] = event_id
        return self._page(self.http.get_json("/markets", params), "markets")

    def get_market(self, market_id: str) -> Dict[str, Any]:
        body = self.http.get_json(f"/markets/{market_id}")
        if not isinstance(body, dict):
            raise ProviderSchemaError("Kalshi market response must be an object")
        market = body.get("market")
        if not isinstance(market, dict):
            raise ProviderSchemaError("Kalshi market response missing 'market'")
        return market

    def get_orderbook(self, market_id: str, **kwargs: Any) -> Dict[str, Any]:
        body = self.http.get_json(f"/markets/{market_id}/orderbook")
        if not isinstance(body, dict):
            raise ProviderSchemaError("Kalshi orderbook response must be an object")
        book = body.get("orderbook_fp") or body.get("orderbook")
        if not isinstance(book, dict):
            raise ProviderSchemaError("Kalshi orderbook response missing orderbook")
        if not any(k in book for k in ("yes_dollars", "no_dollars", "yes", "no")):
            raise ProviderSchemaError("Kalshi orderbook response has no YES/NO levels")
        return body

    def get_trades(self, market_id: str, **kwargs: Any) -> ProviderPage:
        params: Dict[str, Any] = {"ticker": market_id}
        for name in ("limit", "cursor", "min_ts", "max_ts", "is_block_trade"):
            if kwargs.get(name) is not None:
                params[name] = kwargs[name]
        return self._page(self.http.get_json("/markets/trades", params), "trades")

    def get_history(self, market_id: str, **kwargs: Any) -> ProviderPage:
        series_ticker = kwargs.get("series_ticker")
        start_ts = kwargs.get("start_ts")
        end_ts = kwargs.get("end_ts")
        period_interval = kwargs.get("period_interval")
        if not series_ticker or start_ts is None or end_ts is None or period_interval is None:
            raise ValueError("Kalshi history requires series_ticker, start_ts, end_ts, period_interval")
        if period_interval not in (1, 60, 1440):
            raise ValueError("Kalshi period_interval must be 1, 60, or 1440 minutes")
        historical = bool(kwargs.get("historical", False))
        path = (f"/historical/markets/{market_id}/candlesticks" if historical else
                f"/series/{series_ticker}/markets/{market_id}/candlesticks")
        params = {
            "start_ts": int(start_ts), "end_ts": int(end_ts),
            "period_interval": period_interval,
            "include_latest_before_start": bool(kwargs.get("include_latest_before_start", False)),
        }
        body = self.http.get_json(path, params)
        if not isinstance(body, dict):
            raise ProviderSchemaError("Kalshi candlestick response must be an object")
        candles = body.get("candlesticks")
        if not isinstance(candles, list):
            raise ProviderSchemaError("Kalshi candlestick response missing candlesticks")
        return ProviderPage(items=candles, cursor=None, raw=body)

    @staticmethod
    def normalize_event(raw: Dict[str, Any]) -> Event:
        event_id = raw.get("event_ticker") or raw.get("ticker")
        if not event_id or not raw.get("title"):
            raise ProviderSchemaError("Kalshi event requires event_ticker/ticker and title")
        return Event(
            provider="kalshi", event_id=str(event_id), title=str(raw["title"]),
            description=raw.get("sub_title") or raw.get("description"),
            category=raw.get("category"), status=raw.get("status"),
            close_time=raw.get("close_time") or raw.get("expiration_time"),
            resolution={k: raw.get(k) for k in ("mutually_exclusive", "series_ticker") if k in raw},
            source_url=f"https://kalshi.com/markets/{event_id}", metadata=dict(raw),
        )

    @staticmethod
    def normalize_market(raw: Dict[str, Any]) -> Market:
        market_id = raw.get("ticker")
        title = raw.get("title") or raw.get("subtitle")
        if not market_id or not title:
            raise ProviderSchemaError("Kalshi market requires ticker and title/subtitle")
        outcomes = [
            Outcome(outcome_id="YES", label="YES", provider_units="dollars"),
            Outcome(outcome_id="NO", label="NO", provider_units="dollars"),
        ]
        return Market(
            provider="kalshi", market_id=str(market_id),
            event_id=raw.get("event_ticker"), title=str(title),
            description=raw.get("subtitle") if raw.get("title") else None,
            outcomes=outcomes, status=raw.get("status"),
            open_time=raw.get("open_time"),
            close_time=raw.get("close_time") or raw.get("expiration_time"),
            resolution={k: raw.get(k) for k in ("result", "settlement_value", "rules_primary") if k in raw},
            source_url=f"https://kalshi.com/markets/{market_id}", metadata=dict(raw),
        )

    @staticmethod
    def _levels(book: Dict[str, Any], side: str) -> list:
        values = book.get(f"{side}_dollars")
        if values is None:
            values = book.get(side)
        if values is None:
            return []
        if not isinstance(values, list):
            raise ProviderSchemaError(f"Kalshi {side} levels must be a list")
        out = []
        for row in values:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise ProviderSchemaError(f"malformed Kalshi {side} level")
            out.append((probability(row[0], "dollars"), float(row[1])))
        return out

    @classmethod
    def normalize_orderbook(cls, raw: Dict[str, Any], *, market_id: str,
                            event_id: Optional[str] = None,
                            timestamp: Optional[str] = None) -> OrderbookSnapshot:
        book = raw.get("orderbook_fp") or raw.get("orderbook")
        if not isinstance(book, dict):
            raise ProviderSchemaError("Kalshi orderbook missing orderbook_fp/orderbook")
        yes_bids = cls._levels(book, "yes")
        no_bids = cls._levels(book, "no")
        bids = [{"price": p, "quantity": q} for p, q in yes_bids]
        # A NO bid at q is a YES ask at 1-q under Kalshi's binary representation.
        asks = [{"price": round(1.0 - p, 12), "quantity": q} for p, q in no_bids]
        bids.sort(key=lambda row: row["price"], reverse=True)
        asks.sort(key=lambda row: row["price"])
        best_bid = bids[0]["price"] if bids else None
        best_ask = asks[0]["price"] if asks else None
        mid = ((best_bid + best_ask) / 2) if best_bid is not None and best_ask is not None else None
        spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
        from tezcat.external.schemas import BookLevel
        return OrderbookSnapshot(
            provider="kalshi", market_id=market_id, event_id=event_id,
            outcome_id="YES", timestamp=timestamp or normalize_timestamp(raw.get("timestamp") or raw.get("ts") or utc_now()),
            bids=[BookLevel(**row) for row in bids], asks=[BookLevel(**row) for row in asks],
            mid=mid, spread=spread,
            depth=sum(row["quantity"] for row in bids + asks),
            implied_probability=mid, native_bids=[{"outcome": "YES", "price": p, "quantity": q} for p, q in yes_bids],
            native_asks=[{"outcome": "NO", "price": p, "quantity": q} for p, q in no_bids],
            provider_units="dollars", metadata={"raw": raw},
        )

    @staticmethod
    def normalize_trade(raw: Dict[str, Any]) -> Trade:
        trade_id = raw.get("trade_id")
        market_id = raw.get("ticker")
        side = str(raw.get("taker_outcome_side") or "").lower()
        if not trade_id or not market_id or side not in {"yes", "no"}:
            raise ProviderSchemaError("Kalshi trade requires trade_id, ticker, and taker_outcome_side yes/no")
        yes = probability(raw.get("yes_price_dollars"), "dollars")
        no = probability(raw.get("no_price_dollars"), "dollars")
        selected = yes if side == "yes" else no
        return Trade(
            provider="kalshi", market_id=str(market_id), trade_id=str(trade_id),
            timestamp=normalize_timestamp(raw.get("created_time")), outcome_id=side.upper(),
            price=selected, quantity=float(raw.get("count_fp", 0)),
            side="buy" if str(raw.get("taker_book_side", "")).lower() == "bid" else "sell",
            provider_units="dollars", metadata={**raw, "yes_probability": yes, "no_probability": no},
        )

    @staticmethod
    def normalize_candlestick(raw: Dict[str, Any], *, market_id: str) -> Quote:
        end_ts = raw.get("end_period_ts")
        price = raw.get("price") or {}
        yes_bid = raw.get("yes_bid") or {}
        yes_ask = raw.get("yes_ask") or {}
        close = price.get("close_dollars")
        bid = yes_bid.get("close_dollars")
        ask = yes_ask.get("close_dollars")
        if close is None and bid is None and ask is None:
            raise ProviderSchemaError("Kalshi candlestick missing price/bid/ask close")
        return Quote(
            provider="kalshi", market_id=market_id, outcome_id="YES",
            timestamp=normalize_timestamp(end_ts), bid=probability(bid, "dollars") if bid is not None else None,
            ask=probability(ask, "dollars") if ask is not None else None,
            mid=probability(close, "dollars") if close is not None else None,
            spread=None, implied_probability=probability(close, "dollars") if close is not None else None,
            provider_price=float(close) if close is not None else None,
            provider_units="dollars", metadata={**raw, "volume": raw.get("volume_fp"), "open_interest": raw.get("open_interest_fp")},
        )
