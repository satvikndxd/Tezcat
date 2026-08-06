"""Base agent contract, market view, and order intents."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import AgentGroupConfig, RegimeModifiers
from tezcat.memory.agent_memory import AgentMemory


@dataclass
class MarketView:
    """Everything an agent may observe when deciding."""

    step: int
    last_price: float
    mid_price: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    bid_depth: int
    ask_depth: int
    recent_return: float          # return over the metrics return window
    volatility: float             # rolling volatility of log returns
    order_imbalance: float        # (bid_depth - ask_depth) / (bid_depth + ask_depth)
    regime: str
    modifiers: RegimeModifiers
    sentiment: float              # global sentiment in [-1, 1], shock-adjustable
    mm_withdrawn: bool            # market-maker withdrawal shock active
    tick_size: float
    fundamental_price: float = 0.0  # slow-moving fundamental anchor (initial price)

    def ref_price(self) -> float:
        return self.mid_price if self.mid_price is not None else self.last_price


@dataclass
class OrderIntent:
    side: Optional[str] = None            # "buy" | "sell"
    order_type: str = "limit"             # "limit" | "market" | "cancel"
    price: Optional[float] = None
    quantity: int = 0
    cancel_order_id: Optional[str] = None


@dataclass
class BaseAgent:
    agent_id: str
    agent_type: str
    portfolio: Portfolio
    risk_tolerance: float
    trading_frequency: float
    params: dict = field(default_factory=dict)
    memory: AgentMemory = field(default_factory=AgentMemory)
    open_order_ids: List[str] = field(default_factory=list)

    def observe(self, view: MarketView, log_return: float) -> None:
        equity = self.portfolio.equity(view.last_price)
        self.memory.update(view.last_price, log_return, equity, view.regime)

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:  # pragma: no cover
        raise NotImplementedError

    # -- shared helpers ------------------------------------------------
    def participates(self, view: MarketView, rng: random.Random) -> bool:
        p = self.trading_frequency * view.modifiers.participation_multiplier
        p *= 0.5 + self.memory.confidence  # cautious agents trade less
        return rng.random() < min(1.0, p)

    def size_order(self, base: int, view: MarketView, rng: random.Random) -> int:
        scale = self.risk_tolerance * view.modifiers.risk_tolerance_multiplier
        scale *= (1.0 - 0.6 * self.memory.fear)
        qty = max(1, int(round(base * scale * (0.5 + rng.random()))))
        return qty


class AgentRegistry:
    _types: dict = {}

    @classmethod
    def register(cls, agent_type: str):
        def deco(klass):
            cls._types[agent_type] = klass
            return klass
        return deco

    @classmethod
    def create(cls, agent_id: str, group: AgentGroupConfig) -> BaseAgent:
        klass = cls._types.get(group.agent_type.value)
        if klass is None:
            raise ValueError(f"unknown agent type {group.agent_type!r}")
        return klass(
            agent_id=agent_id,
            agent_type=group.agent_type.value,
            portfolio=Portfolio(cash=group.cash, inventory=group.inventory),
            risk_tolerance=group.risk_tolerance,
            trading_frequency=group.trading_frequency,
            params=dict(group.params),
        )
