import pytest

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import MarketConfig
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook


@pytest.fixture
def env():
    book = OrderBook(0.05)
    portfolios = {
        "buyer": Portfolio(cash=100_000, inventory=100),
        "seller": Portfolio(cash=100_000, inventory=100),
        "resting": Portfolio(cash=100_000, inventory=100),
        "poor": Portfolio(cash=50, inventory=0),
    }
    me = MatchingEngine(book, portfolios, MarketConfig())
    return book, portfolios, me


def _submit(me, agent, side, otype, price, qty, step=1):
    o = Order(order_id=f"o_{agent}_{side}_{price}_{qty}", agent_id=agent, side=side,
              order_type=otype, price=price, quantity=qty, remaining=qty)
    return me.submit(o, step)


def test_buy_crosses_ask(env):
    book, pf, me = env
    _submit(me, "resting", "sell", "limit", 100.0, 10)
    trades, order = _submit(me, "buyer", "buy", "limit", 100.1, 10)
    assert len(trades) == 1
    assert trades[0].price == 100.0  # resting price, not incoming
    assert trades[0].quantity == 10
    assert order.status == "filled"
    assert pf["buyer"].cash == 100_000 - 1000
    assert pf["buyer"].inventory == 110
    assert pf["resting"].cash == 100_000 + 1000
    assert pf["resting"].inventory == 90


def test_sell_crosses_bid(env):
    book, pf, me = env
    _submit(me, "resting", "buy", "limit", 100.0, 8)
    trades, order = _submit(me, "seller", "sell", "limit", 99.9, 8)
    assert len(trades) == 1 and trades[0].price == 100.0
    assert pf["seller"].inventory == 92


def test_partial_fill_and_rest(env):
    book, pf, me = env
    _submit(me, "resting", "sell", "limit", 100.0, 5)
    trades, order = _submit(me, "buyer", "buy", "limit", 100.0, 12)
    assert trades[0].quantity == 5
    assert order.status == "partial"
    assert order.remaining == 7
    assert book.best_bid == 100.0  # remainder rests


def test_time_priority_execution(env):
    book, pf, me = env
    _submit(me, "resting", "sell", "limit", 100.0, 5)
    _submit(me, "seller", "sell", "limit", 100.0, 5)
    trades, _ = _submit(me, "buyer", "buy", "market", None, 5)
    assert trades[0].sell_agent_id == "resting"  # earlier order fills first


def test_market_order_empty_book(env):
    book, pf, me = env
    trades, order = _submit(me, "buyer", "buy", "market", None, 10)
    assert trades == []
    assert order.status == "cancelled"


def test_market_order_walks_book(env):
    book, pf, me = env
    _submit(me, "resting", "sell", "limit", 100.0, 5)
    _submit(me, "resting", "sell", "limit", 100.5, 5)
    trades, order = _submit(me, "buyer", "buy", "market", None, 10)
    assert [t.price for t in trades] == [100.0, 100.5]
    assert order.status == "filled"


def test_insufficient_cash_rejected(env):
    book, pf, me = env
    trades, order = _submit(me, "poor", "buy", "limit", 100.0, 10)
    assert order.status == "rejected"


def test_insufficient_inventory_rejected(env):
    book, pf, me = env
    trades, order = _submit(me, "poor", "sell", "limit", 100.0, 10)
    assert order.status == "rejected"


def test_market_buy_capped_by_cash(env):
    book, pf, me = env
    pf["capped"] = Portfolio(cash=250, inventory=0)
    _submit(me, "resting", "sell", "limit", 100.0, 10)
    trades, order = _submit(me, "capped", "buy", "market", None, 10)
    assert sum(t.quantity for t in trades) == 2  # 250 // 100
    assert pf["capped"].cash >= 0


def test_reservations_prevent_overcommit(env):
    book, pf, me = env
    pf["tight"] = Portfolio(cash=1000, inventory=0)
    _, o1 = _submit(me, "tight", "buy", "limit", 100.0, 10)  # reserves all cash
    assert o1.status in ("new", "partial")
    _, o2 = _submit(me, "tight", "buy", "limit", 100.0, 1)
    assert o2.status == "rejected"  # no free cash left


def test_conservation(env):
    book, pf, me = env
    total_cash_before = sum(p.cash for p in pf.values())
    total_inv_before = sum(p.inventory for p in pf.values())
    _submit(me, "resting", "sell", "limit", 100.0, 10)
    _submit(me, "buyer", "buy", "market", None, 6)
    _submit(me, "seller", "sell", "limit", 99.5, 4)
    _submit(me, "buyer", "buy", "limit", 99.5, 4)
    assert abs(sum(p.cash for p in pf.values()) - total_cash_before) < 1e-6
    assert sum(p.inventory for p in pf.values()) == total_inv_before


def test_cancel_releases_reservation(env):
    book, pf, me = env
    _, o = _submit(me, "buyer", "buy", "limit", 100.0, 10)
    assert pf["buyer"].reserved_cash == 1000.0
    cancelled = book.cancel(o.order_id, step=2)
    me.release_on_cancel(cancelled)
    assert pf["buyer"].reserved_cash == 0.0
