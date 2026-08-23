"""Provider adapters against labeled offline fixtures — no network, ever.

Covers the S3 failure matrix: happy paths, pagination, rate limits,
timeouts, malformed payloads, schema drift, and unit normalization for
both documented Kalshi price representations.
"""

from pathlib import Path

import pytest

from tezcat.external.fixtures import FixtureError, FixtureTransport, load_fixture
from tezcat.external.kalshi import KalshiAdapter
from tezcat.external.polymarket import PolymarketAdapter
from tezcat.external.provider import (
    ProviderError, ProviderHTTPError, ProviderRateLimited,
    ProviderSchemaError, ProviderTimeout, RateLimiter, RequestPolicy,
)
from tezcat.external.schema import MarketStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"
NOW = "2026-08-23T00:00:00+00:00"


def _noop(_):
    return None


def kalshi(routes=None) -> KalshiAdapter:
    transport = (FixtureTransport(routes) if routes is not None else
                 FixtureTransport.from_file(FIXTURES / "kalshi" / "synthetic_event.json"))
    return KalshiAdapter(transport=transport, base_url="https://fixture.local/trade-api/v2",
                         limiter=RateLimiter(sleep=_noop),
                         policy=RequestPolicy(sleep=_noop),
                         source_kind="synthetic_fixture", now=lambda: NOW)


def polymarket(routes=None) -> PolymarketAdapter:
    transport = (FixtureTransport(routes) if routes is not None else
                 FixtureTransport.from_file(FIXTURES / "polymarket" / "synthetic_event.json"))
    return PolymarketAdapter(transport=transport,
                             gamma_url="https://fixture.local",
                             clob_url="https://fixture.local",
                             data_url="https://fixture.local",
                             limiter=RateLimiter(sleep=_noop),
                             policy=RequestPolicy(sleep=_noop),
                             source_kind="synthetic_fixture", now=lambda: NOW)


# ---------------------------------------------------------------------------
# Fixture policy
# ---------------------------------------------------------------------------
class TestFixturePolicy:
    def test_bundles_are_labeled(self):
        for name in ("kalshi", "polymarket"):
            data = load_fixture(FIXTURES / name / "synthetic_event.json")
            assert data["fixture_label"] == "SYNTHETIC FIXTURE"

    def test_unlabeled_fixture_rejected(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"routes": {}}')
        with pytest.raises(FixtureError, match="fixture_label"):
            load_fixture(p)

    def test_recorded_without_provenance_rejected(self, tmp_path):
        p = tmp_path / "rec.json"
        p.write_text('{"fixture_label": "RECORDED PROVIDER RESPONSE", "routes": {}}')
        with pytest.raises(FixtureError, match="provenance"):
            load_fixture(p)

    def test_unrouted_request_never_falls_through(self):
        with pytest.raises(ProviderError, match="no route"):
            kalshi({}).get_market("NOPE")


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------
class TestKalshi:
    def test_events(self):
        events, cursor = kalshi().list_events()
        assert cursor is None
        (ev,) = events
        assert ev.provider == "kalshi"
        assert ev.event_id == "SYN-EVENT-2026"
        assert ev.metadata["series_ticker"] == "SYN-SERIES"
        assert ev.provenance.source_kind == "synthetic_fixture"
        assert ev.provenance.adapter_version.startswith("kalshi-adapter/")

    def test_market_dollars_normalization(self):
        m = kalshi().get_market("SYN-MKT-YES")
        assert m.status is MarketStatus.OPEN
        assert m.provider_status == "open"
        assert m.tick_size == pytest.approx(0.01)  # cents int → /100
        assert [o.name for o in m.outcomes] == ["yes", "no"]

    def test_orderbook_no_bids_become_yes_asks(self):
        book = kalshi().get_orderbook("SYN-MKT-YES")
        assert book.best_bid() == pytest.approx(0.62)
        # no bid 27¢ → yes ask at 1 − 0.27 = 0.73
        assert book.best_ask() == pytest.approx(0.73)
        assert "1 - no_bid" in book.book_transform
        assert book.native["orderbook"]["no"][0] == [27, 150]  # verbatim

    def test_trades_cents_normalization(self):
        trades, cursor = kalshi().get_trades("SYN-MKT-YES")
        assert cursor is None
        assert trades[0].price == pytest.approx(0.70)
        assert trades[0].price_transform == "cents_int/100"
        assert trades[0].provider_price == "70"

    def test_history_candles(self):
        rows = kalshi().get_history("SYN-MKT-YES", 0, 10**10)
        assert len(rows) == 72
        first, last = rows[0], rows[-1]
        assert 0.3 < first["implied_probability"] < 0.55
        assert 0.6 < last["implied_probability"] <= 0.85
        assert first["bid"] is not None and first["bid"] < first["ask"]
        assert first["depth"] is None  # not in candles — never fabricated
        assert first["timestamp"] < last["timestamp"]

    def test_pagination_cursor(self):
        routes = {
            "/markets?cursor=c1&limit=1": {"markets": [], "cursor": ""},
            "/markets?limit=1": {
                "markets": [{"ticker": "A", "event_ticker": "E",
                             "status": "open", "title": "?"}],
                "cursor": "c1"},
        }
        a = kalshi(routes)
        page1, cursor = a.list_markets(limit=1)
        assert len(page1) == 1 and cursor == "c1"
        page2, cursor2 = a.list_markets(limit=1, cursor=cursor)
        assert page2 == [] and cursor2 is None

    def test_unknown_status_maps_to_unknown_but_is_preserved(self):
        routes = {"/markets/X": {"market": {
            "ticker": "X", "event_ticker": "E", "status": "quantum",
            "title": "?"}}}
        m = kalshi(routes).get_market("X")
        assert m.status is MarketStatus.UNKNOWN
        assert m.provider_status == "quantum"

    def test_settled_market_carries_resolution(self):
        routes = {"/markets/X": {"market": {
            "ticker": "X", "event_ticker": "E", "status": "settled",
            "result": "yes", "settlement_value_dollars": "1.00",
            "title": "?"}}}
        m = kalshi(routes).get_market("X")
        assert m.status is MarketStatus.RESOLVED
        assert m.resolution.resolved and m.resolution.outcome == "yes"
        assert m.resolution.settlement_value == pytest.approx(1.0)


class TestKalshiFailures:
    def test_missing_field_is_schema_drift(self):
        routes = {"/markets/X": {"market": {"event_ticker": "E",
                                            "status": "open"}}}  # no ticker
        with pytest.raises(ProviderSchemaError, match="ticker"):
            kalshi(routes).get_market("X")

    def test_renamed_envelope_is_schema_drift(self):
        routes = {"/events": {"eventList": []}}
        with pytest.raises(ProviderSchemaError, match="events"):
            kalshi(routes).list_events()

    def test_price_without_either_representation_is_drift(self):
        routes = {"/markets/trades": {"trades": [
            {"trade_id": "t", "count": 1, "created_time": "x"}], "cursor": ""}}
        with pytest.raises(ProviderSchemaError, match="yes_price"):
            kalshi(routes).get_trades("M")

    def test_out_of_range_price_rejected(self):
        routes = {"/markets/trades": {"trades": [
            {"trade_id": "t", "count": 1, "yes_price": 170,
             "created_time": "x"}], "cursor": ""}}
        with pytest.raises(ProviderSchemaError, match=r"outside \[0, 1\]"):
            kalshi(routes).get_trades("M")

    def test_negative_book_size_rejected(self):
        routes = {"/markets/M/orderbook": {"orderbook": {
            "yes": [[50, -5]], "no": []}}}
        with pytest.raises(ProviderSchemaError, match="out of range"):
            kalshi(routes).get_orderbook("M")

    def test_malformed_json_explicit(self):
        routes = {"/events": {"__malformed__": "{not json"}}
        with pytest.raises(ProviderSchemaError, match="malformed JSON"):
            kalshi(routes).list_events()

    def test_http_4xx_immediate(self):
        routes = {"/markets/M": {"__status__": 404, "__body__": "not found"}}
        with pytest.raises(ProviderHTTPError, match="404"):
            kalshi(routes).get_market("M")

    def test_http_5xx_retries_then_raises(self):
        routes = {"/events": {"__status__": 503}}
        a = kalshi(routes)
        with pytest.raises(ProviderHTTPError, match="503"):
            a.list_events()
        assert len(a.transport.requests) == 3  # retried to policy limit

    def test_rate_limit_explicit(self):
        routes = {"/events": {"__status__": 429}}
        with pytest.raises(ProviderRateLimited):
            kalshi(routes).list_events()

    def test_timeout_explicit(self):
        routes = {"/events": {"__timeout__": True}}
        with pytest.raises(ProviderTimeout):
            kalshi(routes).list_events()

    def test_transient_failure_then_success(self):
        routes = {"/events": {"__sequence__": [
            {"__status__": 500}, {"events": [], "cursor": ""}]}}
        events, cursor = kalshi(routes).list_events()
        assert events == [] and cursor is None


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------
class TestPolymarket:
    def test_events(self):
        events, cursor = polymarket().list_events()
        assert cursor is None
        (ev,) = events
        assert ev.provider == "polymarket"
        assert ev.event_id == "888001"

    def test_market_outcome_token_pairing(self):
        m = polymarket().get_market("912001")
        assert m.status is MarketStatus.OPEN
        assert [(o.name, o.token_id) for o in m.outcomes] == [
            ("Yes", "777001"), ("No", "777002")]
        assert m.metadata["conditionId"] == "0xsyntheticcondition"
        assert m.tick_size == pytest.approx(0.01)

    def test_orderbook_identity_transform(self):
        book = polymarket().get_orderbook("777001")
        assert book.best_bid() == pytest.approx(0.66)
        assert book.best_ask() == pytest.approx(0.69)
        assert "decimal_string/1.0" in book.book_transform
        assert book.native["book"]["bids"][0]["price"] == "0.66"

    def test_trades_drop_wallets(self):
        trades, _ = polymarket().get_trades("0xsyntheticcondition")
        assert trades[0].price == pytest.approx(0.68)
        for t in trades:
            dumped = t.model_dump_json()
            assert "WALLET-MUST-NOT-APPEAR" not in dumped

    def test_history(self):
        rows = polymarket().get_history("777001", 0, 10**10)
        assert len(rows) == 72
        assert rows[0]["bid"] is None  # endpoint has no quotes — stays None
        assert 0 <= rows[0]["implied_probability"] <= 1

    def test_offset_pagination(self):
        row = {"id": "1", "question": "?", "outcomes": '["Yes","No"]',
               "active": True, "closed": False}
        routes = {"/markets?limit=1&offset=0": [row],
                  "/markets?limit=1&offset=1": []}
        a = polymarket(routes)
        page1, cursor = a.list_markets(limit=1)
        assert len(page1) == 1 and cursor == "1"
        page2, cursor2 = a.list_markets(limit=1, cursor=cursor)
        assert page2 == [] and cursor2 is None


class TestPolymarketFailures:
    def test_mismatched_outcome_tokens_is_drift(self):
        routes = {"/markets/1": {"id": "1", "question": "?",
                                 "outcomes": '["Yes","No"]',
                                 "clobTokenIds": '["only-one"]',
                                 "active": True, "closed": False}}
        with pytest.raises(ProviderSchemaError, match="refusing to pair"):
            polymarket(routes).get_market("1")

    def test_non_json_string_list_is_drift(self):
        routes = {"/markets/1": {"id": "1", "question": "?",
                                 "outcomes": "Yes,No",
                                 "active": True, "closed": False}}
        with pytest.raises(ProviderSchemaError, match="JSON-encoded"):
            polymarket(routes).get_market("1")

    def test_object_instead_of_list_is_drift(self):
        routes = {"/events": {"data": []}}
        with pytest.raises(ProviderSchemaError, match="expected a list"):
            polymarket(routes).list_events()

    def test_out_of_range_price_rejected(self):
        routes = {"/book": {"bids": [{"price": "1.20", "size": "1"}],
                            "asks": []}}
        with pytest.raises(ProviderSchemaError, match=r"outside \[0, 1\]"):
            polymarket(routes).get_orderbook("t")

    def test_missing_history_key_is_drift(self):
        routes = {"/prices-history": {"prices": []}}
        with pytest.raises(ProviderSchemaError, match="history"):
            polymarket(routes).get_history("t", 0, 1)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class TestRateLimiter:
    def test_throttles_after_burst(self):
        clock = {"t": 0.0}
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            clock["t"] += s

        rl = RateLimiter(rate_per_second=2.0, burst=2,
                         clock=lambda: clock["t"], sleep=fake_sleep)
        for _ in range(4):
            rl.acquire()
        assert len(sleeps) == 2          # two requests beyond the burst waited
        assert all(s > 0 for s in sleeps)
