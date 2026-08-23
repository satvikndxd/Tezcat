"""Ecology Fingerprint (Phase S4): a market-environment identity.

A compact, **derived, versioned, hashable, immutable** summary of the
market environment one world realizes. A strategy result can then say
"performance under ecology fingerprint X" — an environment identity, not
merely a file name.

Every value is computed from the world's own artifacts (snapshots, trades,
config); nothing is hand-assigned. Numeric features only — no subjective
"low/medium/high" grades. ``FINGERPRINT_VERSION`` participates in the
fingerprint hash: changing any feature definition mints new identities
rather than silently reinterpreting old ones.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import Field

from tezcat.analysis.stylized_facts import (
    hill_tail_index, log_returns, max_drawdown,
)
from tezcat.analysis.stylized_facts import extract_features
from tezcat.core.config import ExperimentConfig, FrozenModel, canonical_json

FINGERPRINT_VERSION = 1


def _quantile(sorted_xs: List[float], q: float) -> Optional[float]:
    if not sorted_xs:
        return None
    idx = q * (len(sorted_xs) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_xs) - 1)
    frac = idx - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


class EcologyFingerprint(FrozenModel):
    """Versioned numeric description of one realized market ecology."""

    fingerprint_version: int = FINGERPRINT_VERSION
    n_steps: int = Field(..., ge=1)
    # liquidity / microstructure
    mean_relative_spread: Optional[float] = None
    depth_q25: Optional[float] = None
    depth_q50: Optional[float] = None
    depth_q75: Optional[float] = None
    mean_order_imbalance: Optional[float] = None
    # price dynamics
    return_volatility: Optional[float] = None
    hill_tail_alpha: Optional[float] = None
    volatility_clustering: Optional[float] = None
    max_drawdown: Optional[float] = None
    crash_detected: bool = False
    # activity
    trades_per_step: Optional[float] = None
    volume_per_step: Optional[float] = None
    # regimes (fraction of steps spent in each)
    regime_occupancy: Dict[str, float] = Field(default_factory=dict)
    n_regime_transitions: int = 0
    n_shocks: int = 0
    # structural (from the experiment config, not the realization)
    agent_composition: Dict[str, float] = Field(default_factory=dict)
    risk_enabled: bool = False
    total_agents: int = 0

    def fingerprint_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode()).hexdigest()


def extract_fingerprint(config: ExperimentConfig,
                        snapshots: List[Dict[str, Any]],
                        trades: List[Dict[str, Any]],
                        regime_events: List[Dict[str, Any]],
                        shock_events: List[Dict[str, Any]]
                        ) -> EcologyFingerprint:
    """Derive the fingerprint from one world's realized artifacts."""
    n = len(snapshots)
    if n == 0:
        raise ValueError("cannot fingerprint an empty world")

    rel_spreads = [s["spread"] / s["mid_price"] for s in snapshots
                   if s.get("spread") is not None
                   and s.get("mid_price") not in (None, 0)]
    depths = sorted(s["bid_depth"] + s["ask_depth"] for s in snapshots
                    if s.get("bid_depth") is not None
                    and s.get("ask_depth") is not None)
    imbalances = [s["order_imbalance"] for s in snapshots
                  if s.get("order_imbalance") is not None]

    prices = [s["last_price"] for s in snapshots]
    returns = log_returns(prices)
    feats = extract_features(prices)
    vol = None
    if len(returns) >= 2:
        m = sum(returns) / len(returns)
        vol = (sum((r - m) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5

    occupancy: Dict[str, int] = {}
    for s in snapshots:
        occupancy[s.get("regime", "stable")] = occupancy.get(
            s.get("regime", "stable"), 0) + 1

    total_count = sum(g.count for g in config.agents)
    composition = {}
    for g in config.agents:
        key = g.agent_type.value
        composition[key] = composition.get(key, 0.0) + g.count / total_count

    mdd = max_drawdown(prices)
    return EcologyFingerprint(
        n_steps=n,
        mean_relative_spread=_mean(rel_spreads),
        depth_q25=_quantile(depths, 0.25),
        depth_q50=_quantile(depths, 0.50),
        depth_q75=_quantile(depths, 0.75),
        mean_order_imbalance=_mean(imbalances),
        return_volatility=vol,
        hill_tail_alpha=hill_tail_index(returns) if len(returns) >= 60 else None,
        volatility_clustering=feats.get("volatility_clustering"),
        max_drawdown=mdd,
        crash_detected=mdd >= config.metrics_policy.crash_drawdown_threshold,
        trades_per_step=len(trades) / n,
        volume_per_step=sum(t["quantity"] for t in trades) / n,
        regime_occupancy={k: v / n for k, v in sorted(occupancy.items())},
        n_regime_transitions=len(regime_events),
        n_shocks=len(shock_events),
        agent_composition={k: round(v, 6) for k, v in sorted(composition.items())},
        risk_enabled=config.risk.enabled,
        total_agents=total_count,
    )
