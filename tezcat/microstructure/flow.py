"""Event-stream microstructure analytics (Phase F7).

``MicrostructureAnalyzer`` replays a run's canonical event log through an
embedded :class:`~tezcat.events.replay.ReplayKernel` and measures the market
*as it evolves* — so every metric has exact lineage to source events, and
the book state used for pre-trade mids is the proven-correct replayed book,
not a parallel approximation.

Metric definitions (units, interpretation, edge cases)
------------------------------------------------------
aggressor sign
    +1 if the incoming (non-resting) order of a trade is the buyer, -1 if
    the seller. Inferred from event order: trades follow their aggressor's
    ``order_accepted`` event.

effective spread (per trade)
    ``2 * sign * (price - mid_pre)`` where ``mid_pre`` is the mid quote
    immediately before the trade. Units: price. Positive = the aggressor
    paid up relative to mid. Undefined (skipped, counted) when the
    pre-trade book is one-sided.

realized spread (per trade, horizon h steps)
    ``2 * sign * (price - mid_{t+h})`` using the end-of-step mid h steps
    later. What the liquidity provider actually kept after the market
    moved. Undefined near the end of the run (skipped, counted).

price impact (per trade, horizon h)
    ``sign * (mid_{t+h} - mid_pre)``. Effective spread decomposes as
    realized spread + 2 * impact.

signed volume (per step)
    Sum of aggressor-signed trade quantities. Units: shares.

queue position at rest
    Shares already resting at an order's price level when it rests (shares
    ahead in FIFO). Units: shares.

fill statistics (limit orders)
    fill fraction (filled/quantity at terminal state or end of run),
    fully-filled probability, steps to first fill.

cancellation intensity (per step)
    cancels + expiries this step.

TCA (per order with an arrival mid; zero-fee market, fees reported as 0)
    slippage/share = ``sign * (avg_fill - arrival_mid)``;
    execution cost = slippage/share * filled;
    opportunity cost = ``sign * (final_mid - arrival_mid) * unfilled``
    for terminally unfilled remainder (Perold-style shortfall
    decomposition); implementation shortfall = execution + opportunity.
    Orders with no arrival mid (empty book) are counted, not invented.

Latency categories are not modeled (the engine has no latency yet) and are
deliberately absent rather than fabricated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tezcat.core.config import ExperimentConfig
from tezcat.events.replay import ReplayKernel
from tezcat.microstructure.metrics import microprice, queue_imbalance, relative_spread


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


class MicrostructureAnalyzer:
    def __init__(self, config: ExperimentConfig, run_id: str, horizon: int = 5):
        self.config = config
        self.run_id = run_id
        self.horizon = horizon
        self.kernel = ReplayKernel(config, run_id)

    # ------------------------------------------------------------------
    def _best_qty(self, side) -> int:
        tick = side.best_tick()
        if tick is None:
            return 0
        return sum(o.remaining for o in side.levels[tick])

    def _mid(self) -> Optional[float]:
        return self.kernel.book.mid_price()

    # ------------------------------------------------------------------
    def run(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        book = self.kernel.book
        orders: Dict[str, Dict[str, Any]] = {}   # order lifecycle records
        trades: List[Dict[str, Any]] = []
        steps: List[Dict[str, Any]] = []
        last_accepted: Optional[str] = None
        step_signed_volume = 0
        step_cancels = 0
        step_trades = 0

        for ev in events:
            etype, step, d = ev["type"], ev["step"], ev["data"]

            if etype == "order_accepted":
                last_accepted = d["order_id"]
                orders[d["order_id"]] = {
                    "order_id": d["order_id"], "agent_id": d["agent_id"],
                    "side": d["side"], "order_type": d["order_type"],
                    "quantity": d["quantity"], "arrival_step": step,
                    "arrival_mid": self._mid(), "filled": 0, "fill_value": 0.0,
                    "first_fill_step": None, "terminal": None,
                    "queue_ahead": None,
                }

            elif etype == "trade":
                rec_b = orders.get(d["buy_order_id"])
                rec_s = orders.get(d["sell_order_id"])
                sign = 0
                if d["buy_order_id"] == last_accepted:
                    sign = 1
                elif d["sell_order_id"] == last_accepted:
                    sign = -1
                mid_pre = self._mid()
                eff = 2 * sign * (d["price"] - mid_pre) if (mid_pre and sign) else None
                trades.append({"step": step, "price": d["price"],
                               "quantity": d["quantity"], "sign": sign,
                               "mid_pre": mid_pre, "effective_spread": eff})
                step_signed_volume += sign * d["quantity"]
                step_trades += 1
                for rec in (rec_b, rec_s):
                    if rec is not None:
                        rec["filled"] += d["quantity"]
                        rec["fill_value"] += d["price"] * d["quantity"]
                        if rec["first_fill_step"] is None:
                            rec["first_fill_step"] = step

            elif etype == "order_rested":
                rec = orders.get(d["order_id"])
                if rec is not None:
                    # Shares ahead at this level, measured *before* the
                    # kernel applies the rest.
                    tick = book.to_tick(d["price"])
                    side = book.bids if rec["side"] == "buy" else book.asks
                    ahead = sum(o.remaining for o in side.levels.get(tick, []))
                    rec["queue_ahead"] = ahead

            elif etype in ("order_cancelled", "order_expired"):
                step_cancels += 1
                rec = orders.get(d["order_id"])
                if rec is not None:
                    rec["terminal"] = etype.removeprefix("order_")

            elif etype == "order_discarded":
                rec = orders.get(d["order_id"])
                if rec is not None:
                    rec["terminal"] = "discarded"

            elif etype == "step_ended":
                # Record the step row BEFORE applying (book state is already
                # end-of-step; step_ended only sets env fields).
                bb, ba = book.best_bid, book.best_ask
                bq, aq = self._best_qty(book.bids), self._best_qty(book.asks)
                steps.append({
                    "step": step,
                    "mid": book.mid_price(),
                    "microprice": microprice(bb, bq, ba, aq),
                    "queue_imbalance": queue_imbalance(bq, aq),
                    "relative_spread": relative_spread(bb, ba),
                    "signed_volume": step_signed_volume,
                    "n_trades": step_trades,
                    "cancellations": step_cancels,
                })
                step_signed_volume = 0
                step_cancels = 0
                step_trades = 0

            self.kernel.apply(ev)

        # Fully-filled orders that never rested/cancelled terminate "filled".
        for rec in orders.values():
            if rec["terminal"] is None and rec["filled"] >= rec["quantity"]:
                rec["terminal"] = "filled"

        return self._summarize(orders, trades, steps)

    # ------------------------------------------------------------------
    def _summarize(self, orders: Dict[str, Dict[str, Any]],
                   trades: List[Dict[str, Any]],
                   steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        h = self.horizon
        mid_by_step = {s["step"]: s["mid"] for s in steps}

        # Realized spread and impact at horizon h.
        skipped_horizon = 0
        for t in trades:
            future_mid = mid_by_step.get(t["step"] + h)
            if (future_mid is None or t["mid_pre"] is None or not t["sign"]):
                t["realized_spread"] = None
                t["price_impact"] = None
                skipped_horizon += 1
                continue
            t["realized_spread"] = 2 * t["sign"] * (t["price"] - future_mid)
            t["price_impact"] = t["sign"] * (future_mid - t["mid_pre"])

        # Fill statistics over limit orders.
        limits = [o for o in orders.values() if o["order_type"] == "limit"]
        fill_fracs = [o["filled"] / o["quantity"] for o in limits]
        fully = [o for o in limits if o["filled"] >= o["quantity"]]
        first_fill = [o["first_fill_step"] - o["arrival_step"]
                      for o in limits if o["first_fill_step"] is not None]
        queue_ahead = [o["queue_ahead"] for o in limits
                       if o["queue_ahead"] is not None]

        # TCA (zero-fee market — fees reported explicitly as 0).
        final_mid = steps[-1]["mid"] if steps else None
        tca_orders, no_reference = [], 0
        for o in orders.values():
            if o["arrival_mid"] is None:
                no_reference += 1
                continue
            if o["filled"] == 0 and o["terminal"] is None:
                continue  # still resting untouched at end of run
            sign = 1 if o["side"] == "buy" else -1
            avg_fill = o["fill_value"] / o["filled"] if o["filled"] else None
            slip = (sign * (avg_fill - o["arrival_mid"])
                    if avg_fill is not None else None)
            execution = slip * o["filled"] if slip is not None else 0.0
            unfilled = o["quantity"] - o["filled"]
            opportunity = (sign * (final_mid - o["arrival_mid"]) * unfilled
                           if (unfilled > 0 and o["terminal"] is not None
                               and final_mid is not None) else 0.0)
            tca_orders.append({
                "order_id": o["order_id"], "agent_id": o["agent_id"],
                "side": o["side"], "order_type": o["order_type"],
                "filled": o["filled"], "unfilled": unfilled,
                "slippage_per_share": slip, "fees": 0.0,
                "execution_cost": execution, "opportunity_cost": opportunity,
                "implementation_shortfall": execution + opportunity,
            })

        eff = [t["effective_spread"] for t in trades if t["effective_spread"] is not None]
        rs = [t["realized_spread"] for t in trades if t["realized_spread"] is not None]
        pi = [t["price_impact"] for t in trades if t["price_impact"] is not None]

        return {
            "run_id": self.run_id,
            "horizon_steps": h,
            "replay_state_hash": self.kernel.state_hash(),  # lineage proof
            "trades": {
                "n": len(trades),
                "mean_effective_spread": _mean(eff),
                "mean_realized_spread": _mean(rs),
                "mean_price_impact": _mean(pi),
                "skipped_no_horizon_or_reference": skipped_horizon,
            },
            "fills": {
                "n_limit_orders": len(limits),
                "fill_probability": len(fully) / len(limits) if limits else None,
                "mean_fill_fraction": _mean(fill_fracs),
                "mean_steps_to_first_fill": _mean([float(v) for v in first_fill]),
                "mean_queue_ahead_at_rest": _mean([float(v) for v in queue_ahead]),
            },
            "tca": {
                "n_orders": len(tca_orders),
                "orders_without_reference_mid": no_reference,
                "fee_model": "zero-fee market (explicit)",
                "total_execution_cost": sum(o["execution_cost"] for o in tca_orders),
                "total_opportunity_cost": sum(o["opportunity_cost"] for o in tca_orders),
                "total_implementation_shortfall": sum(
                    o["implementation_shortfall"] for o in tca_orders),
                "by_agent_type": self._tca_by_type(tca_orders),
            },
            "qi_association": self._qi_association(steps),
            "per_step": steps,
            "per_trade": trades,
            "per_order_tca": tca_orders,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _tca_by_type(tca_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        by: Dict[str, Dict[str, Any]] = {}
        for o in tca_orders:
            # agent ids look like "<type>_<gi>_<i>" or "whale"
            parts = o["agent_id"].rsplit("_", 2)
            atype = parts[0] if len(parts) == 3 else o["agent_id"]
            t = by.setdefault(atype, {"orders": 0, "execution_cost": 0.0,
                                      "opportunity_cost": 0.0,
                                      "implementation_shortfall": 0.0})
            t["orders"] += 1
            t["execution_cost"] += o["execution_cost"]
            t["opportunity_cost"] += o["opportunity_cost"]
            t["implementation_shortfall"] += o["implementation_shortfall"]
        return by

    @staticmethod
    def _qi_association(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Association between queue imbalance and the next step's mid move.

        A within-model experimental association (correlation), NOT a causal
        or real-market claim — see docs/microstructure.md.
        """
        pairs = []
        for prev, nxt in zip(steps, steps[1:]):
            if (prev["queue_imbalance"] is not None and prev["mid"] is not None
                    and nxt["mid"] is not None):
                pairs.append((prev["queue_imbalance"], nxt["mid"] - prev["mid"]))
        if len(pairs) < 10:
            return {"correlation": None, "n": len(pairs),
                    "warning": "fewer than 10 usable step pairs"}
        xs, ys = zip(*pairs)
        n = len(pairs)
        mx, my = sum(xs) / n, sum(ys) / n
        sx = (sum((v - mx) ** 2 for v in xs)) ** 0.5
        sy = (sum((v - my) ** 2 for v in ys)) ** 0.5
        if sx == 0 or sy == 0:
            return {"correlation": 0.0, "n": n,
                    "warning": "zero variance in imbalance or mid moves"}
        corr = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
        return {"correlation": corr, "n": n}
