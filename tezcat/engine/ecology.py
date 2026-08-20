"""Ecology Engine: coordinates the full simulation step loop.

Step sequence (per step):
  1. advance clock, decay environment state
  2. fire due shocks (scheduled + manual)
  3. evaluate regime + expose modifiers
  4. expire stale resting orders
  5. build market view; agents observe + decide
  6. validate/submit orders; match trades; settle portfolios
  7. record market snapshot + step metrics
  8. check termination
"""

from __future__ import annotations

import hashlib
import itertools
import random
from typing import Any, Dict, List, Optional

from tezcat.agents.base import AgentRegistry, BaseAgent, MarketView, OrderIntent
from tezcat.agents.portfolio import Portfolio
from tezcat.agents import traders  # noqa: F401  (registers agent types)
from tezcat.core.config import (
    SCHEMA_VERSION, ExperimentConfig, Regime, ShockConfig, ShockType, canonical_json,
)
from tezcat.market.matching import MatchingEngine, Trade
from tezcat.market.order_book import Order, OrderBook
from tezcat.metrics.engine import MetricsEngine
from tezcat.regimes.engine import RegimeEngine
from tezcat.shocks.engine import EnvironmentState, ShockEngine

WHALE_ID = "whale"


class EcologyEngine:
    def __init__(self, run_id: str, config: ExperimentConfig, seed: int):
        self.run_id = run_id
        self.config = config
        self.seed = seed
        self.rng = random.Random(seed)
        self.step_num = 0
        self.done = False
        self.failed: Optional[str] = None

        mc = config.market
        self.book = OrderBook(mc.tick_size)
        self.portfolios: Dict[str, Portfolio] = {}
        self.agents: List[BaseAgent] = []
        self._order_counter = itertools.count(1)

        for gi, group in enumerate(config.agents):
            for i in range(group.count):
                agent = AgentRegistry.create(f"{group.agent_type.value}_{gi}_{i}", group)
                self.agents.append(agent)
                self.portfolios[agent.agent_id] = agent.portfolio

        # Exogenous whale participant used by whale-order shocks.
        whale_endowment = mc.initial_price * 1_000_000
        self.portfolios[WHALE_ID] = Portfolio(cash=whale_endowment, inventory=200_000)
        # Conservation targets frozen at construction (no fees/financing yet).
        self._expected_total_cash = sum(p.cash for p in self.portfolios.values())
        self._expected_total_inventory = sum(p.inventory for p in self.portfolios.values())

        self.env = EnvironmentState()
        self.matching = MatchingEngine(self.book, self.portfolios, mc)
        self.shocks = ShockEngine(run_id, config.shocks, self.env)
        self.regimes = RegimeEngine(run_id, config.regime_policy)
        self.metrics = MetricsEngine(run_id, config.metrics_policy, mc.initial_price)

        self.last_price = mc.initial_price
        self._last_log_return = 0.0
        self.trades: List[Dict[str, Any]] = []
        self.snapshots: List[Dict[str, Any]] = []
        self.regime_step_counts: Dict[str, int] = {r.value: 0 for r in Regime}
        self._agent_types: Dict[str, str] = {a.agent_id: a.agent_type for a in self.agents}
        self._agent_types[WHALE_ID] = "whale"

    # ------------------------------------------------------------------
    def _next_order_id(self) -> str:
        return f"ord_{next(self._order_counter):08d}"

    def _market_brief(self) -> Dict[str, Any]:
        return {
            "last_price": round(self.last_price, 4),
            "best_bid": self.book.best_bid,
            "best_ask": self.book.best_ask,
            "spread": self.book.spread,
            "bid_depth": self.book.bid_depth(),
            "ask_depth": self.book.ask_depth(),
        }

    # ------------------------------------------------------------------
    def _fire_shocks(self) -> None:
        for cfg, reason in self.shocks.due_shocks(self.step_num):
            before = self._market_brief()
            if cfg.shock_type == ShockType.WHALE_ORDER:
                payload = self.shocks.start_whale(cfg, self.step_num)
            else:
                payload = self.shocks.apply_env_shock(cfg, self.step_num)
            # Execute the first whale slice immediately so before/after differ.
            self._run_whale_slices()
            after = self._market_brief()
            self.shocks.record(cfg, self.step_num, reason, payload, before, after)

    def _run_whale_slices(self) -> None:
        """Execute the per-step quantity of each active whale program."""
        max_chunk = self.config.market.max_order_size
        for sl in self.shocks.whale_slices(self.step_num):
            remaining = sl["quantity"]
            filled = 0
            while remaining > 0:
                chunk = min(remaining, max_chunk)
                order = Order(
                    order_id=self._next_order_id(), agent_id=WHALE_ID, side=sl["side"],
                    order_type="market", price=None, quantity=chunk, remaining=chunk,
                    created_step=self.step_num,
                )
                trades, order = self.matching.submit(order, self.step_num)
                self._record_trades(trades)
                got = sum(t.quantity for t in trades)
                filled += got
                remaining -= chunk
                if got == 0:
                    break  # book exhausted; retry next step
            sl["program"]["remaining"] -= filled

    # ------------------------------------------------------------------
    def _record_trades(self, trades: List[Trade]) -> None:
        for t in trades:
            self.last_price = t.price
            self.trades.append({
                "trade_id": t.trade_id,
                "step": t.step,
                "price": round(t.price, 4),
                "quantity": t.quantity,
                "buy_agent_id": t.buy_agent_id,
                "sell_agent_id": t.sell_agent_id,
                "buy_agent_type": self._agent_types.get(t.buy_agent_id, "?"),
                "sell_agent_type": self._agent_types.get(t.sell_agent_id, "?"),
                "buy_order_id": t.buy_order_id,
                "sell_order_id": t.sell_order_id,
            })

    # ------------------------------------------------------------------
    def _expire_orders(self) -> None:
        for order in self.book.expired_orders(self.step_num, self.config.market.max_order_age):
            cancelled = self.book.cancel(order.order_id, self.step_num, status="expired")
            if cancelled is not None:
                self.matching.release_on_cancel(cancelled)

    # ------------------------------------------------------------------
    def _build_view(self) -> MarketView:
        bid_depth = self.book.bid_depth()
        ask_depth = self.book.ask_depth()
        total = bid_depth + ask_depth
        return MarketView(
            step=self.step_num,
            last_price=self.last_price,
            mid_price=self.book.mid_price(),
            best_bid=self.book.best_bid,
            best_ask=self.book.best_ask,
            spread=self.book.spread,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            recent_return=self.metrics.recent_return(),
            volatility=self.metrics.rolling_volatility,
            order_imbalance=(bid_depth - ask_depth) / total if total else 0.0,
            regime=self.regimes.current.value,
            modifiers=self.regimes.modifiers,
            sentiment=self.env.sentiment,
            mm_withdrawn=self.env.mm_withdrawn,
            tick_size=self.config.market.tick_size,
            fundamental_price=self.config.market.initial_price,
        )

    # ------------------------------------------------------------------
    def _process_intent(self, agent: BaseAgent, intent: OrderIntent, trades_this_step: List[int]) -> None:
        if intent.order_type == "cancel":
            if intent.cancel_order_id:
                cancelled = self.book.cancel(intent.cancel_order_id, self.step_num)
                if cancelled is not None:
                    self.matching.release_on_cancel(cancelled)
                if intent.cancel_order_id in agent.open_order_ids:
                    agent.open_order_ids.remove(intent.cancel_order_id)
            return
        if intent.side is None or intent.quantity <= 0:
            return
        qty = min(intent.quantity, self.config.market.max_order_size)
        order = Order(
            order_id=self._next_order_id(), agent_id=agent.agent_id, side=intent.side,
            order_type=intent.order_type,
            price=intent.price if intent.order_type == "limit" else None,
            quantity=qty, remaining=qty, created_step=self.step_num,
        )
        trades, order = self.matching.submit(order, self.step_num)
        self._record_trades(trades)
        trades_this_step.append(sum(t.quantity for t in trades))
        if order.status in ("new", "partial") and order.remaining > 0 and self.book.get_order(order.order_id):
            agent.open_order_ids.append(order.order_id)

    # ------------------------------------------------------------------
    def step(self) -> Dict[str, Any]:
        """Advance one simulation step. Returns the market snapshot."""
        if self.done:
            raise RuntimeError("run already finished")
        self.step_num += 1
        step = self.step_num
        trades_before = len(self.trades)

        # 1-2. environment decay + shocks (incl. ongoing whale programs)
        self.env.tick(step)
        self._fire_shocks()
        self._run_whale_slices()

        # 3. regime evaluation (uses last step's metrics)
        self.regimes.observe(
            step, self.last_price, self.book.spread,
            self.book.bid_depth() + self.book.ask_depth(),
            self.metrics.rolling_volatility,
        )
        self.regime_step_counts[self.regimes.current.value] += 1

        # 4. expire stale orders
        self._expire_orders()

        # 5-6. agents observe, decide, submit
        view = self._build_view()
        order_agents = list(self.agents)
        self.rng.shuffle(order_agents)
        fills: List[int] = []
        for agent in order_agents:
            # prune stale open-order references
            agent.open_order_ids = [oid for oid in agent.open_order_ids
                                    if self.book.get_order(oid) is not None][-20:]
            agent.observe(view, self._last_log_return)
            for intent in agent.decide(view, self.rng):
                self._process_intent(agent, intent, fills)

        # 7. snapshot + metrics
        step_trades = self.trades[trades_before:]
        volume = sum(t["quantity"] for t in step_trades)
        row = self.metrics.on_step(
            step, self.last_price, self.book.spread,
            self.book.bid_depth(), self.book.ask_depth(),
            volume, len(step_trades), self.regimes.current.value,
        )
        self._last_log_return = row["log_return"]
        snapshot = {
            "run_id": self.run_id,
            "step": step,
            "last_price": round(self.last_price, 4),
            "best_bid": self.book.best_bid,
            "best_ask": self.book.best_ask,
            "mid_price": self.book.mid_price(),
            "spread": self.book.spread,
            "bid_depth": self.book.bid_depth(),
            "ask_depth": self.book.ask_depth(),
            "volume": volume,
            "order_imbalance": row["order_imbalance"],
            "regime": self.regimes.current.value,
        }
        self.snapshots.append(snapshot)

        # 8. termination
        if step >= self.config.total_steps:
            self.done = True
        return snapshot

    # ------------------------------------------------------------------
    def build_report(self) -> Dict[str, Any]:
        return self.metrics.build_report(
            self.run_id, self.agents, self.last_price,
            shock_count=len(self.shocks.events),
            regime_events=self.regimes.events,
            regime_step_counts=self.regime_step_counts,
        )

    # ------------------------------------------------------------------
    def check_invariants(self) -> None:
        """Economic invariants; raises AssertionError on violation.

        Checks, in order: inventory conservation, cash conservation (no fees
        or financing exist, so total cash is constant), per-agent resource
        floors, reservation bounds, and exact reconciliation of reservations
        against the open orders resting in the book.
        """
        from tezcat.agents.portfolio import INVARIANT_EPS

        mc = self.config.market
        total_inventory = sum(p.inventory for p in self.portfolios.values())
        assert total_inventory == self._expected_total_inventory, (
            f"asset not conserved: {total_inventory} != {self._expected_total_inventory}")

        total_cash = sum(p.cash for p in self.portfolios.values())
        # Float add/sub error can accumulate over many settlements; allow a
        # small absolute drift far below any economically meaningful amount.
        assert abs(total_cash - self._expected_total_cash) <= max(1.0, 1e-9 * self._expected_total_cash), (
            f"cash not conserved: {total_cash} != {self._expected_total_cash}")

        # Reservations must reconcile exactly to the resting open orders.
        expected_res_cash: Dict[str, float] = {aid: 0.0 for aid in self.portfolios}
        expected_res_inv: Dict[str, int] = {aid: 0 for aid in self.portfolios}
        for side in (self.book.bids, self.book.asks):
            for order in side.all_orders():
                if order.side == "buy":
                    expected_res_cash[order.agent_id] += (order.price or 0.0) * order.remaining
                else:
                    expected_res_inv[order.agent_id] += order.remaining

        for aid, p in self.portfolios.items():
            if not mc.allow_negative_cash:
                assert p.cash >= -INVARIANT_EPS, f"negative cash for {aid}: {p.cash}"
            if not mc.allow_short:
                assert p.inventory >= 0, f"negative inventory for {aid}: {p.inventory}"
            assert p.reserved_cash <= p.cash + INVARIANT_EPS, f"over-reserved cash for {aid}"
            assert p.reserved_inventory <= p.inventory, f"over-reserved inventory for {aid}"
            assert abs(p.reserved_cash - expected_res_cash[aid]) <= INVARIANT_EPS, (
                f"reserved cash for {aid} ({p.reserved_cash}) does not reconcile "
                f"to open buy orders ({expected_res_cash[aid]})")
            assert p.reserved_inventory == expected_res_inv[aid], (
                f"reserved inventory for {aid} ({p.reserved_inventory}) does not "
                f"reconcile to open sell orders ({expected_res_inv[aid]})")

    # ------------------------------------------------------------------
    # Deterministic identity (Phase F2)
    # ------------------------------------------------------------------
    def state_hash(self) -> str:
        """Canonical hash of the semantic kernel state.

        Covers the book (per-level FIFO order queues), all portfolios,
        environment state, regime, step counter, and last price. Floats are
        encoded with ``repr`` (shortest exact round-trip form), so two states
        hash equal iff they are bit-identical, and the hash is stable across
        fresh processes.
        """
        def side_state(side):
            ticks = side.sorted_ticks
            return [
                [t, [[o.order_id, o.agent_id, o.remaining, o.seq, o.status]
                     for o in side.levels[t]]]
                for t in ticks
            ]

        payload = {
            "schema_version": SCHEMA_VERSION,
            "step": self.step_num,
            "last_price": repr(self.last_price),
            "book": {"bids": side_state(self.book.bids), "asks": side_state(self.book.asks)},
            "portfolios": {
                aid: [repr(p.cash), p.inventory, repr(p.reserved_cash),
                      p.reserved_inventory, repr(p.realized_pnl), repr(p.avg_cost)]
                for aid, p in sorted(self.portfolios.items())
            },
            "env": {"sentiment": repr(self.env.sentiment), "mm_withdrawn": self.env.mm_withdrawn},
            "regime": self.regimes.current.value,
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def event_hash(self) -> str:
        """Canonical hash of the recorded event/output sequence.

        Covers trades, snapshots, shock events, and regime events in order.
        Two runs with the same config, schema, and seed must produce the same
        event hash; any divergence is a determinism failure.
        """
        payload = {
            "schema_version": SCHEMA_VERSION,
            "trades": self.trades,
            "snapshots": self.snapshots,
            "shock_events": [e.to_dict() for e in self.shocks.events],
            "regime_events": [e.to_dict() for e in self.regimes.events],
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
