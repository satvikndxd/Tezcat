from __future__ import annotations

import json
from pathlib import Path

import pytest

from tezcat.external.datasets import ExternalDatasetStore
from tezcat.external.providers.base import ProviderHttpError, ProviderPage, ProviderRateLimitError
from tezcat.external.providers.kalshi import KalshiAdapter
from tezcat.external.providers.polymarket import PolymarketAdapter
from tezcat.external.service import ExternalMarketService
from tezcat.external.research import (
    extract_event_signature, match_events, probability_divergence,
)
from tezcat.external.schemas import (
    Event, OrderbookSnapshot, Quote, Trade, checksum,
)
from tezcat.persistence.local import LocalStore


ROOT = Path(__file__).parent / "fixtures"


def load_fixture(provider: str):
    return json.loads((ROOT / provider / "market_bundle.json").read_text())


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        key = path
        if key not in self.responses:
            raise AssertionError(f"unexpected fake path {path}")
        return self.responses[key]

    def close(self):
        pass


def test_kalshi_normalization_preserves_binary_semantics():
    fixture = load_fixture("kalshi")
    client = FakeClient({
        "/markets": {"markets": [fixture["market"]], "cursor": "next"},
        "/markets/KXTEST-YES/orderbook": fixture["orderbook"],
        "/markets/trades": fixture["trades"],
    })
    adapter = KalshiAdapter(client=client)
    page = adapter.list_markets(event_id="KXTEST-EVENT", status="open")
    assert page.cursor == "next"
    market = adapter.normalize_market(page.items[0])
    assert [outcome.outcome_id for outcome in market.outcomes] == ["YES", "NO"]
    book = adapter.normalize_orderbook(
        adapter.get_orderbook("KXTEST-YES"), market_id="KXTEST-YES", timestamp="2026-08-22T00:00:00Z"
    )
    assert book.bids[0].price == pytest.approx(0.42)
    assert book.asks[0].price == pytest.approx(0.45)  # 1 - NO bid 0.55
    trade_page = adapter.get_trades("KXTEST-YES")
    trade = adapter.normalize_trade(trade_page.items[0])
    assert trade.outcome_id == "YES"
    assert trade.price == pytest.approx(0.43)
    assert client.calls[-1][0] == "/markets/trades"


def test_kalshi_candlesticks_use_documented_live_path():
    fixture = load_fixture("kalshi")
    client = FakeClient({
        "/series/KXTEST/markets/KXTEST-YES/candlesticks": fixture["candlesticks"]
    })
    adapter = KalshiAdapter(client=client)
    page = adapter.get_history(
        "KXTEST-YES", series_ticker="KXTEST", start_ts=1, end_ts=2,
        period_interval=60
    )
    assert len(page.items) == 2
    assert client.calls[0][0] == "/series/KXTEST/markets/KXTEST-YES/candlesticks"
    quote = adapter.normalize_candlestick(page.items[1], market_id="KXTEST-YES")
    assert quote.implied_probability == pytest.approx(0.52)


def test_polymarket_public_read_only_surfaces_and_trade_boundary():
    fixture = load_fixture("polymarket")
    gamma = FakeClient({
        "/markets/condition-1": fixture["market"],
        "/events": {"events": [fixture["event"]], "next_cursor": "next"},
    })
    clob = FakeClient({
        "/book": fixture["orderbook"],
        "/prices-history": fixture["price_history"],
    })
    adapter = PolymarketAdapter(gamma=gamma, clob=clob)
    market = adapter.normalize_market(adapter.get_market("condition-1"))
    assert market.outcomes[0].token_id == "token-yes-1"
    book = adapter.normalize_orderbook(
        adapter.get_orderbook("token-yes-1", token_id="token-yes-1"),
        token_id="token-yes-1", market_id="condition-1"
    )
    assert book.mid == pytest.approx(0.44)
    history = adapter.get_history("token-yes-1", interval="1h")
    assert len(history.items) == 2
    quote = adapter.normalize_history(history.items[1], market_id="condition-1", token_id="token-yes-1")
    assert quote.implied_probability == pytest.approx(0.52)
    with pytest.raises(ProviderHttpError, match="not available"):
        adapter.get_trades("condition-1")


def test_lineage_dataset_is_idempotent_and_versions_changed_raw(tmp_path):
    store = ExternalDatasetStore(LocalStore(str(tmp_path)))
    raw = {"fixture_type": "SYNTHETIC FIXTURE", "value": 1}
    normalized = {"schema_version": "s3.1", "value": 1}
    first = store.register(
        provider="kalshi", source_id="fixture", raw=raw, normalized=normalized,
        event_ids=["e1"], market_ids=["m1"], source_url="fixture://kalshi",
        endpoint_id="fixture", adapter_version="test-v1"
    )
    same = store.register(
        provider="kalshi", source_id="fixture", raw=raw, normalized=normalized,
        event_ids=["e1"], market_ids=["m1"], source_url="fixture://kalshi",
        endpoint_id="fixture", adapter_version="test-v1"
    )
    changed = store.register(
        provider="kalshi", source_id="fixture", raw={**raw, "value": 2}, normalized={**normalized, "value": 2},
        event_ids=["e1"], market_ids=["m1"], source_url="fixture://kalshi",
        endpoint_id="fixture", adapter_version="test-v1"
    )
    assert first.dataset_id == same.dataset_id
    assert first.dataset_version == 1
    assert changed.dataset_id != first.dataset_id
    assert changed.dataset_version == 2
    assert len(store.list("kalshi")) == 2


def test_event_matching_is_cautious_and_divergence_is_not_arbitrage():
    a = Event(provider="kalshi", event_id="a", title="Will rainfall exceed 10 inches?", category="weather")
    b = Event(provider="polymarket", event_id="b", title="Will rainfall exceed 10 inches?", category="weather")
    match = match_events(a, b)
    assert match.level == "EXACT"
    divergence = probability_divergence(0.67, 0.72)
    assert divergence["label"] == "cross-provider probability divergence"
    assert "arbitrage" not in divergence["interpretation"]


def test_signature_is_hashable_and_preserves_observed_inferred_boundary():
    quotes = [
        Quote(provider="kalshi", market_id="m1", outcome_id="YES", timestamp="2026-08-22T00:00:00Z", mid=0.42, implied_probability=0.42),
        Quote(provider="kalshi", market_id="m1", outcome_id="YES", timestamp="2026-08-22T01:00:00Z", mid=0.52, implied_probability=0.52),
    ]
    trades = [Trade(provider="kalshi", market_id="m1", trade_id="t1", timestamp="2026-08-22T00:30:00Z", outcome_id="YES", price=0.45, quantity=3)]
    signature = extract_event_signature(provider="kalshi", dataset_id="extds_1", market_id="m1", quotes=quotes, trades=trades)
    assert signature.delta_probability == pytest.approx(0.10)
    assert signature.metadata["data_class"] == "INFERRED"
    assert len(signature.checksum) == 64


def test_polymarket_bare_list_pagination_and_schema_drift_are_explicit():
    from tezcat.external.schemas import ProviderSchemaError

    fixture = load_fixture("polymarket")
    adapter = PolymarketAdapter(gamma=FakeClient({
        "/events": [fixture["event"]],
        "/markets": "not-an-object-or-list",
    }))
    page = adapter.list_events()
    assert page.cursor is None
    assert page.items == [fixture["event"]]
    with pytest.raises(ProviderSchemaError, match="object or list"):
        adapter.list_markets()


def test_missing_orderbook_timestamp_is_retrieval_time_not_epoch_zero():
    fixture = load_fixture("kalshi")
    raw = {key: value for key, value in fixture["orderbook"].items() if key != "timestamp"}
    book = KalshiAdapter.normalize_orderbook(raw, market_id="KXTEST-YES")
    assert not book.timestamp.startswith("1970-")
    assert book.timestamp.endswith("Z")


def test_invalid_provider_probability_fails_as_data_quality_error():
    fixture = load_fixture("kalshi")
    raw = json.loads(json.dumps(fixture["orderbook"]))
    raw["orderbook_fp"]["yes_dollars"][0][0] = "1.5"
    from tezcat.external.schemas import DataQualityError
    with pytest.raises(DataQualityError, match=r"outside \[0,1\]"):
        KalshiAdapter.normalize_orderbook(raw, market_id="KXTEST-YES")


def test_public_demo_snapshot_budget_is_bounded(monkeypatch, tmp_path):
    fixture = load_fixture("kalshi")
    provider = KalshiAdapter(client=FakeClient({
        "/markets/KXTEST-YES": {"market": fixture["market"]},
        "/markets/KXTEST-YES/orderbook": fixture["orderbook"],
    }))
    service = ExternalMarketService(LocalStore(str(tmp_path)), providers={"kalshi": provider})
    monkeypatch.setenv("TEZCAT_PUBLIC_DEMO", "1")
    monkeypatch.setenv("TEZCAT_PUBLIC_EXTERNAL_MIN_INTERVAL", "0")
    monkeypatch.setenv("TEZCAT_PUBLIC_MAX_EXTERNAL_DATASETS", "1")
    service.snapshot("kalshi", "KXTEST-YES", source_id="bounded-fixture")
    with pytest.raises(ProviderRateLimitError, match="dataset limit"):
        service.snapshot("kalshi", "KXTEST-YES", source_id="bounded-fixture-2")
