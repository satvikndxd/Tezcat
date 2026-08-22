"""Read-only Polymarket market-data adapter.

The adapter uses current public Gamma metadata and CLOB market-data surfaces.
It does not use the archived CLOB client and contains no order/account methods.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tezcat.external.schemas import (
    Event, Market, OrderbookSnapshot, Outcome, ProviderSchemaError, Quote,
    Trade, normalize_timestamp, probability, utc_now,
)
from .base import ProviderHttpError, ProviderPage, ReadOnlyHttpClient


class PolymarketAdapter:
    provider_id = "polymarket"
    adapter_version = "polymarket-public-rest-v1"
    gamma_url = "https://gamma-api.polymarket.com"
    clob_url = "https://clob.polymarket.com"

    def __init__(self, *, gamma: Optional[ReadOnlyHttpClient] = None,
                 clob: Optional[ReadOnlyHttpClient] = None,
                 gamma_url: Optional[str] = None,
                 clob_url: Optional[str] = None):
        self.gamma = gamma or ReadOnlyHttpClient(gamma_url or self.gamma_url)
        self.clob = clob or ReadOnlyHttpClient(clob_url or self.clob_url)

    def close(self) -> None:
        self.gamma.close()
        if self.clob is not self.gamma:
            self.clob.close()

    @staticmethod
    def _decode(value: Any, field: str) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("[", "{")):
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ProviderSchemaError(f"Polymarket field {field} is malformed JSON") from exc
        return value

    @staticmethod
    def _page(body: Any, key: str) -> ProviderPage:
        if isinstance(body, list):
            return ProviderPage(items=body, cursor=None, raw={key: body})
        if not isinstance(body, dict):
            raise ProviderSchemaError("Polymarket response must be an object or list")
        items = body.get(key)
        if items is None and key == "events":
            items = body.get("data")
        if not isinstance(items, list):
            raise ProviderSchemaError(f"Polymarket response missing list field {key!r}")
        cursor = body.get("next_cursor") or body.get("nextCursor")
        return ProviderPage(items=items, cursor=str(cursor) if cursor not in (None, "", "LTE=") else None, raw=body)

    def list_events(self, *, cursor: Optional[str] = None, limit: int = 100,
                    status: Optional[str] = None) -> ProviderPage:
        params: Dict[str, Any] = {"limit": max(1, min(500, limit))}
        if cursor:
            params["next_cursor"] = cursor
        if status:
            params["closed"] = str(status).lower() in {"closed", "resolved"}
        else:
            params["closed"] = False
        body = self.gamma.get_json("/events", params)
        return self._page(body, "events")

    def list_markets(self, *, cursor: Optional[str] = None, limit: int = 100,
                     status: Optional[str] = None, event_id: Optional[str] = None) -> ProviderPage:
        params: Dict[str, Any] = {"limit": max(1, min(500, limit))}
        if cursor:
            params["next_cursor"] = cursor
        if status:
            params["closed"] = str(status).lower() in {"closed", "resolved"}
        else:
            params["closed"] = False
        if event_id:
            params["event_id"] = event_id
        body = self.gamma.get_json("/markets", params)
        return self._page(body, "markets")

    def get_market(self, market_id: str) -> Dict[str, Any]:
        body = self.gamma.get_json(f"/markets/{market_id}")
        if not isinstance(body, dict):
            raise ProviderSchemaError("Polymarket market response must be an object")
        if "id" in body or "conditionId" in body:
            return body
        market = body.get("market")
        if not isinstance(market, dict):
            raise ProviderSchemaError("Polymarket market response missing market object")
        return market

    def get_orderbook(self, market_id: str, **kwargs: Any) -> Dict[str, Any]:
        token_id = kwargs.get("token_id") or market_id
        return self.clob.get_json("/book", {"token_id": token_id})

    def get_trades(self, market_id: str, **kwargs: Any) -> ProviderPage:
        # The documented /data/trades endpoint is authenticated.  Refuse rather
        # than silently requesting private/user-scoped data or pretending it is public.
        raise ProviderHttpError(
            "Polymarket public trades are not available through the documented "
            "unauthenticated surface; provide an explicitly configured server-side "
            "read-only credential or use recorded fixtures"
        )

    def get_history(self, market_id: str, **kwargs: Any) -> ProviderPage:
        params: Dict[str, Any] = {"market": market_id}
        for name in ("startTs", "endTs", "interval", "fidelity"):
            if kwargs.get(name) is not None:
                params[name] = kwargs[name]
        body = self.clob.get_json("/prices-history", params)
        if not isinstance(body, dict):
            raise ProviderSchemaError("Polymarket price history response must be an object")
        history = body.get("history")
        if not isinstance(history, list):
            raise ProviderSchemaError("Polymarket price history missing history list")
        return ProviderPage(items=history, cursor=None, raw=body)

    @staticmethod
    def normalize_event(raw: Dict[str, Any]) -> Event:
        event_id = raw.get("id") or raw.get("event_id")
        title = raw.get("title") or raw.get("question")
        if not event_id or not title:
            raise ProviderSchemaError("Polymarket event requires id and title/question")
        return Event(
            provider="polymarket", event_id=str(event_id), title=str(title),
            description=raw.get("description"), category=raw.get("category"),
            status="closed" if raw.get("closed") else ("active" if raw.get("active", True) else "inactive"),
            close_time=raw.get("endDate") or raw.get("end_date"),
            resolution={k: raw.get(k) for k in ("resolutionSource", "resolved", "winner") if k in raw},
            source_url=(f"https://polymarket.com/event/{raw.get('slug')}" if raw.get("slug") else None),
            metadata=dict(raw),
        )

    @classmethod
    def normalize_market(cls, raw: Dict[str, Any]) -> Market:
        market_id = raw.get("id") or raw.get("conditionId") or raw.get("condition_id")
        title = raw.get("question") or raw.get("title")
        if not market_id or not title:
            raise ProviderSchemaError("Polymarket market requires id/conditionId and question/title")
        labels = cls._decode(raw.get("outcomes"), "outcomes") or ["YES", "NO"]
        token_ids = cls._decode(raw.get("clobTokenIds") or raw.get("clob_token_ids") or raw.get("tokens"), "token ids")
        if isinstance(token_ids, dict):
            token_ids = [token_ids.get("yes"), token_ids.get("no")]
        if not isinstance(token_ids, list):
            token_ids = []
        outcomes: List[Outcome] = []
        for idx, label in enumerate(labels if isinstance(labels, list) else [labels]):
            token = token_ids[idx] if idx < len(token_ids) else None
            outcomes.append(Outcome(
                outcome_id=str(label).upper(), label=str(label),
                token_id=str(token) if token is not None else None,
                provider_units="probability",
            ))
        return Market(
            provider="polymarket", market_id=str(market_id),
            event_id=str(raw.get("eventId") or raw.get("event_id")) if raw.get("eventId") or raw.get("event_id") else None,
            title=str(title), description=raw.get("description"), outcomes=outcomes,
            status="closed" if raw.get("closed") else ("active" if raw.get("active", True) else "inactive"),
            open_time=raw.get("startDate") or raw.get("start_date"),
            close_time=raw.get("endDate") or raw.get("end_date"),
            resolution={k: raw.get(k) for k in ("resolved", "resolutionSource", "winner") if k in raw},
            source_url=(f"https://polymarket.com/event/{raw.get('slug')}" if raw.get("slug") else None),
            metadata=dict(raw),
        )

    @staticmethod
    def normalize_orderbook(raw: Dict[str, Any], *, token_id: Optional[str] = None,
                            event_id: Optional[str] = None,
                            market_id: Optional[str] = None) -> OrderbookSnapshot:
        bids = raw.get("bids")
        asks = raw.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ProviderSchemaError("Polymarket orderbook requires bids and asks lists")
        from tezcat.external.schemas import BookLevel
        canonical_bids = []
        canonical_asks = []
        for row in bids:
            if not isinstance(row, dict) or "price" not in row or "size" not in row:
                raise ProviderSchemaError("malformed Polymarket bid level")
            canonical_bids.append(BookLevel(price=probability(row["price"]), quantity=float(row["size"])))
        for row in asks:
            if not isinstance(row, dict) or "price" not in row or "size" not in row:
                raise ProviderSchemaError("malformed Polymarket ask level")
            canonical_asks.append(BookLevel(price=probability(row["price"]), quantity=float(row["size"])))
        canonical_bids.sort(key=lambda level: level.price, reverse=True)
        canonical_asks.sort(key=lambda level: level.price)
        best_bid = canonical_bids[0].price if canonical_bids else None
        best_ask = canonical_asks[0].price if canonical_asks else None
        mid = ((best_bid + best_ask) / 2) if best_bid is not None and best_ask is not None else None
        return OrderbookSnapshot(
            provider="polymarket", market_id=str(market_id or raw.get("market") or raw.get("asset_id") or token_id),
            event_id=event_id, outcome_id=str(token_id or raw.get("asset_id") or "UNKNOWN"),
            timestamp=normalize_timestamp(raw.get("timestamp") or raw.get("ts") or utc_now()),
            bids=canonical_bids, asks=canonical_asks, mid=mid,
            spread=(best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
            depth=sum(level.quantity for level in canonical_bids + canonical_asks),
            implied_probability=mid,
            native_bids=[dict(row) for row in bids], native_asks=[dict(row) for row in asks],
            provider_units="probability", source_url="https://clob.polymarket.com/book",
            metadata={k: raw.get(k) for k in ("condition_id", "market", "asset_id", "hash", "min_order_size", "tick_size", "neg_risk", "last_trade_price")},
        )

    @staticmethod
    def normalize_trade(raw: Dict[str, Any]) -> Trade:
        trade_id = raw.get("id") or raw.get("trade_id")
        market_id = raw.get("market") or raw.get("condition_id")
        if not trade_id or not market_id or raw.get("price") is None:
            raise ProviderSchemaError("Polymarket trade requires id, market, and price")
        return Trade(
            provider="polymarket", market_id=str(market_id), trade_id=str(trade_id),
            timestamp=normalize_timestamp(raw.get("match_time") or raw.get("timestamp")),
            outcome_id=str(raw.get("outcome") or raw.get("asset_id") or "UNKNOWN"),
            price=probability(raw["price"]), quantity=float(raw.get("size", 0)),
            side=str(raw.get("side", "unknown")).lower() if str(raw.get("side", "unknown")).lower() in {"buy", "sell"} else "unknown",
            provider_units="probability", metadata=dict(raw),
        )

    @staticmethod
    def normalize_history(raw: Dict[str, Any], *, market_id: str,
                          token_id: Optional[str] = None) -> Quote:
        timestamp = raw.get("t") or raw.get("timestamp")
        price = raw.get("p") if raw.get("p") is not None else raw.get("price")
        if timestamp is None or price is None:
            raise ProviderSchemaError("Polymarket history row requires t and p")
        p = probability(price)
        return Quote(
            provider="polymarket", market_id=market_id,
            outcome_id=str(token_id or "UNKNOWN"), timestamp=normalize_timestamp(timestamp),
            mid=p, implied_probability=p, provider_price=p, provider_units="probability",
            metadata=dict(raw),
        )
