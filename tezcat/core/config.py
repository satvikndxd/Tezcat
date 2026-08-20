"""Experiment configuration models.

All configuration is expressed as pydantic models so it validates on input,
serializes deterministically, and can be hashed for reproducibility.

Configuration models are **frozen** (immutable after construction). To vary a
config, build a new one (``model_copy(update=...)`` or re-validate a mutated
dump). This guarantees a stored ``config_hash`` cannot silently go stale.
Note: pydantic freezing prevents attribute assignment; contents of mutable
containers (e.g. ``RegimePolicy.modifiers``) must not be mutated by callers.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Version of the simulation/config schema. Included in every config hash and
#: artifact manifest; bump on any change that alters serialized semantics.
SCHEMA_VERSION = 1


class FrozenModel(BaseModel):
    """Base for all config models: immutable after validation."""

    model_config = ConfigDict(frozen=True)


class AgentType(str, Enum):
    NOISE_TRADER = "noise_trader"
    RETAIL_TRADER = "retail_trader"
    MOMENTUM_TRADER = "momentum_trader"
    MEAN_REVERSION_TRADER = "mean_reversion_trader"
    MARKET_MAKER = "market_maker"


class ShockType(str, Enum):
    WHALE_ORDER = "whale_order"
    MM_WITHDRAWAL = "mm_withdrawal"
    SENTIMENT_SHOCK = "sentiment_shock"


class Regime(str, Enum):
    STABLE = "stable"
    CRISIS = "crisis"
    RECOVERY = "recovery"


class MarketConfig(FrozenModel):
    symbol: str = "TZC"
    initial_price: float = Field(100.0, gt=0)
    tick_size: float = Field(0.05, gt=0)
    min_order_size: int = Field(1, ge=1)
    max_order_size: int = Field(500, ge=1)
    max_order_age: int = Field(40, ge=1, description="Steps before a resting order expires")
    allow_short: bool = False
    allow_negative_cash: bool = False
    self_trade_policy: Literal["allow", "cancel_resting"] = Field(
        "allow",
        description=(
            "Policy when an incoming order would match an agent's own resting "
            "order. 'allow' preserves the legacy baseline semantics (self-trades "
            "settle against the same portfolio; accounting nets to zero cash/"
            "inventory but perturbs realized PnL and avg cost). 'cancel_resting' "
            "is standard self-trade prevention: the resting order is cancelled, "
            "its reservations released, and matching continues at the next level."
        ),
    )

    @model_validator(mode="after")
    def _check_sizes(self) -> "MarketConfig":
        if self.max_order_size < self.min_order_size:
            raise ValueError("max_order_size must be >= min_order_size")
        return self


class AgentGroupConfig(FrozenModel):
    """A homogeneous group of agents of one type."""

    agent_type: AgentType
    count: int = Field(..., ge=1, le=500)
    cash: float = Field(10_000.0, ge=0)
    inventory: int = Field(100, ge=0)
    risk_tolerance: float = Field(0.5, ge=0, le=1)
    trading_frequency: float = Field(0.5, ge=0, le=1, description="Per-step participation probability")
    params: Dict[str, Any] = Field(default_factory=dict, description="Strategy-specific parameters")


class ShockTrigger(FrozenModel):
    kind: Literal["scheduled", "manual"] = "scheduled"
    step: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _check_step(self) -> "ShockTrigger":
        if self.kind == "scheduled" and self.step is None:
            raise ValueError("scheduled trigger requires a step")
        return self


class ShockConfig(FrozenModel):
    shock_id: str
    shock_type: ShockType
    trigger: ShockTrigger
    side: Optional[Literal["buy", "sell"]] = None
    magnitude: float = Field(..., gt=0)
    duration: int = Field(1, ge=1)
    enabled: bool = True
    description: str = ""


class RegimeModifiers(FrozenModel):
    aggression_multiplier: float = Field(1.0, gt=0)
    risk_tolerance_multiplier: float = Field(1.0, gt=0)
    quote_spread_multiplier: float = Field(1.0, gt=0)
    participation_multiplier: float = Field(1.0, gt=0)
    cancel_probability: float = Field(0.0, ge=0, le=1)


class RegimePolicy(FrozenModel):
    enabled: bool = True
    evaluation_interval: int = Field(5, ge=1)
    persistence_required: int = Field(3, ge=1, description="Consecutive confirmations before transition")
    crisis_drawdown: float = Field(0.06, gt=0, description="Rolling drawdown that signals crisis")
    crisis_spread_mult: float = Field(3.0, gt=1, description="Spread vs baseline that signals crisis")
    crisis_depth_frac: float = Field(0.3, gt=0, lt=1, description="Depth vs baseline that signals crisis")
    recovery_vol_frac: float = Field(0.8, gt=0, description="Vol vs crisis-peak vol that signals recovery")
    stable_after: int = Field(60, ge=1, description="Calm steps in recovery before returning to stable")
    lookback: int = Field(80, ge=10)
    modifiers: Dict[Regime, RegimeModifiers] = Field(
        default_factory=lambda: {
            Regime.STABLE: RegimeModifiers(),
            Regime.CRISIS: RegimeModifiers(
                aggression_multiplier=1.6,
                risk_tolerance_multiplier=0.5,
                quote_spread_multiplier=2.5,
                participation_multiplier=1.3,
                cancel_probability=0.25,
            ),
            Regime.RECOVERY: RegimeModifiers(
                aggression_multiplier=0.8,
                risk_tolerance_multiplier=0.8,
                quote_spread_multiplier=1.4,
                participation_multiplier=0.9,
                cancel_probability=0.05,
            ),
        }
    )


class MetricsPolicy(FrozenModel):
    vol_window: int = Field(20, ge=2)
    return_window: int = Field(10, ge=1)
    crash_drawdown_threshold: float = Field(0.15, gt=0)
    liquidity_spread_mult: float = Field(5.0, gt=1)
    compute_hhi: bool = True


class ExperimentConfig(FrozenModel):
    market: MarketConfig = Field(default_factory=MarketConfig)
    agents: List[AgentGroupConfig]
    shocks: List[ShockConfig] = Field(default_factory=list)
    regime_policy: RegimePolicy = Field(default_factory=RegimePolicy)
    metrics_policy: MetricsPolicy = Field(default_factory=MetricsPolicy)
    total_steps: int = Field(1500, ge=10, le=100_000)
    seed_policy: Literal["fixed", "random"] = "fixed"
    default_seed: int = Field(42, ge=0)
    step_delay_ms: int = Field(15, ge=0, le=1000, description="Pacing delay for live dashboard viewing")

    @field_validator("agents")
    @classmethod
    def _at_least_one_agent(cls, v: List[AgentGroupConfig]) -> List[AgentGroupConfig]:
        if not v:
            raise ValueError("agent population must not be empty")
        return v

    @model_validator(mode="after")
    def _check_shock_steps(self) -> "ExperimentConfig":
        ids = set()
        for s in self.shocks:
            if s.shock_id in ids:
                raise ValueError(f"duplicate shock_id {s.shock_id!r}")
            ids.add(s.shock_id)
            if s.trigger.kind == "scheduled" and s.trigger.step is not None and s.trigger.step >= self.total_steps:
                raise ValueError(f"shock {s.shock_id!r} scheduled at step {s.trigger.step} beyond total_steps")
        return self


def _reject_non_canonical(obj: Any) -> Any:
    raise TypeError(
        f"non-canonical value of type {type(obj).__name__} in hash payload; "
        "convert to JSON-native types before hashing"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used for hashing.

    Strict: only JSON-native types are accepted. This replaces the previous
    ``default=str`` fallback, which could silently hash unstable ``repr``s.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=_reject_non_canonical)


def config_hash(config: ExperimentConfig) -> str:
    """Content hash of a config under the current schema version.

    ``schema_version`` is part of the payload, so the same parameter values
    hash differently across schema revisions — a hash names *semantics*, not
    just numbers.
    """
    payload = {"schema_version": SCHEMA_VERSION, "config": config.model_dump(mode="json")}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
