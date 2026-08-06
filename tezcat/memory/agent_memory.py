"""Bounded, serializable adaptive memory for agents.

All fields update incrementally (EMA-style) so memory stays O(1) per agent and
never breaks determinism.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class AgentMemory:
    confidence: float = 0.5       # [0, 1] willingness to act / size up
    fear: float = 0.0             # [0, 1] risk aversion pressure
    recent_loss: float = 0.0      # EMA of per-step equity losses (>= 0)
    trend_belief: float = 0.0     # EMA of recent returns
    value_belief: float = 0.0     # anchor price (set on first observation)
    volatility_estimate: float = 0.0
    last_equity: float = 0.0

    def update(self, price: float, log_return: float, equity: float, regime: str) -> None:
        # Trend and volatility beliefs.
        self.trend_belief = 0.85 * self.trend_belief + 0.15 * log_return
        self.volatility_estimate = 0.94 * self.volatility_estimate + 0.06 * abs(log_return)

        # Value anchor drifts slowly toward the observed price.
        if self.value_belief <= 0:
            self.value_belief = price
        else:
            self.value_belief = 0.995 * self.value_belief + 0.005 * price

        # PnL experience -> fear / confidence.
        if self.last_equity > 0:
            pnl = equity - self.last_equity
            loss = max(0.0, -pnl) / max(self.last_equity, 1e-9)
            self.recent_loss = 0.9 * self.recent_loss + 0.1 * loss
        self.last_equity = equity

        fear_drive = 25.0 * self.recent_loss + (0.35 if regime == "crisis" else 0.0)
        self.fear = _clamp(0.92 * self.fear + 0.08 * fear_drive, 0.0, 1.0)
        self.confidence = _clamp(0.95 * self.confidence + 0.05 * (1.0 - self.fear), 0.05, 1.0)

    def to_dict(self) -> dict:
        return {k: round(v, 8) for k, v in asdict(self).items()}
