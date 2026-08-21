"""Event replay kernel (Phase F3).

Rebuilds market state (order book + all portfolios + environment) purely from
the canonical event log — no agent decisions, no RNG. If the event schema is
complete, the replayed state hash equals the live engine's state hash bit for
bit. This is the executable proof that the log captures every financially
meaningful transition.

Replay applies the *same* float operations in the *same* order as the
matching engine's settlement path, so portfolio floats reproduce exactly.

Duplicate/ordering policy: events must arrive with strictly sequential
``seq`` starting at 1; anything else raises ``ReplayError``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import ExperimentConfig
from tezcat.core.state_hash import compute_state_hash
from tezcat.events.log import ReplayError
from tezcat.market.order_book import Order, OrderBook

WHALE_ID = "whale"
WHALE_INVENTORY = 200_000


def initial_portfolios(config: ExperimentConfig) -> Dict[str, Portfolio]:
    """The engine's initial portfolio allocation, reproduced independently.

    Must mirror EcologyEngine.__init__ (agent naming, endowments, whale).
    Guarded by the replay state-hash tests: any drift between the two
    constructions fails replay immediately.
    """
    portfolios: Dict[str, Portfolio] = {}
    for gi, group in enumerate(config.agents):
        for i in range(group.count):
            aid = f"{group.agent_type.value}_{gi}_{i}"
            portfolios[aid] = Portfolio(cash=group.cash, inventory=group.inventory)
    portfolios[WHALE_ID] = Portfolio(
        cash=config.market.initial_price * 1_000_000, inventory=WHALE_INVENTORY)
    return portfolios


class ReplayKernel:
    """Consumes an event stream and reconstructs market state."""

    def __init__(self, config: ExperimentConfig, run_id: str):
        self.config = config
        self.run_id = run_id
        self.book = OrderBook(config.market.tick_size)
        self.portfolios = initial_portfolios(config)
        self.step = 0
        self.last_price = config.market.initial_price
        self.sentiment = 0.0
        self.mm_withdrawn = False
        self.regime = "stable"
        self._live: Dict[str, Order] = {}   # accepted orders not yet terminal
        self._next_seq = 1
        self.trades_applied = 0

    # ------------------------------------------------------------------
    def apply_all(self, events: Iterable[Dict[str, Any]]) -> None:
        for ev in events:
            self.apply(ev)

    def apply(self, event: Dict[str, Any]) -> None:
        if event["seq"] != self._next_seq:
            raise ReplayError(
                f"event sequence violation: expected seq {self._next_seq}, "
                f"got {event['seq']} (duplicate or missing event)")
        self._next_seq += 1

        handler = getattr(self, f"_on_{event['type']}", None)
        if handler is None:
            raise ReplayError(f"unknown event type {event['type']!r}")
        handler(event["step"], event["data"])

    # -- order lifecycle ------------------------------------------------
    def _on_order_accepted(self, step: int, d: Dict[str, Any]) -> None:
        order = Order(order_id=d["order_id"], agent_id=d["agent_id"],
                      side=d["side"], order_type=d["order_type"],
                      price=d["price"], quantity=d["quantity"],
                      remaining=d["quantity"], created_step=step)
        pf = self.portfolios[order.agent_id]
        # Same reservation ops, same order, as MatchingEngine.submit.
        if order.side == "sell":
            pf.reserve_inventory(order.remaining)
        elif order.order_type == "limit":
            pf.reserve_cash(order.price * order.remaining)
        self._live[order.order_id] = order

    def _on_order_rejected(self, step: int, d: Dict[str, Any]) -> None:
        pass  # no state change by definition

    def _on_trade(self, step: int, d: Dict[str, Any]) -> None:
        buy = self._order_ref(d["buy_order_id"])
        sell = self._order_ref(d["sell_order_id"])
        price, qty = d["price"], d["quantity"]
        buyer = self.portfolios[buy.agent_id]
        seller = self.portfolios[sell.agent_id]

        # Mirror MatchingEngine._settle exactly (op order matters for floats).
        if buy.order_type == "limit":
            buyer.release_cash((buy.price or price) * qty)
        seller.release_inventory(qty)
        buyer.apply_buy(price, qty)
        seller.apply_sell(price, qty)

        for o in (buy, sell):
            o.remaining -= qty
            o.updated_step = step

        # Exactly one side is resting in the book; the other is incoming.
        buy_resting = self.book.get_order(buy.order_id) is buy
        sell_resting = self.book.get_order(sell.order_id) is sell
        if buy_resting == sell_resting:
            raise ReplayError(
                f"trade {d['trade_id']} does not have exactly one resting side")
        resting = buy if buy_resting else sell
        incoming = sell if buy_resting else buy
        if resting.remaining == 0:
            self.book.cancel(resting.order_id, step, status="filled")
            self._live.pop(resting.order_id, None)
        else:
            resting.status = "partial"
        if incoming.remaining == 0:
            self._live.pop(incoming.order_id, None)
        self.last_price = price
        self.trades_applied += 1

    def _on_order_rested(self, step: int, d: Dict[str, Any]) -> None:
        order = self._order_ref(d["order_id"])
        order.status = d["status"]
        order.remaining = d["remaining"]
        order.created_step = step
        order.updated_step = step
        order.seq = d["seq"]
        self.book.add_limit(order)

    def _on_order_discarded(self, step: int, d: Dict[str, Any]) -> None:
        order = self._live.pop(d["order_id"], None)
        if order is not None and order.side == "sell":
            self.portfolios[order.agent_id].release_inventory(d["remaining"])

    def _on_order_cancelled(self, step: int, d: Dict[str, Any]) -> None:
        self._remove_resting(d["order_id"], step, "cancelled")

    def _on_order_expired(self, step: int, d: Dict[str, Any]) -> None:
        self._remove_resting(d["order_id"], step, "expired")

    def _remove_resting(self, order_id: str, step: int, status: str) -> None:
        order = self.book.cancel(order_id, step, status=status)
        if order is None:
            raise ReplayError(f"{status} event for unknown order {order_id!r}")
        self._live.pop(order_id, None)
        pf = self.portfolios[order.agent_id]
        # Mirror MatchingEngine.release_on_cancel.
        if order.side == "sell":
            pf.release_inventory(order.remaining)
        else:
            pf.release_cash((order.price or 0.0) * order.remaining)

    # -- environment / narration ---------------------------------------
    def _on_step_ended(self, step: int, d: Dict[str, Any]) -> None:
        self.step = step
        self.last_price = d["last_price"]
        self.sentiment = d["sentiment"]
        self.mm_withdrawn = d["mm_withdrawn"]
        self.regime = d["regime"]

    def _on_shock(self, step: int, d: Dict[str, Any]) -> None:
        pass  # forensic record; environment effects arrive via step_ended

    def _on_regime_transition(self, step: int, d: Dict[str, Any]) -> None:
        pass  # forensic record; regime state arrives via step_ended

    def _on_intervention(self, step: int, d: Dict[str, Any]) -> None:
        pass  # lineage record

    # Risk events (Phase F8) are forensic records: the forced orders they
    # announce arrive as ordinary order/trade events with full semantics.
    def _on_margin_call(self, step: int, d: Dict[str, Any]) -> None:
        pass

    def _on_margin_restored(self, step: int, d: Dict[str, Any]) -> None:
        pass

    def _on_liquidation(self, step: int, d: Dict[str, Any]) -> None:
        pass

    def _on_default(self, step: int, d: Dict[str, Any]) -> None:
        pass

    # ------------------------------------------------------------------
    def _order_ref(self, order_id: str) -> Order:
        order = self._live.get(order_id) or self.book.get_order(order_id)
        if order is None:
            raise ReplayError(f"trade references unknown order {order_id!r}")
        return order

    # ------------------------------------------------------------------
    def state_hash(self) -> str:
        """Same definition as EcologyEngine.state_hash."""
        return compute_state_hash(
            self.book, self.portfolios, self.step, self.last_price,
            self.sentiment, self.mm_withdrawn, self.regime)
