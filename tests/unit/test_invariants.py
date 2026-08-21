"""Phase F1: property-style invariant tests over randomized order streams.

Thousands of generated submissions/cancellations (including invalid orders)
are pushed through the matching engine, and after every burst the economic
laws are re-checked:

- cash conservation (no fees exist, so total cash is constant)
- inventory conservation
- no negative cash / inventory under the default policy
- reservation reconciliation: reserved cash/inventory equals exactly the
  resources committed to resting open orders
- price-time (FIFO) priority at each level

The generator is seeded so any failure is reproducible from the printed seed.
"""

import random

import pytest

from tezcat.agents.portfolio import INVARIANT_EPS, Portfolio
from tezcat.core.config import MarketConfig
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook

AGENTS = [f"ag_{i}" for i in range(6)]


def _check_all(book: OrderBook, portfolios, total_cash0: float, total_inv0: int, seed: int):
    ctx = f"(generator seed={seed})"
    total_cash = sum(p.cash for p in portfolios.values())
    total_inv = sum(p.inventory for p in portfolios.values())
    assert abs(total_cash - total_cash0) < 1e-4, f"cash not conserved {ctx}"
    assert total_inv == total_inv0, f"inventory not conserved {ctx}"

    expected_cash = {a: 0.0 for a in portfolios}
    expected_inv = {a: 0 for a in portfolios}
    for side in (book.bids, book.asks):
        for o in side.all_orders():
            if o.side == "buy":
                expected_cash[o.agent_id] += (o.price or 0.0) * o.remaining
            else:
                expected_inv[o.agent_id] += o.remaining

    for aid, p in portfolios.items():
        assert p.cash >= -INVARIANT_EPS, f"negative cash for {aid} {ctx}"
        assert p.inventory >= 0, f"negative inventory for {aid} {ctx}"
        assert abs(p.reserved_cash - expected_cash[aid]) <= INVARIANT_EPS, (
            f"cash reservation mismatch for {aid}: "
            f"{p.reserved_cash} != {expected_cash[aid]} {ctx}")
        assert p.reserved_inventory == expected_inv[aid], (
            f"inventory reservation mismatch for {aid} {ctx}")

    # FIFO: within each level, arrival sequence numbers strictly increase.
    for side in (book.bids, book.asks):
        for tick in side.sorted_ticks:
            seqs = [o.seq for o in side.levels[tick]]
            assert seqs == sorted(seqs), f"FIFO violated at tick {tick} {ctx}"


@pytest.mark.parametrize("seed", [1, 7, 42, 1234, 99999])
@pytest.mark.parametrize("self_trade_policy", ["allow", "cancel_resting"])
def test_random_order_stream_invariants(seed, self_trade_policy):
    rng = random.Random(seed)
    book = OrderBook(0.05)
    portfolios = {a: Portfolio(cash=rng.choice([500.0, 5_000.0, 50_000.0]),
                               inventory=rng.choice([0, 10, 200]))
                  for a in AGENTS}
    config = MarketConfig(max_order_size=50, self_trade_policy=self_trade_policy)
    me = MatchingEngine(book, portfolios, config)

    total_cash0 = sum(p.cash for p in portfolios.values())
    total_inv0 = sum(p.inventory for p in portfolios.values())
    oid = 0

    for step in range(1, 401):
        for _ in range(rng.randint(1, 5)):
            action = rng.random()
            if action < 0.15:
                # Cancel a random resting order (sometimes a bogus ID).
                open_ids = [o.order_id
                            for s in (book.bids, book.asks) for o in s.all_orders()]
                target = rng.choice(open_ids) if open_ids and rng.random() < 0.9 else "bogus"
                cancelled = book.cancel(target, step)
                if cancelled is not None:
                    me.release_on_cancel(cancelled)
                continue
            oid += 1
            side = rng.choice(["buy", "sell"])
            otype = "market" if rng.random() < 0.25 else "limit"
            # Mix of sane, aggressive, sub-tick, oversized, and zero orders.
            price = None if otype == "market" else rng.choice(
                [round(rng.uniform(95, 105), 2), 0.01, 0.0, 5000.0])
            qty = rng.choice([0, 1, 5, 25, 49, 51, 500])
            order = Order(order_id=f"p_{oid}", agent_id=rng.choice(AGENTS),
                          side=side, order_type=otype, price=price,
                          quantity=qty, remaining=qty)
            me.submit(order, step)
        if step % 40 == 0:
            _check_all(book, portfolios, total_cash0, total_inv0, seed)

    _check_all(book, portfolios, total_cash0, total_inv0, seed)


def test_tick_snapping_on_all_resting_orders():
    """Every resting order's price must be an exact tick multiple."""
    rng = random.Random(7)
    book = OrderBook(0.05)
    portfolios = {a: Portfolio(cash=100_000.0, inventory=1_000) for a in AGENTS}
    me = MatchingEngine(book, portfolios, MarketConfig())
    for i in range(300):
        side = rng.choice(["buy", "sell"])
        price = round(rng.uniform(90, 110), 3)  # deliberately off-tick
        qty = rng.randint(1, 20)
        me.submit(Order(order_id=f"t_{i}", agent_id=rng.choice(AGENTS), side=side,
                        order_type="limit", price=price, quantity=qty, remaining=qty), 1)
    for s in (book.bids, book.asks):
        for o in s.all_orders():
            tick = book.to_tick(o.price)
            assert abs(o.price - book.to_price(tick)) < 1e-12
