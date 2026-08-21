"""Phase F1: explicit financial-semantics contracts.

Covers duplicate order IDs, self-trade policy, cancel consistency, and
config immutability. See docs/semantics.md for the written contract.
"""

import pytest
from pydantic import ValidationError

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MarketConfig, canonical_json,
)
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook


def _order(oid, agent, side, otype, price, qty):
    return Order(order_id=oid, agent_id=agent, side=side, order_type=otype,
                 price=price, quantity=qty, remaining=qty)


def _env(**market_kw):
    book = OrderBook(0.05)
    portfolios = {
        "a": Portfolio(cash=100_000, inventory=100),
        "b": Portfolio(cash=100_000, inventory=100),
    }
    me = MatchingEngine(book, portfolios, MarketConfig(**market_kw))
    return book, portfolios, me


# ---------------------------------------------------------------------------
# Duplicate order IDs
# ---------------------------------------------------------------------------
def test_duplicate_order_id_rejected_at_validation():
    book, pf, me = _env()
    _, o1 = me.submit(_order("dup", "a", "buy", "limit", 99.0, 5), 1)
    assert o1.status == "new"
    trades, o2 = me.submit(_order("dup", "b", "buy", "limit", 99.0, 5), 1)
    assert o2.status == "rejected"
    assert trades == []
    # The original resting order is untouched.
    assert book.get_order("dup").agent_id == "a"


def test_duplicate_order_id_add_limit_raises():
    book = OrderBook(0.05)
    book.add_limit(_order("x", "a", "buy", "limit", 99.0, 5))
    with pytest.raises(ValueError, match="duplicate order id"):
        book.add_limit(_order("x", "b", "buy", "limit", 99.0, 5))


# ---------------------------------------------------------------------------
# Self-trade policy
# ---------------------------------------------------------------------------
def test_self_trade_allowed_by_default_preserves_conservation():
    book, pf, me = _env()  # default policy: "allow"
    me.submit(_order("s1", "a", "sell", "limit", 100.0, 10), 1)
    trades, o = me.submit(_order("b1", "a", "buy", "limit", 100.0, 10), 1)
    assert len(trades) == 1
    assert trades[0].buy_agent_id == trades[0].sell_agent_id == "a"
    # Net cash and inventory unchanged for the self-trading agent.
    assert abs(pf["a"].cash - 100_000) < 1e-9
    assert pf["a"].inventory == 100
    assert pf["a"].reserved_cash == 0.0 and pf["a"].reserved_inventory == 0


def test_self_trade_cancel_resting_policy():
    book, pf, me = _env(self_trade_policy="cancel_resting")
    me.submit(_order("s1", "a", "sell", "limit", 100.0, 10), 1)
    # A second seller behind agent a's order at a worse price.
    me.submit(_order("s2", "b", "sell", "limit", 100.5, 10), 1)
    trades, o = me.submit(_order("b1", "a", "buy", "limit", 100.5, 10), 1)
    # a's own resting sell was cancelled, matching continued to b's order.
    assert len(trades) == 1
    assert trades[0].sell_agent_id == "b"
    assert trades[0].price == 100.5
    assert book.get_order("s1") is None
    # Cancelled resting order's inventory reservation is fully released.
    assert pf["a"].reserved_inventory == 0
    assert pf["a"].inventory == 110


def test_self_trade_cancel_resting_releases_and_rests_remainder():
    book, pf, me = _env(self_trade_policy="cancel_resting")
    me.submit(_order("s1", "a", "sell", "limit", 100.0, 10), 1)
    trades, o = me.submit(_order("b1", "a", "buy", "limit", 100.0, 4), 1)
    assert trades == []
    assert o.status == "new" and o.remaining == 4  # rests after STP cancel
    assert book.get_order("s1") is None
    assert pf["a"].reserved_inventory == 0
    assert abs(pf["a"].reserved_cash - 400.0) < 1e-9  # only the resting buy


# ---------------------------------------------------------------------------
# Cancel consistency
# ---------------------------------------------------------------------------
def test_cancel_unknown_order_returns_none():
    book = OrderBook(0.05)
    assert book.cancel("nope", step=1) is None


def test_cancel_inconsistency_raises_and_preserves_record():
    book = OrderBook(0.05)
    o = _order("x", "a", "buy", "limit", 99.0, 5)
    book.add_limit(o)
    # Corrupt the side directly to simulate internal inconsistency.
    tick = book.to_tick(99.0)
    book.bids.levels[tick].remove(o)
    with pytest.raises(RuntimeError, match="inconsistency"):
        book.cancel("x", step=1)
    # The tracked record is NOT silently dropped (no reservation leak).
    assert book.get_order("x") is o


# ---------------------------------------------------------------------------
# Config immutability (Phase F2 boundary)
# ---------------------------------------------------------------------------
def test_config_models_are_frozen():
    cfg = ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=5)],
        total_steps=100,
    )
    with pytest.raises(ValidationError):
        cfg.total_steps = 999
    with pytest.raises(ValidationError):
        cfg.market.tick_size = 0.5


def test_canonical_json_rejects_non_native_types():
    with pytest.raises(TypeError, match="non-canonical"):
        canonical_json({"x": object()})
