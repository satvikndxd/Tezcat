"""Regime engine: detects stable / crisis / recovery with hysteresis."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional

from tezcat.core.config import Regime, RegimeModifiers, RegimePolicy

@dataclass
class RegimeEvent:
    event_id: str
    run_id: str
    step: int
    previous_regime: str
    new_regime: str
    reason: str
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "step": self.step,
            "previous_regime": self.previous_regime,
            "new_regime": self.new_regime,
            "reason": self.reason,
            "metrics": self.metrics,
        }


class RegimeEngine:
    def __init__(self, run_id: str, policy: RegimePolicy):
        self.run_id = run_id
        self.policy = policy
        self.current = Regime.STABLE
        self.events: List[RegimeEvent] = []
        # Run-scoped plain-int counter: deterministic and checkpointable.
        self._event_seq = 0

        lb = policy.lookback
        self._prices: deque = deque(maxlen=lb)
        self._spreads: deque = deque(maxlen=lb)
        self._depths: deque = deque(maxlen=lb)
        self._baseline_spread: Optional[float] = None
        self._baseline_depth: Optional[float] = None
        self._crisis_peak_vol: float = 0.0
        self._confirm_count = 0
        self._pending: Optional[Regime] = None
        self._calm_steps = 0

    # ------------------------------------------------------------------
    @property
    def modifiers(self) -> RegimeModifiers:
        return self.policy.modifiers.get(self.current, RegimeModifiers())

    # ------------------------------------------------------------------
    def observe(self, step: int, price: float, spread: Optional[float], depth: int,
                volatility: float) -> None:
        self._prices.append(price)
        if spread is not None:
            self._spreads.append(spread)
        self._depths.append(depth)

        # Freeze baselines from early calm history.
        if self._baseline_spread is None and len(self._spreads) >= 30:
            self._baseline_spread = median(self._spreads)
            self._baseline_depth = median(self._depths)

        if not self.policy.enabled or step % self.policy.evaluation_interval != 0:
            return
        if len(self._prices) < 20:
            return

        target, reason, metrics = self._evaluate(price, spread, depth, volatility)
        if target == self.current:
            self._pending = None
            self._confirm_count = 0
            return
        if target == self._pending:
            self._confirm_count += 1
        else:
            self._pending = target
            self._confirm_count = 1
        if self._confirm_count >= self.policy.persistence_required:
            self._transition(step, target, reason, metrics)

    # ------------------------------------------------------------------
    def _evaluate(self, price: float, spread: Optional[float], depth: int, vol: float):
        peak = max(self._prices)
        drawdown = (peak - price) / peak if peak > 0 else 0.0
        spread_mult = (spread / self._baseline_spread) if (spread and self._baseline_spread) else 1.0
        depth_frac = (depth / self._baseline_depth) if self._baseline_depth else 1.0
        metrics = {
            "drawdown": round(drawdown, 5),
            "spread_mult": round(spread_mult, 3),
            "depth_frac": round(depth_frac, 3),
            "volatility": round(vol, 6),
        }
        p = self.policy

        if self.current != Regime.CRISIS:
            if drawdown >= p.crisis_drawdown:
                return Regime.CRISIS, f"drawdown {drawdown:.1%} >= {p.crisis_drawdown:.0%}", metrics
            if spread_mult >= p.crisis_spread_mult and depth_frac <= p.crisis_depth_frac:
                return (Regime.CRISIS,
                        f"spread {spread_mult:.1f}x baseline with depth at {depth_frac:.0%}", metrics)

        if self.current == Regime.CRISIS:
            self._crisis_peak_vol = max(self._crisis_peak_vol, vol)
            short = list(self._prices)[-10:]
            stabilizing = short[-1] >= short[0] * 0.997
            vol_ok = vol <= self._crisis_peak_vol * p.recovery_vol_frac or vol < 1e-9
            if stabilizing and vol_ok and drawdown < p.crisis_drawdown * 1.5:
                return Regime.RECOVERY, "price stabilized and volatility receding", metrics
            return Regime.CRISIS, "", metrics

        if self.current == Regime.RECOVERY:
            calm = (spread_mult < p.crisis_spread_mult * 0.6) and drawdown < p.crisis_drawdown * 0.6
            if calm:
                self._calm_steps += p.evaluation_interval
            else:
                self._calm_steps = 0
            if self._calm_steps >= p.stable_after:
                return Regime.STABLE, f"calm for {self._calm_steps} steps", metrics
            return Regime.RECOVERY, "", metrics

        return Regime.STABLE, "", metrics

    # ------------------------------------------------------------------
    def _transition(self, step: int, target: Regime, reason: str, metrics: Dict[str, Any]) -> None:
        self._event_seq += 1
        ev = RegimeEvent(
            event_id=f"rgm_{self._event_seq:06d}",
            run_id=self.run_id,
            step=step,
            previous_regime=self.current.value,
            new_regime=target.value,
            reason=reason,
            metrics=metrics,
        )
        self.events.append(ev)
        self.current = target
        self._pending = None
        self._confirm_count = 0
        self._calm_steps = 0
        if target == Regime.CRISIS:
            self._crisis_peak_vol = 0.0

    # -- checkpoint support (Phase F3) ---------------------------------
    def state_dict(self) -> Dict[str, Any]:
        return {
            "current": self.current.value,
            "event_seq": self._event_seq,
            "prices": list(self._prices),
            "spreads": list(self._spreads),
            "depths": list(self._depths),
            "baseline_spread": self._baseline_spread,
            "baseline_depth": self._baseline_depth,
            "crisis_peak_vol": self._crisis_peak_vol,
            "confirm_count": self._confirm_count,
            "pending": self._pending.value if self._pending is not None else None,
            "calm_steps": self._calm_steps,
            "events": [e.to_dict() for e in self.events],
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        self.current = Regime(state["current"])
        self._event_seq = state["event_seq"]
        self._prices.clear(); self._prices.extend(state["prices"])
        self._spreads.clear(); self._spreads.extend(state["spreads"])
        self._depths.clear(); self._depths.extend(state["depths"])
        self._baseline_spread = state["baseline_spread"]
        self._baseline_depth = state["baseline_depth"]
        self._crisis_peak_vol = state["crisis_peak_vol"]
        self._confirm_count = state["confirm_count"]
        self._pending = Regime(state["pending"]) if state["pending"] is not None else None
        self._calm_steps = state["calm_steps"]
        self.events = [RegimeEvent(**e) for e in state["events"]]
