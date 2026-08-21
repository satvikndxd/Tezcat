"""Shock engine: scheduled and manual exogenous events.

Shock effects are applied through the shared ``EnvironmentState`` (sentiment,
mm_withdrawal) or by submitting orders from a dedicated whale participant.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tezcat.core.config import ShockConfig, ShockTrigger, ShockType

@dataclass
class EnvironmentState:
    """Mutable shared state that shocks act on and agents observe."""

    sentiment: float = 0.0          # [-1, 1]
    mm_withdrawn: bool = False
    _sentiment_decay: float = 0.0
    _mm_withdraw_until: int = -1
    _sentiment_until: int = -1

    def tick(self, step: int) -> None:
        if self.mm_withdrawn and step > self._mm_withdraw_until:
            self.mm_withdrawn = False
        if step > self._sentiment_until and abs(self.sentiment) > 1e-6:
            self.sentiment *= 0.97  # decay back to neutral
            if abs(self.sentiment) < 0.02:
                self.sentiment = 0.0


@dataclass
class ShockEvent:
    event_id: str
    run_id: str
    step: int
    shock_id: str
    shock_type: str
    trigger_reason: str
    payload: Dict[str, Any]
    market_before: Dict[str, Any] = field(default_factory=dict)
    market_after: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "step": self.step,
            "shock_id": self.shock_id,
            "shock_type": self.shock_type,
            "trigger_reason": self.trigger_reason,
            "payload": self.payload,
            "market_before": self.market_before,
            "market_after": self.market_after,
        }


class ShockEngine:
    def __init__(self, run_id: str, schedule: List[ShockConfig], env: EnvironmentState):
        self.run_id = run_id
        self.schedule = [s for s in schedule if s.enabled]
        self.env = env
        self.events: List[ShockEvent] = []
        self._manual_queue: List[ShockConfig] = []
        self._manual_counter = itertools.count(1)
        # Run-scoped: event IDs are deterministic per run, not process-global.
        self._event_counter = itertools.count(1)
        # Active whale programs: {"side", "remaining", "per_step", "until"}
        self.active_whales: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def start_whale(self, cfg: ShockConfig, step: int) -> Dict[str, Any]:
        """Register a whale order program executed over cfg.duration steps."""
        side = cfg.side or "sell"
        total = max(1, int(cfg.magnitude))
        per_step = max(1, total // max(1, cfg.duration))
        self.active_whales.append({
            "side": side, "remaining": total, "per_step": per_step,
            "until": step + cfg.duration,
        })
        return {"side": side, "quantity": total, "per_step": per_step,
                "duration": cfg.duration}

    def whale_slices(self, step: int) -> List[Dict[str, Any]]:
        """Quantity each active whale should trade this step; prunes finished."""
        out = []
        for w in self.active_whales:
            if w.get("last_step") == step:
                continue  # already executed this step
            qty = min(w["per_step"], w["remaining"])
            if qty > 0 and step <= w["until"]:
                w["last_step"] = step
                out.append({"side": w["side"], "quantity": qty, "program": w})
        self.active_whales = [w for w in self.active_whales
                              if w["remaining"] > 0 and step <= w["until"]]
        return out

    # ------------------------------------------------------------------
    def inject_manual(self, shock_type: str, side: Optional[str], magnitude: float,
                      duration: Optional[int]) -> ShockConfig:
        cfg = ShockConfig(
            shock_id=f"manual_{next(self._manual_counter)}",
            shock_type=ShockType(shock_type),
            trigger=ShockTrigger(kind="manual"),
            side=side,
            magnitude=magnitude,
            duration=duration or 20,
            description="manually injected",
        )
        self._manual_queue.append(cfg)
        return cfg

    # ------------------------------------------------------------------
    def due_shocks(self, step: int) -> List[tuple]:
        """Return (config, trigger_reason) pairs to fire at this step."""
        due = [(s, "scheduled") for s in self.schedule
               if s.trigger.kind == "scheduled" and s.trigger.step == step]
        while self._manual_queue:
            due.append((self._manual_queue.pop(0), "manual"))
        return due

    # ------------------------------------------------------------------
    def apply_env_shock(self, cfg: ShockConfig, step: int) -> Dict[str, Any]:
        """Apply non-order shocks to environment state. Returns the payload."""
        if cfg.shock_type == ShockType.MM_WITHDRAWAL:
            self.env.mm_withdrawn = True
            self.env._mm_withdraw_until = step + cfg.duration
            return {"withdrawn_until": step + cfg.duration}
        if cfg.shock_type == ShockType.SENTIMENT_SHOCK:
            direction = -1.0 if (cfg.side or "sell") == "sell" else 1.0
            self.env.sentiment = max(-1.0, min(1.0, direction * cfg.magnitude))
            self.env._sentiment_until = step + cfg.duration
            return {"sentiment": self.env.sentiment, "until": step + cfg.duration}
        return {}

    def record(self, cfg: ShockConfig, step: int, reason: str, payload: Dict[str, Any],
               before: Dict[str, Any], after: Dict[str, Any]) -> ShockEvent:
        ev = ShockEvent(
            event_id=f"shk_{next(self._event_counter):06d}",
            run_id=self.run_id,
            step=step,
            shock_id=cfg.shock_id,
            shock_type=cfg.shock_type.value,
            trigger_reason=reason,
            payload=payload,
            market_before=before,
            market_after=after,
        )
        self.events.append(ev)
        return ev
