"""Risk engine: margin gates and the liquidation process (Phase F8).

Two enforcement surfaces:

1. **Order admission** (`check_order`, `max_buy_qty`) — an initial-margin
   gate replaces the strict cash check when risk is enabled. Buys are
   admitted only if, at worst-case fill, ``equity ≥ initial_margin ×
   (position + committed + new notional)`` where *committed* is cash already
   reserved for resting buys. Sells always reduce exposure and pass.

2. **The liquidation process** (`evaluate`) — once per step, every margin
   account is marked. A maintenance breach emits a ``margin_call`` event and
   starts a clock; after ``liquidation_delay`` steps of continuous breach,
   the engine emits a ``liquidation`` event and returns forced sell
   quantities, which the ecology submits as *real market orders through the
   matching engine* — forced flow consumes liquidity, moves price, and
   appears in the event log like any other flow. Recovery above maintenance
   emits ``margin_restored``. An agent with zero inventory and negative cash
   emits ``default`` once; the debt stays on the books (cash transfers are
   zero-sum, so conservation is unaffected).

No randomness anywhere: the risk path is deterministic by construction.
The whale participant is exogenous and exempt.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import RiskPolicy
from tezcat.events.log import EventLog

WHALE_ID = "whale"


class RiskEngine:
    def __init__(self, policy: RiskPolicy, portfolios: Dict[str, Portfolio],
                 events: Optional[EventLog] = None):
        self.policy = policy
        self.portfolios = portfolios
        self.events = events
        self.mark_price: float = 0.0
        # Per-agent margin state.
        self._breach_since: Dict[str, int] = {}
        self._defaulted: set = set()
        # Counters for the risk report (reconciled against events in tests).
        self.margin_call_count = 0
        self.liquidation_slice_count = 0
        self.forced_volume = 0
        self.default_count = 0
        self.max_leverage_observed = 0.0
        self.min_margin_ratio = math.inf

    # ------------------------------------------------------------------
    def _emit(self, step: int, type_: str, data: Dict[str, Any]) -> None:
        if self.events is not None:
            self.events.append(step, type_, data)

    @staticmethod
    def equity(pf: Portfolio, mark: float) -> float:
        return pf.cash + pf.inventory * mark

    @staticmethod
    def position_value(pf: Portfolio, mark: float) -> float:
        return pf.inventory * mark

    def margin_ratio(self, pf: Portfolio, mark: float) -> Optional[float]:
        pos = self.position_value(pf, mark)
        if pos <= 0:
            return None
        return self.equity(pf, mark) / pos

    def leverage(self, pf: Portfolio, mark: float) -> Optional[float]:
        eq = self.equity(pf, mark)
        if eq <= 0:
            return None  # insolvent: leverage undefined, not infinite-printed
        return self.position_value(pf, mark) / eq

    # ------------------------------------------------------------------
    # Order admission
    # ------------------------------------------------------------------
    def check_order(self, order, ref_price: Optional[float]) -> Optional[str]:
        """Initial-margin gate for buys. Returns a rejection reason or None."""
        if order.side != "buy" or order.agent_id == WHALE_ID:
            return None
        pf = self.portfolios.get(order.agent_id)
        if pf is None:
            return None
        price = order.price if order.price is not None else ref_price
        if price is None or price <= 0:
            return None  # no reference: matching will cap during execution
        if order.order_type == "limit":
            # Full-quantity gate: a resting order commits its whole notional.
            mark = self.mark_price or price
            eq = self.equity(pf, mark)
            exposure = (self.position_value(pf, mark) + pf.reserved_cash
                        + price * order.quantity)
            if eq < self.policy.initial_margin * exposure:
                return "initial margin exceeded"
        else:
            # Market buys are capped during matching (mirrors the legacy
            # cash-cap semantics); reject only when there is no headroom
            # for even a single share.
            if self.max_buy_qty(pf, price) < 1:
                return "initial margin exceeded"
        return None

    def market_buy_cap(self, order, price: float) -> Optional[int]:
        """Per-fill cap for market buys under the initial margin.

        None means "no cap" (exempt participant); the matching engine treats
        0 as "stop filling".
        """
        if order.agent_id == WHALE_ID:
            return None
        pf = self.portfolios.get(order.agent_id)
        if pf is None:
            return None
        return self.max_buy_qty(pf, price)

    def max_buy_qty(self, pf: Portfolio, price: float) -> int:
        """Largest additional buy quantity the initial margin permits."""
        if price <= 0:
            return 0
        mark = self.mark_price or price
        eq = self.equity(pf, mark)
        headroom = (eq / self.policy.initial_margin
                    - self.position_value(pf, mark) - pf.reserved_cash)
        return max(0, int(headroom // price))

    # ------------------------------------------------------------------
    # Mark-to-market and the liquidation clock
    # ------------------------------------------------------------------
    def evaluate(self, step: int, mark: float) -> List[Tuple[str, int]]:
        """Mark all accounts; emit margin events; return forced sells.

        Returns [(agent_id, quantity)] liquidation slices due this step.
        """
        self.mark_price = mark
        forced: List[Tuple[str, int]] = []
        p = self.policy
        for aid, pf in sorted(self.portfolios.items()):
            if aid == WHALE_ID:
                continue
            pos = self.position_value(pf, mark)
            eq = self.equity(pf, mark)

            lev = self.leverage(pf, mark)
            if lev is not None:
                self.max_leverage_observed = max(self.max_leverage_observed, lev)

            if pos <= 0:
                if pf.cash < 0 and aid not in self._defaulted:
                    self._defaulted.add(aid)
                    self.default_count += 1
                    self._emit(step, "default", {
                        "agent_id": aid, "debt": pf.cash})
                self._breach_since.pop(aid, None)
                continue

            ratio = eq / pos
            self.min_margin_ratio = min(self.min_margin_ratio, ratio)

            if ratio < p.maintenance_margin:
                if aid not in self._breach_since:
                    self._breach_since[aid] = step
                    self.margin_call_count += 1
                    self._emit(step, "margin_call", {
                        "agent_id": aid, "equity": eq, "position_value": pos,
                        "margin_ratio": ratio,
                        "threshold": p.maintenance_margin})
                if step - self._breach_since[aid] >= p.liquidation_delay:
                    qty = max(1, int(pf.inventory * p.liquidation_fraction))
                    qty = min(qty, pf.available_inventory)
                    if qty > 0:
                        self.liquidation_slice_count += 1
                        self._emit(step, "liquidation", {
                            "agent_id": aid, "quantity": qty,
                            "reason": "maintenance_margin_breach",
                            "margin_ratio": ratio, "equity": eq,
                            "order_type": p.liquidation_order_type})
                        forced.append((aid, qty))
            elif aid in self._breach_since:
                del self._breach_since[aid]
                self._emit(step, "margin_restored", {
                    "agent_id": aid, "margin_ratio": ratio})
        return forced

    def record_forced_fill(self, quantity: int) -> None:
        self.forced_volume += quantity

    # ------------------------------------------------------------------
    def report(self) -> Dict[str, Any]:
        """Risk block for the run report; keys are flat for use as batch DVs."""
        return {
            "risk_margin_calls": self.margin_call_count,
            "risk_liquidation_slices": self.liquidation_slice_count,
            "risk_forced_volume": self.forced_volume,
            "risk_defaults": self.default_count,
            "risk_max_leverage": round(self.max_leverage_observed, 4),
            "risk_min_margin_ratio": (round(self.min_margin_ratio, 4)
                                      if self.min_margin_ratio != math.inf else None),
        }

    # -- checkpoint support --------------------------------------------
    def state_dict(self) -> Dict[str, Any]:
        return {
            "mark_price": self.mark_price,
            "breach_since": dict(self._breach_since),
            "defaulted": sorted(self._defaulted),
            "margin_call_count": self.margin_call_count,
            "liquidation_slice_count": self.liquidation_slice_count,
            "forced_volume": self.forced_volume,
            "default_count": self.default_count,
            "max_leverage_observed": self.max_leverage_observed,
            "min_margin_ratio": (self.min_margin_ratio
                                 if self.min_margin_ratio != math.inf else None),
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        self.mark_price = state["mark_price"]
        self._breach_since = dict(state["breach_since"])
        self._defaulted = set(state["defaulted"])
        self.margin_call_count = state["margin_call_count"]
        self.liquidation_slice_count = state["liquidation_slice_count"]
        self.forced_volume = state["forced_volume"]
        self.default_count = state["default_count"]
        self.max_leverage_observed = state["max_leverage_observed"]
        self.min_margin_ratio = (state["min_margin_ratio"]
                                 if state["min_margin_ratio"] is not None else math.inf)
