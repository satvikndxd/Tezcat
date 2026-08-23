"""Canonical external-market schema: semantics enforced at the model layer."""

import pytest
from pydantic import ValidationError

from tezcat.external.schema import (
    BookLevel, MarketObservation, MarketStatus, OrderbookSnapshot, Quote,
)


def _quote(**kw):
    base = dict(price_transform="decimal_string/1.0")
    base.update(kw)
    return Quote(**base)


class TestQuote:
    def test_probability_bounds_enforced(self):
        with pytest.raises(ValidationError, match=r"\[0, 1\]"):
            _quote(bid=1.2)
        with pytest.raises(ValidationError, match=r"\[0, 1\]"):
            _quote(ask=-0.1)

    def test_crossed_quote_rejected(self):
        with pytest.raises(ValidationError, match="crossed"):
            _quote(bid=0.7, ask=0.6)

    def test_missing_fields_stay_none(self):
        q = _quote(bid=0.4)
        assert q.ask is None and q.spread is None

    def test_spread(self):
        assert _quote(bid=0.40, ask=0.45).spread == pytest.approx(0.05)

    def test_transform_is_mandatory(self):
        with pytest.raises(ValidationError):
            Quote(bid=0.5)


class TestOrderbook:
    def _book(self, bids, asks):
        return OrderbookSnapshot(
            provider="kalshi", market_id="M",
            bids=[BookLevel(price=p, size=s) for p, s in bids],
            asks=[BookLevel(price=p, size=s) for p, s in asks],
            book_transform="test")

    def test_helpers(self):
        book = self._book([(0.62, 100), (0.60, 50)], [(0.65, 80), (0.70, 40)])
        assert book.best_bid() == 0.62
        assert book.best_ask() == 0.65
        assert book.midpoint() == pytest.approx(0.635)
        assert book.depth(band=0.05) == pytest.approx(100 + 50 + 80)
        assert book.imbalance() == pytest.approx((100 - 80) / 180)

    def test_unsorted_sides_rejected(self):
        with pytest.raises(ValidationError, match="not sorted"):
            self._book([(0.60, 1), (0.62, 1)], [])
        with pytest.raises(ValidationError, match="not sorted"):
            self._book([], [(0.70, 1), (0.65, 1)])

    def test_crossed_book_rejected(self):
        with pytest.raises(ValidationError, match="crossed"):
            self._book([(0.70, 1)], [(0.65, 1)])

    def test_empty_book_has_null_derivatives(self):
        book = self._book([], [])
        assert book.midpoint() is None
        assert book.depth() is None
        assert book.imbalance() is None


class TestMarketObservation:
    def test_nulls_preserved_not_fabricated(self):
        obs = MarketObservation(market_id="M", timestamp="2026-01-01T00:00:00+00:00")
        assert obs.implied_probability is None
        assert obs.volume is None
        assert obs.status is MarketStatus.UNKNOWN

    def test_probability_bounds(self):
        with pytest.raises(ValidationError):
            MarketObservation(market_id="M", timestamp="t",
                              implied_probability=1.01)

    def test_negative_quantities_rejected(self):
        with pytest.raises(ValidationError):
            MarketObservation(market_id="M", timestamp="t", volume=-1)
        with pytest.raises(ValidationError):
            MarketObservation(market_id="M", timestamp="t", spread=-0.01)
