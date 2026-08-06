"""Metrics engine: incremental step metrics and the final run report."""

from __future__ import annotations

import itertools
import math
from collections import deque
from typing import Any, Dict, List, Optional

from tezcat.core.config import MetricsPolicy

_report_counter = itertools.count(1)


class MetricsEngine:
    def __init__(self, run_id: str, policy: MetricsPolicy, initial_price: float):
        self.run_id = run_id
        self.policy = policy
        self.initial_price = initial_price

        self._returns_window: deque = deque(maxlen=policy.vol_window)
        self._prices_window: deque = deque(maxlen=max(policy.return_window + 1, 2))
        self._last_price = initial_price
        self._peak_price = initial_price
        self._max_drawdown = 0.0
        self._sum_spread = 0.0
        self._n_spread = 0
        self._total_volume = 0
        self._total_trades = 0
        self._sum_sq_returns = 0.0
        self._n_returns = 0
        self._spread_history: deque = deque(maxlen=200)
        self._baseline_spread: Optional[float] = None
        self._min_depth_frac = 1.0
        self._baseline_depth: Optional[float] = None
        self._max_spread_mult = 0.0
        self.step_metrics: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    @property
    def last_price(self) -> float:
        return self._last_price

    @property
    def rolling_volatility(self) -> float:
        n = len(self._returns_window)
        if n < 2:
            return 0.0
        mean = sum(self._returns_window) / n
        var = sum((r - mean) ** 2 for r in self._returns_window) / (n - 1)
        return math.sqrt(var)

    def recent_return(self) -> float:
        if len(self._prices_window) < 2:
            return 0.0
        first, last = self._prices_window[0], self._prices_window[-1]
        return (last - first) / first if first > 0 else 0.0

    # ------------------------------------------------------------------
    def on_step(self, step: int, last_price: float, spread: Optional[float],
                bid_depth: int, ask_depth: int, volume: int, n_trades: int,
                regime: str) -> Dict[str, Any]:
        log_ret = math.log(last_price / self._last_price) if self._last_price > 0 and last_price > 0 else 0.0
        self._returns_window.append(log_ret)
        self._prices_window.append(last_price)
        self._sum_sq_returns += log_ret * log_ret
        self._n_returns += 1
        self._last_price = last_price

        self._peak_price = max(self._peak_price, last_price)
        drawdown = (self._peak_price - last_price) / self._peak_price if self._peak_price > 0 else 0.0
        self._max_drawdown = max(self._max_drawdown, drawdown)

        depth = bid_depth + ask_depth
        if spread is not None:
            self._sum_spread += spread
            self._n_spread += 1
            rel_spread = spread / last_price if last_price > 0 else 0.0
            self._spread_history.append(rel_spread)
            if self._baseline_spread is None and len(self._spread_history) >= 30:
                s = sorted(self._spread_history)
                self._baseline_spread = s[len(s) // 2]
            if self._baseline_spread:
                self._max_spread_mult = max(self._max_spread_mult, rel_spread / self._baseline_spread)
        if self._baseline_depth is None and step >= 30:
            self._baseline_depth = max(1.0, depth)
        if self._baseline_depth:
            self._min_depth_frac = min(self._min_depth_frac, depth / self._baseline_depth)

        self._total_volume += volume
        self._total_trades += n_trades

        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0

        row = {
            "step": step,
            "last_price": round(last_price, 4),
            "log_return": round(log_ret, 6),
            "rolling_volatility": round(self.rolling_volatility, 6),
            "spread": round(spread, 4) if spread is not None else None,
            "depth": depth,
            "volume": volume,
            "order_imbalance": round(imbalance, 4),
            "regime": regime,
        }
        self.step_metrics.append(row)
        return row

    # ------------------------------------------------------------------
    def build_report(self, run_id: str, agents: List[Any], last_price: float,
                     shock_count: int, regime_events: List[Any],
                     regime_step_counts: Dict[str, int]) -> Dict[str, Any]:
        realized_vol = math.sqrt(self._sum_sq_returns / self._n_returns) if self._n_returns else 0.0
        avg_spread = self._sum_spread / self._n_spread if self._n_spread else 0.0

        pnl_by_type: Dict[str, Dict[str, Any]] = {}
        inv_shares, vol_shares = [], []
        total_inv = sum(max(0, a.portfolio.inventory) for a in agents) or 1
        for a in agents:
            t = pnl_by_type.setdefault(a.agent_type, {
                "agents": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
                "total_pnl": 0.0, "final_cash": 0.0, "final_inventory": 0,
            })
            t["agents"] += 1
            t["realized_pnl"] += a.portfolio.realized_pnl
            t["unrealized_pnl"] += a.portfolio.unrealized_pnl(last_price)
            t["total_pnl"] += a.portfolio.total_pnl(last_price, self.initial_price)
            t["final_cash"] += a.portfolio.cash
            t["final_inventory"] += a.portfolio.inventory
            inv_shares.append(max(0, a.portfolio.inventory) / total_inv)
        for t in pnl_by_type.values():
            for k in ("realized_pnl", "unrealized_pnl", "total_pnl", "final_cash"):
                t[k] = round(t[k], 2)

        total_regime_steps = sum(regime_step_counts.values()) or 1
        report = {
            "report_id": f"rpt_{next(_report_counter):06d}",
            "run_id": run_id,
            "initial_price": round(self.initial_price, 4),
            "final_price": round(last_price, 4),
            "total_return": round((last_price - self.initial_price) / self.initial_price, 6),
            "realized_volatility": round(realized_vol, 6),
            "average_spread": round(avg_spread, 4),
            "max_drawdown": round(self._max_drawdown, 6),
            "total_volume": self._total_volume,
            "total_trades": self._total_trades,
            "crash_detected": self._max_drawdown >= self.policy.crash_drawdown_threshold,
            "liquidity_crisis_detected": (
                self._max_spread_mult >= self.policy.liquidity_spread_mult or self._min_depth_frac <= 0.2
            ),
            "max_spread_mult": round(self._max_spread_mult, 2),
            "min_depth_frac": round(self._min_depth_frac, 3),
            "agent_pnl_by_type": pnl_by_type,
            "regime_step_share": {
                k: round(v / total_regime_steps, 4) for k, v in regime_step_counts.items()
            },
            "shock_count": shock_count,
            "regime_transition_count": len(regime_events),
        }
        if self.policy.compute_hhi and inv_shares:
            report["hhi_inventory"] = round(sum(s * s for s in inv_shares), 4)
        return report
