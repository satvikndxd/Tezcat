"""Stress Lab: canonical, versioned stress scenarios (Phase F8).

A stress scenario is not a new execution engine — it is an
:class:`~tezcat.experiments.schema.ExperimentVersion` built from a named
constructor, so every scenario inherits the full F4/F5 machinery: immutable
content-addressed identity, deterministic seeds, resumable batches,
seed-level artifacts, and F6 analysis. Reproducing a scenario is reproducing
its experiment version.
"""

from __future__ import annotations

from typing import List, Optional

from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, RiskPolicy, ShockConfig,
    ShockTrigger, ShockType,
)
from tezcat.experiments.schema import DesignSpec, ExperimentVersion, Factor


def stressed_base_config(total_steps: int = 300, whale_magnitude: int = 3000,
                         shock_step: int = 150,
                         initial_margin: float = 0.1,
                         maintenance_margin: float = 0.05) -> ExperimentConfig:
    """Pump-then-dump on a margin-enabled ecology.

    A sentiment pump (step 40) makes cash-light retail/momentum agents lever
    up through margin borrowing; a whale dump (step ``shock_step``) then
    marks their collateral down. Margin ratio only *falls* with price when
    an agent carries debt (``ratio = 1 - debt/position``), so the pump phase
    is what makes the crash capable of forcing liquidation — the cascade is
    endogenous, not scripted.

    Empirically (300 steps, defaults): some seeds produce margin-call
    cascades with ~50% drawdowns, others stay safe — cross-seed variation
    is the point; use replications.
    """
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=10),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=10,
                             cash=600, inventory=80,
                             params={"herding": 0.7, "base_size": 14}),
            AgentGroupConfig(agent_type=AgentType.MOMENTUM_TRADER, count=6,
                             cash=600, inventory=80,
                             params={"base_size": 16}),
            AgentGroupConfig(agent_type=AgentType.MEAN_REVERSION_TRADER, count=4),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=3),
        ],
        shocks=[
            ShockConfig(shock_id="pump", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=40),
                        side="buy", magnitude=0.8, duration=80),
            ShockConfig(shock_id="dump", shock_type=ShockType.WHALE_ORDER,
                        trigger=ShockTrigger(kind="scheduled", step=shock_step),
                        side="sell", magnitude=whale_magnitude, duration=10),
        ],
        risk=RiskPolicy(enabled=True, initial_margin=initial_margin,
                        maintenance_margin=maintenance_margin,
                        liquidation_delay=2, liquidation_fraction=0.25),
        total_steps=total_steps,
    )


def leverage_liquidity_grid(experiment_id: str = "stress_lev_liq",
                            replications: int = 20,
                            margin_levels: Optional[List[float]] = None,
                            mm_levels: Optional[List[int]] = None,
                            root_seed: Optional[int] = None) -> ExperimentVersion:
    """The canonical F8 phase experiment: leverage cap x liquidity.

    Lower initial margin permits higher leverage (~1/initial_margin);
    fewer market makers thin the book. The hypothesis under test is that
    forced-liquidation severity is superadditive in high leverage x thin
    liquidity.
    """
    margin_levels = margin_levels or [0.5, 0.1]     # ~2x vs ~10x leverage cap
    mm_levels = mm_levels or [1, 3]
    if len(margin_levels) != 2 or len(mm_levels) != 2:
        raise ValueError("the canonical grid is 2x2; build a custom "
                         "DesignSpec for other shapes")
    design = DesignSpec(
        design_type="factorial",
        question=("Does the leverage cap interact with market-maker "
                  "liquidity in liquidation-cascade severity?"),
        hypothesis=("Forced volume and drawdown are superadditive in "
                    "high leverage x thin liquidity."),
        independent_variables=["risk.initial_margin", "agents.4.count"],
        dependent_variables=["max_drawdown", "risk_forced_volume",
                             "risk_liquidation_slices", "risk_margin_calls",
                             "risk_max_leverage", "total_return"],
        primary_metric="risk_forced_volume",
        factors=[
            Factor(name="im", path="risk.initial_margin", levels=margin_levels),
            Factor(name="mm", path="agents.4.count", levels=mm_levels),
        ],
        replications=replications,
    )
    # Maintenance must stay below the smallest initial margin level.
    min_im = min(margin_levels)
    base = stressed_base_config(initial_margin=max(margin_levels),
                                maintenance_margin=min_im / 2)
    return ExperimentVersion(experiment_id, "leverage-x-liquidity", base,
                             design, root_seed=root_seed)
