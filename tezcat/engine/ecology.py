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
import random
from typing import Any, Dict, List, Optional

from tezcat.agents.base import AgentRegistry, BaseAgent, MarketView, OrderIntent
from tezcat.agents.portfolio import Portfolio
from tezcat.agents import traders  # noqa: F401  (registers agent types)
from tezcat.core.config import (
    SCHEMA_VERSION, ExperimentConfig, Regime, ShockConfig, ShockType, canonical_json,
)
from tezcat.core.config import config_hash as _config_hash
from tezcat.core.state_hash import compute_state_hash
from tezcat.events.log import EventLog
from tezcat.market.matching import MatchingEngine, Trade
from tezcat.market.order_book import Order, OrderBook
from tezcat.metrics.engine import MetricsEngine
from tezcat.regimes.engine import RegimeEngine
from tezcat.shocks.engine import EnvironmentState, ShockEngine

WHALE_ID = "whale"

#: Bump on any change to the checkpoint layout.
CHECKPOINT_VERSION = 1


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
        # Run-scoped plain-int counter: deterministic and checkpointable.
        self._order_seq = 0
        self.events_log = EventLog(run_id)

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
        # Risk engine (Phase F8): only constructed when enabled, so legacy
        # configs keep strict cash semantics and the frozen baseline.
        if config.risk.enabled:
            from tezcat.risk.engine import RiskEngine
            self.risk: Optional["RiskEngine"] = RiskEngine(
                config.risk, self.portfolios, events=self.events_log)
            self.risk.mark_price = mc.initial_price
        else:
            self.risk = None
        self.matching = MatchingEngine(self.book, self.portfolios, mc,
                                       events=self.events_log, risk=self.risk)
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
        self._order_seq += 1
        return f"ord_{self._order_seq:08d}"

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
            ev = self.shocks.record(cfg, self.step_num, reason, payload, before, after)
            self.events_log.append(self.step_num, "shock", ev.to_dict())

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
    def _run_liquidations(self) -> None:
        """Execute forced sells due this step. Forced flow is real order
        flow: it goes through validation and matching, consumes liquidity,
        and appears in the event log like any other order."""
        max_chunk = self.config.market.max_order_size
        for agent_id, quantity in self.risk.evaluate(self.step_num, self.last_price):
            remaining = quantity
            while remaining > 0:
                chunk = min(remaining, max_chunk)
                order = Order(
                    order_id=self._next_order_id(), agent_id=agent_id,
                    side="sell", order_type="market", price=None,
                    quantity=chunk, remaining=chunk, created_step=self.step_num,
                )
                trades, order = self.matching.submit(order, self.step_num)
                self._record_trades(trades)
                filled = sum(t.quantity for t in trades)
                self.risk.record_forced_fill(filled)
                remaining -= chunk
                if filled == 0:
                    break  # book exhausted; the breach clock keeps running

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
                self.events_log.append(self.step_num, "order_expired", {
                    "order_id": cancelled.order_id, "side": cancelled.side,
                    "price": cancelled.price, "remaining": cancelled.remaining})

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
                    self.events_log.append(self.step_num, "order_cancelled", {
                        "order_id": cancelled.order_id, "side": cancelled.side,
                        "price": cancelled.price, "remaining": cancelled.remaining,
                        "reason": "agent"})
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

        # 2b. margin evaluation + forced liquidation flow (risk enabled only)
        if self.risk is not None:
            self._run_liquidations()

        # 3. regime evaluation (uses last step's metrics)
        n_regime_events = len(self.regimes.events)
        self.regimes.observe(
            step, self.last_price, self.book.spread,
            self.book.bid_depth() + self.book.ask_depth(),
            self.metrics.rolling_volatility,
        )
        for ev in self.regimes.events[n_regime_events:]:
            self.events_log.append(step, "regime_transition", ev.to_dict())
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
        # End-of-step environment record: lets event replay reconstruct
        # non-book state (env decay, regime) without re-simulating agents.
        self.events_log.append(step, "step_ended", {
            "last_price": self.last_price, "sentiment": self.env.sentiment,
            "mm_withdrawn": self.env.mm_withdrawn,
            "regime": self.regimes.current.value})

        # 8. termination
        if step >= self.config.total_steps:
            self.done = True
        return snapshot

    # ------------------------------------------------------------------
    def build_report(self) -> Dict[str, Any]:
        report = self.metrics.build_report(
            self.run_id, self.agents, self.last_price,
            shock_count=len(self.shocks.events),
            regime_events=self.regimes.events,
            regime_step_counts=self.regime_step_counts,
        )
        if self.risk is not None:
            # Flat risk_* keys so batch designs can declare them as
            # dependent variables; absent entirely when risk is disabled.
            report.update(self.risk.report())
        return report

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

        margin_on = self.risk is not None
        for aid, p in self.portfolios.items():
            if not mc.allow_negative_cash and not margin_on:
                # Under margin (risk enabled), negative cash IS borrowing —
                # bounded by the margin gates, not by a hard floor.
                assert p.cash >= -INVARIANT_EPS, f"negative cash for {aid}: {p.cash}"
            if not mc.allow_short:
                assert p.inventory >= 0, f"negative inventory for {aid}: {p.inventory}"
            if not margin_on:
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
        fresh processes. Shared with the event-replay kernel
        (tezcat.core.state_hash) so replay is checked against the same
        definition.
        """
        return compute_state_hash(
            self.book, self.portfolios, self.step_num, self.last_price,
            self.env.sentiment, self.env.mm_withdrawn, self.regimes.current.value)

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

    # ------------------------------------------------------------------
    # Checkpoints and forks (Phase F3)
    # ------------------------------------------------------------------
    def checkpoint(self) -> Dict[str, Any]:
        """Serializable snapshot of the complete kernel state.

        Checkpoints are inter-step: call between step() invocations. The
        returned dict is strictly JSON-native and round-trips bit-identically
        (Python floats survive JSON exactly). Restoring and continuing must
        reproduce the uninterrupted run's hashes — enforced by tests.
        """
        def order_state(o: Order) -> Dict[str, Any]:
            return {"order_id": o.order_id, "agent_id": o.agent_id, "side": o.side,
                    "order_type": o.order_type, "price": o.price,
                    "quantity": o.quantity, "remaining": o.remaining,
                    "status": o.status, "created_step": o.created_step,
                    "updated_step": o.updated_step, "seq": o.seq}

        def portfolio_state(p: Portfolio) -> Dict[str, Any]:
            return {"cash": p.cash, "inventory": p.inventory,
                    "reserved_cash": p.reserved_cash,
                    "reserved_inventory": p.reserved_inventory,
                    "realized_pnl": p.realized_pnl, "avg_cost": p.avg_cost,
                    "initial_cash": p.initial_cash,
                    "initial_inventory": p.initial_inventory}

        rng_state = self.rng.getstate()
        return {
            "checkpoint_version": CHECKPOINT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "config_hash": _config_hash(self.config),
            "seed": self.seed,
            "step": self.step_num,
            "done": self.done,
            "failed": self.failed,
            "rng_state": [rng_state[0], list(rng_state[1]), rng_state[2]],
            "order_seq": self._order_seq,
            "trade_seq": self.matching._trade_seq,
            "book_seq": self.book._seq,
            "last_price": self.last_price,
            "last_log_return": self._last_log_return,
            "book": {
                "bids": [order_state(o) for o in self.book.bids.all_orders()],
                "asks": [order_state(o) for o in self.book.asks.all_orders()],
            },
            "portfolios": {aid: portfolio_state(p)
                           for aid, p in sorted(self.portfolios.items())},
            "agents": [{
                "agent_id": a.agent_id,
                "memory": {k: getattr(a.memory, k) for k in (
                    "confidence", "fear", "recent_loss", "trend_belief",
                    "value_belief", "volatility_estimate", "last_equity")},
                "open_order_ids": list(a.open_order_ids),
            } for a in self.agents],
            "env": {"sentiment": self.env.sentiment,
                    "mm_withdrawn": self.env.mm_withdrawn,
                    "sentiment_decay": self.env._sentiment_decay,
                    "mm_withdraw_until": self.env._mm_withdraw_until,
                    "sentiment_until": self.env._sentiment_until},
            "shocks": self.shocks.state_dict(),
            "regimes": self.regimes.state_dict(),
            "metrics": self.metrics.state_dict(),
            "risk": self.risk.state_dict() if self.risk is not None else None,
            "events_log": self.events_log.state_dict(),
            "trades": self.trades,
            "snapshots": self.snapshots,
            "regime_step_counts": dict(self.regime_step_counts),
            "expected_total_cash": self._expected_total_cash,
            "expected_total_inventory": self._expected_total_inventory,
        }

    @classmethod
    def restore(cls, checkpoint: Dict[str, Any], config: ExperimentConfig,
                run_id: Optional[str] = None) -> "EcologyEngine":
        """Rebuild an engine from a checkpoint.

        ``config`` must hash-match the checkpoint's recorded config; a fork
        keeps the parent's config but takes a new ``run_id``.
        """
        if checkpoint.get("checkpoint_version") != CHECKPOINT_VERSION:
            raise ValueError(
                f"incompatible checkpoint version "
                f"{checkpoint.get('checkpoint_version')} (runtime implements "
                f"{CHECKPOINT_VERSION})")
        if _config_hash(config) != checkpoint["config_hash"]:
            raise ValueError(
                "config does not match checkpoint config_hash; restoring under "
                "a different config is undefined — create a new experiment")

        eng = cls(run_id or checkpoint["run_id"], config, checkpoint["seed"])
        eng.step_num = checkpoint["step"]
        eng.done = checkpoint["done"]
        eng.failed = checkpoint["failed"]
        rs = checkpoint["rng_state"]
        eng.rng.setstate((rs[0], tuple(rs[1]), rs[2]))
        eng._order_seq = checkpoint["order_seq"]
        eng.matching._trade_seq = checkpoint["trade_seq"]
        eng.last_price = checkpoint["last_price"]
        eng._last_log_return = checkpoint["last_log_return"]
        eng._expected_total_cash = checkpoint["expected_total_cash"]
        eng._expected_total_inventory = checkpoint["expected_total_inventory"]

        # Portfolios: mutate in place (agents share these exact objects).
        for aid, ps in checkpoint["portfolios"].items():
            p = eng.portfolios[aid]
            for k, v in ps.items():
                setattr(p, k, v)

        # Book: re-insert resting orders preserving both price-level FIFO
        # order (within each level) and *arrival order* in the order-ID map —
        # expiry iterates that map, so its insertion order is semantic.
        restored_orders = []
        for side_name, side in (("bids", eng.book.bids), ("asks", eng.book.asks)):
            for os_ in checkpoint["book"][side_name]:
                order = Order(**os_)
                tick = eng.book.to_tick(order.price)
                side.add(tick, order)
                restored_orders.append((order, tick))
        restored_orders.sort(key=lambda ot: ot[0].seq)  # seq == arrival order
        eng.book._orders = {o.order_id: (o, t) for o, t in restored_orders}
        eng.book._seq = checkpoint["book_seq"]

        # Agents: restore memory and open-order references by id.
        agent_state = {a["agent_id"]: a for a in checkpoint["agents"]}
        for a in eng.agents:
            st = agent_state[a.agent_id]
            for k, v in st["memory"].items():
                setattr(a.memory, k, v)
            a.open_order_ids = list(st["open_order_ids"])

        env = checkpoint["env"]
        eng.env.sentiment = env["sentiment"]
        eng.env.mm_withdrawn = env["mm_withdrawn"]
        eng.env._sentiment_decay = env["sentiment_decay"]
        eng.env._mm_withdraw_until = env["mm_withdraw_until"]
        eng.env._sentiment_until = env["sentiment_until"]

        eng.shocks.load_state(checkpoint["shocks"])
        eng.regimes.load_state(checkpoint["regimes"])
        eng.metrics.load_state(checkpoint["metrics"])
        if checkpoint.get("risk") is not None:
            if eng.risk is None:
                raise ValueError("checkpoint carries risk state but the "
                                 "config has risk disabled")
            eng.risk.load_state(checkpoint["risk"])
        eng.events_log.load_state(checkpoint["events_log"])
        eng.trades = list(checkpoint["trades"])
        eng.snapshots = list(checkpoint["snapshots"])
        eng.regime_step_counts = dict(checkpoint["regime_step_counts"])

        # New identity for forked children; sub-engines emit under it.
        eng.shocks.run_id = eng.run_id
        eng.regimes.run_id = eng.run_id
        eng.metrics.run_id = eng.run_id
        eng.events_log.run_id = eng.run_id
        return eng

    @classmethod
    def fork(cls, checkpoint: Dict[str, Any], config: ExperimentConfig,
             new_run_id: str, intervention: Optional[Dict[str, Any]] = None,
             ) -> "EcologyEngine":
        """Create a child run from a parent checkpoint with a declared
        intervention.

        The child shares the parent's exact event prefix (the restored event
        chain continues from the parent's chain head) and diverges only
        through the recorded intervention. Supported operations:

        - {"op": "inject_shock", "shock_type", "side"?, "magnitude", "duration"?}
        - {"op": "set_sentiment", "value", "duration"}
        - {"op": "withdraw_mm", "duration"}
        """
        from tezcat.checkpoints import checkpoint_hash

        eng = cls.restore(checkpoint, config, run_id=new_run_id)
        intervention = intervention or {"name": "none", "operations": []}
        eng.events_log.append(eng.step_num, "intervention", {
            "name": intervention.get("name", "unnamed"),
            "operations": intervention.get("operations", []),
            "parent_run_id": checkpoint["run_id"],
            "checkpoint_step": checkpoint["step"],
            "checkpoint_hash": checkpoint_hash(checkpoint),
        })
        for op in intervention.get("operations", []):
            kind = op["op"]
            if kind == "inject_shock":
                eng.shocks.inject_manual(op["shock_type"], op.get("side"),
                                         op["magnitude"], op.get("duration"))
            elif kind == "set_sentiment":
                eng.env.sentiment = max(-1.0, min(1.0, float(op["value"])))
                eng.env._sentiment_until = eng.step_num + int(op["duration"])
            elif kind == "withdraw_mm":
                eng.env.mm_withdrawn = True
                eng.env._mm_withdraw_until = eng.step_num + int(op["duration"])
            else:
                raise ValueError(f"unknown intervention op {kind!r}")
        return eng
