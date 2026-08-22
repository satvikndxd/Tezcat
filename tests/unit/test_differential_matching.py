"""Phase F11: differential test — reference matcher vs the real engine.

The reference implements the documented matching rules (docs/semantics.md)
in the most naive way possible: flat lists, linear scans, no price-level
structures. It shares only the Portfolio class (so settlement float ops are
op-for-op identical). Both implementations consume identical randomized
order streams; trades, final book composition, and portfolio states must
agree exactly. Any future optimization of the real engine must keep passing
this test unchanged.
"""

import random

import pytest

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import MarketConfig
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook

AGENTS = [f"ag_{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# The reference implementation (naive on purpose)
# ---------------------------------------------------------------------------
class ReferenceMarket:
    """Documented rules, implemented flat:

    1. buy crosses while limit >= best ask; sell while limit <= best bid
    2. same price -> arrival order (seq)
    3. execution at the *resting* order's price
    4. partial fills; non-marketable limit remainder rests
    5. market remainder discarded; reservations released
    """

    def __init__(self, tick_size, portfolios):
        self.tick = tick_size
        self.pf = portfolios
        self.bids = []   # dicts: agent, price, remaining, seq
        self.asks = []
        self.seq = 0
        self.trades = []

    def snap(self, price):
        return round(round(price / self.tick) * self.tick, 10)

    def _best(self, side):
        if not side:
            return None
        if side is self.bids:
            return max(side, key=lambda o: (o["price"], -o["seq"]))
        return min(side, key=lambda o: (o["price"], o["seq"]))

    def submit(self, agent, side, otype, price, qty):
        pf = self.pf[agent]
        if otype == "limit":
            price = self.snap(price)
            if price < self.tick:
                return
        # Validation mirror (streams are generated affordable, but mirror
        # the checks so rejects match too).
        if side == "sell" and pf.inventory - pf.reserved_inventory < qty:
            return
        if side == "buy" and otype == "limit":
            if pf.cash - pf.reserved_cash + 1e-9 < price * qty:
                return

        # Reserve.
        if side == "sell":
            pf.reserve_inventory(qty)
        elif otype == "limit":
            pf.reserve_cash(price * qty)

        remaining = qty
        opposite = self.asks if side == "buy" else self.bids
        while remaining > 0:
            best = self._best(opposite)
            if best is None:
                break
            if otype == "limit":
                if side == "buy" and price < best["price"] - 1e-12:
                    break
                if side == "sell" and price > best["price"] + 1e-12:
                    break
            fill = min(remaining, best["remaining"])
            if side == "buy" and otype == "market":
                afford = int((self.pf[agent].cash - self.pf[agent].reserved_cash)
                             // best["price"])
                if afford <= 0:
                    break
                fill = min(fill, afford)

            buyer, seller = (agent, best["agent"]) if side == "buy" else (best["agent"], agent)
            # Settlement, same op order as MatchingEngine._settle.
            if side == "buy":
                if otype == "limit":
                    self.pf[buyer].release_cash(price * fill)
                self.pf[seller].release_inventory(fill)
            else:
                self.pf[buyer].release_cash(best["price"] * fill)
                self.pf[seller].release_inventory(fill)
            self.pf[buyer].apply_buy(best["price"], fill)
            self.pf[seller].apply_sell(best["price"], fill)

            self.trades.append((round(best["price"], 10), fill, buyer, seller))
            remaining -= fill
            best["remaining"] -= fill
            if best["remaining"] == 0:
                opposite.remove(best)

        if remaining > 0:
            if otype == "limit":
                self.seq += 1
                book = self.bids if side == "buy" else self.asks
                book.append({"agent": agent, "price": price,
                             "remaining": remaining, "seq": self.seq})
            elif side == "sell":
                pf.release_inventory(remaining)

    def book_state(self):
        def norm(side, reverse):
            return sorted(((o["price"], o["seq"], o["agent"], o["remaining"])
                           for o in side),
                          key=lambda t: (-t[0] if reverse else t[0], t[1]))
        return norm(self.bids, True), norm(self.asks, False)


# ---------------------------------------------------------------------------
def _real_market(portfolios):
    book = OrderBook(0.05)
    me = MatchingEngine(book, portfolios, MarketConfig(max_order_size=500))
    return book, me


def _real_book_state(book):
    def norm(side, reverse):
        out = [(o.price, o.seq, o.agent_id, o.remaining)
               for o in side.all_orders()]
        return sorted(out, key=lambda t: (-t[0] if reverse else t[0], t[1]))
    return norm(book.bids, True), norm(book.asks, False)


@pytest.mark.parametrize("seed", [1, 7, 42, 99, 1234, 55555])
def test_reference_and_engine_agree_on_random_streams(seed):
    rng = random.Random(seed)
    pf_real = {a: Portfolio(cash=1_000_000.0, inventory=5_000) for a in AGENTS}
    pf_ref = {a: Portfolio(cash=1_000_000.0, inventory=5_000) for a in AGENTS}
    book, me = _real_market(pf_real)
    ref = ReferenceMarket(0.05, pf_ref)

    for i in range(400):
        agent = rng.choice(AGENTS)
        side = rng.choice(["buy", "sell"])
        otype = "market" if rng.random() < 0.2 else "limit"
        price = None if otype == "market" else round(rng.uniform(95, 105), 2)
        qty = rng.randint(1, 40)

        order = Order(order_id=f"o_{i}", agent_id=agent, side=side,
                      order_type=otype, price=price, quantity=qty, remaining=qty)
        trades, _ = me.submit(order, step=1)
        ref.submit(agent, side, otype, price, qty)

        # Trade-by-trade agreement (price, qty, counterparties).
        got = [(round(t.price, 10), t.quantity, t.buy_agent_id, t.sell_agent_id)
               for t in trades]
        assert got == ref.trades[len(ref.trades) - len(got):], f"order {i} diverged"

    # Final book composition identical (price, arrival order, agent, size).
    rb, ra = _real_book_state(book)
    fb, fa = ref.book_state()
    # seq counters differ in absolute value; compare everything but seq order-wise
    assert [(p, a, r) for p, _, a, r in rb] == [(p, a, r) for p, _, a, r in fb]
    assert [(p, a, r) for p, _, a, r in ra] == [(p, a, r) for p, _, a, r in fa]

    # Portfolio state bit-identical (same settlement ops in same order).
    for a in AGENTS:
        assert pf_real[a].cash == pf_ref[a].cash
        assert pf_real[a].inventory == pf_ref[a].inventory
        assert pf_real[a].realized_pnl == pf_ref[a].realized_pnl
