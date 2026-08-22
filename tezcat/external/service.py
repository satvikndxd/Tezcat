"""Provider-neutral service layer for the External Event-Market Intelligence Layer."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from tezcat.external.datasets import ExternalDatasetStore
from tezcat.external.providers.base import ProviderRateLimitError
from tezcat.external.providers.kalshi import KalshiAdapter
from tezcat.external.providers.polymarket import PolymarketAdapter
from tezcat.external.schemas import ProviderSchemaError, utc_now


class ExternalMarketService:
    def __init__(self, store: Any, *, providers: Optional[Dict[str, Any]] = None):
        self.datasets = ExternalDatasetStore(store)
        self.providers: Dict[str, Any] = providers or {
            "kalshi": KalshiAdapter(),
            "polymarket": PolymarketAdapter(),
        }
        self._last_snapshot_monotonic = 0.0

    def close(self) -> None:
        """Close owned read-only provider transports."""
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    def provider_ids(self) -> list[str]:
        return sorted(self.providers)

    def provider(self, provider_id: str) -> Any:
        try:
            return self.providers[provider_id.lower()]
        except KeyError as exc:
            raise ValueError(f"unsupported external provider {provider_id!r}") from exc

    @staticmethod
    def _public_demo() -> bool:
        return os.environ.get("TEZCAT_PUBLIC_DEMO", "").lower() in {"1", "true", "yes"}

    @staticmethod
    def _public_int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    def bounded_discovery_limit(self, limit: int) -> int:
        if not self._public_demo():
            return limit
        return min(limit, self._public_int("TEZCAT_PUBLIC_MAX_EXTERNAL_ITEMS", 50))

    def _check_snapshot_budget(self) -> None:
        if not self._public_demo():
            return
        current = time.monotonic()
        minimum = float(os.environ.get("TEZCAT_PUBLIC_EXTERNAL_MIN_INTERVAL", "10"))
        if current - self._last_snapshot_monotonic < max(0.0, minimum):
            raise ProviderRateLimitError("public demo external snapshot refresh is rate limited")
        if len(self.datasets.list()) >= self._public_int("TEZCAT_PUBLIC_MAX_EXTERNAL_DATASETS", 25):
            raise ProviderRateLimitError("public demo external dataset limit reached")
        self._last_snapshot_monotonic = current

    def list_events(self, provider_id: str, **kwargs: Any) -> Any:
        if "limit" in kwargs:
            kwargs["limit"] = self.bounded_discovery_limit(kwargs["limit"])
        return self.provider(provider_id).list_events(**kwargs)

    def list_markets(self, provider_id: str, **kwargs: Any) -> Any:
        if "limit" in kwargs:
            kwargs["limit"] = self.bounded_discovery_limit(kwargs["limit"])
        return self.provider(provider_id).list_markets(**kwargs)

    def get_market(self, provider_id: str, market_id: str) -> Any:
        return self.provider(provider_id).get_market(market_id)

    def snapshot(self, provider_id: str, market_id: str, *, token_id: Optional[str] = None,
                 history: Optional[Dict[str, Any]] = None,
                 trades: Optional[Dict[str, Any]] = None,
                 source_id: Optional[str] = None,
                 license_terms: str = "unknown — verify provider terms before redistribution",
                 permitted_use: str = "local analysis only unless provider terms state otherwise") -> Dict[str, Any]:
        self._check_snapshot_budget()
        provider = self.provider(provider_id)
        raw_market = provider.get_market(market_id)
        normalized_market = provider.normalize_market(raw_market)
        event_id = normalized_market.event_id
        raw_orderbook: Optional[Dict[str, Any]] = None
        normalized_book: Optional[Dict[str, Any]] = None
        if provider_id.lower() == "polymarket":
            token_id = token_id or next((o.token_id for o in normalized_market.outcomes if o.token_id), None)
            if not token_id:
                raise ProviderSchemaError("Polymarket snapshot requires an outcome token_id")
            raw_orderbook = provider.get_orderbook(token_id, token_id=token_id)
            normalized_book = provider.normalize_orderbook(
                raw_orderbook, token_id=token_id, market_id=normalized_market.market_id, event_id=event_id
            ).model_dump(mode="json")
        else:
            raw_orderbook = provider.get_orderbook(market_id)
            normalized_book = provider.normalize_orderbook(
                raw_orderbook, market_id=normalized_market.market_id, event_id=event_id, timestamp=utc_now()
            ).model_dump(mode="json")

        raw_bundle: Dict[str, Any] = {
            "retrieved_at": utc_now(),
            "provider": provider_id.lower(),
            "market": raw_market,
            "orderbook": raw_orderbook,
        }
        normalized_bundle: Dict[str, Any] = {
            "schema_version": "s3.1",
            "data_class": "OBSERVED",
            "provider": provider_id.lower(),
            "retrieved_at": raw_bundle["retrieved_at"],
            "market": normalized_market.model_dump(mode="json"),
            "orderbook": normalized_book,
            "quotes": [], "trades": [],
        }

        if history is not None:
            raw_history_page = provider.get_history(market_id, **history)
            raw_bundle["history"] = raw_history_page.raw
            for row in raw_history_page.items:
                if provider_id.lower() == "kalshi":
                    quote = provider.normalize_candlestick(row, market_id=market_id)
                else:
                    quote = provider.normalize_history(row, market_id=market_id, token_id=token_id)
                normalized_bundle["quotes"].append(quote.model_dump(mode="json"))
        if trades is not None:
            raw_trade_page = provider.get_trades(market_id, **trades)
            raw_bundle["trades"] = raw_trade_page.raw
            normalized_bundle["trades"] = [
                provider.normalize_trade(row).model_dump(mode="json") for row in raw_trade_page.items
            ]

        manifest = self.datasets.register(
            provider=provider_id.lower(), source_id=source_id or market_id,
            raw=raw_bundle, normalized=normalized_bundle,
            event_ids=[event_id] if event_id else [], market_ids=[normalized_market.market_id],
            source_url=(normalized_market.source_url or f"provider://{provider_id}/{market_id}"),
            endpoint_id="snapshot", adapter_version=provider.adapter_version,
            license_terms=license_terms, permitted_use=permitted_use,
        )
        return {
            "manifest": manifest.model_dump(mode="json"),
            "raw": raw_bundle,
            "normalized": normalized_bundle,
        }
