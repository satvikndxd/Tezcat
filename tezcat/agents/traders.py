"""The five MVP agent types."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from tezcat.agents.base import AgentRegistry, BaseAgent, MarketView, OrderIntent


def _cancel_intents(agent: BaseAgent, view: MarketView, rng: random.Random) -> List[OrderIntent]:
    """Regime-driven cancellations of stale resting orders."""
    out = []
    p = view.modifiers.cancel_probability
    if p <= 0:
        return out
    for oid in list(agent.open_order_ids):
        if rng.random() < p:
            out.append(OrderIntent(order_type="cancel", cancel_order_id=oid))
    return out


@AgentRegistry.register("noise_trader")
@dataclass
class NoiseTrader(BaseAgent):
    """Random side, size, and aggressiveness. Provides baseline order flow."""

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:
        intents = _cancel_intents(self, view, rng)
        if not self.participates(view, rng):
            return intents
        side = "buy" if rng.random() < 0.5 + 0.1 * view.sentiment else "sell"
        qty = self.size_order(int(self.params.get("base_size", 5)), view, rng)
        ref = view.ref_price()
        aggression = 0.15 * view.modifiers.aggression_multiplier
        if rng.random() < aggression:
            intents.append(OrderIntent(side=side, order_type="market", quantity=qty))
        else:
            offset_ticks = rng.randint(0, 4)
            price = ref - offset_ticks * view.tick_size if side == "buy" else ref + offset_ticks * view.tick_size
            intents.append(OrderIntent(side=side, order_type="limit", price=price, quantity=qty))
        return intents


@AgentRegistry.register("retail_trader")
@dataclass
class RetailTrader(BaseAgent):
    """Sentiment- and herd-driven flow; panics in crises."""

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:
        intents = _cancel_intents(self, view, rng)
        if not self.participates(view, rng):
            return intents

        herding = float(self.params.get("herding", 0.5))
        buy_prop = 0.5
        buy_prop += 0.30 * view.sentiment
        buy_prop += 6.0 * self.memory.trend_belief          # chases trends
        buy_prop += herding * 0.25 * view.order_imbalance    # follows the crowd
        buy_prop -= 0.35 * self.memory.fear

        qty = self.size_order(int(self.params.get("base_size", 6)), view, rng)
        ref = view.ref_price()

        # Panic selling under crisis + fear, or under strongly negative sentiment.
        panicking = (view.regime == "crisis" and self.memory.fear > 0.45) or view.sentiment < -0.55
        if panicking and self.portfolio.available_inventory > 0:
            if rng.random() < 0.5 * view.modifiers.aggression_multiplier:
                dump = min(self.portfolio.available_inventory, qty * 2)
                intents.append(OrderIntent(side="sell", order_type="market", quantity=dump))
                return intents

        side = "buy" if rng.random() < buy_prop else "sell"
        aggressive = rng.random() < 0.25 * view.modifiers.aggression_multiplier
        if aggressive:
            intents.append(OrderIntent(side=side, order_type="market", quantity=qty))
        else:
            off = rng.randint(0, 3) * view.tick_size
            price = ref - off if side == "buy" else ref + off
            intents.append(OrderIntent(side=side, order_type="limit", price=price, quantity=qty))
        return intents


@AgentRegistry.register("momentum_trader")
@dataclass
class MomentumTrader(BaseAgent):
    """Buys strength, sells weakness."""

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:
        intents = _cancel_intents(self, view, rng)
        if not self.participates(view, rng):
            return intents

        threshold = float(self.params.get("threshold", 0.0015))
        signal = 0.6 * view.recent_return + 0.4 * self.memory.trend_belief * 5.0
        if abs(signal) < threshold:
            return intents

        side = "buy" if signal > 0 else "sell"
        strength = min(3.0, abs(signal) / threshold)
        qty = self.size_order(int(self.params.get("base_size", 6) * strength), view, rng)
        if rng.random() < 0.45 * view.modifiers.aggression_multiplier:
            intents.append(OrderIntent(side=side, order_type="market", quantity=qty))
        else:
            ref = view.ref_price()
            # Cross toward the touch: momentum traders pay up.
            price = ref + view.tick_size if side == "buy" else ref - view.tick_size
            intents.append(OrderIntent(side=side, order_type="limit", price=price, quantity=qty))
        return intents


@AgentRegistry.register("mean_reversion_trader")
@dataclass
class MeanReversionTrader(BaseAgent):
    """Trades against deviation from a slow-moving value anchor."""

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:
        intents = _cancel_intents(self, view, rng)
        if not self.participates(view, rng):
            return intents

        w = float(self.params.get("fundamental_weight", 0.5))
        fundamental = view.fundamental_price or view.last_price
        anchor = w * fundamental + (1.0 - w) * (self.memory.value_belief or view.last_price)
        band = float(self.params.get("band", 0.01))
        dev = (view.last_price - anchor) / anchor
        if abs(dev) < band:
            return intents

        side = "sell" if dev > 0 else "buy"
        strength = min(6.0, abs(dev) / band)
        qty = self.size_order(int(self.params.get("base_size", 5) * strength), view, rng)
        ref = view.ref_price()
        if strength > 2.5 and rng.random() < 0.35 * view.modifiers.aggression_multiplier:
            intents.append(OrderIntent(side=side, order_type="market", quantity=qty))
        else:
            # Passive: lean against the move.
            off = rng.randint(0, 2) * view.tick_size
            price = ref - off if side == "buy" else ref + off
            intents.append(OrderIntent(side=side, order_type="limit", price=price, quantity=qty))
        return intents


@AgentRegistry.register("market_maker")
@dataclass
class MarketMaker(BaseAgent):
    """Quotes both sides around mid, skews for inventory, widens in stress."""

    def decide(self, view: MarketView, rng: random.Random) -> List[OrderIntent]:
        # Always start by cancelling previous quotes.
        intents = [OrderIntent(order_type="cancel", cancel_order_id=oid) for oid in self.open_order_ids]

        if view.mm_withdrawn:
            return intents  # withdrawal shock: pull all quotes, do not requote

        ref = view.ref_price()
        base_half = float(self.params.get("half_spread", 2.0)) * view.tick_size
        vol_widen = 1.0 + min(4.0, 220.0 * view.volatility)
        fear_widen = 1.0 + 1.5 * self.memory.fear
        half = base_half * view.modifiers.quote_spread_multiplier * vol_widen * fear_widen
        half = max(view.tick_size, half)

        # Inventory skew: long inventory -> shade quotes down to sell it off.
        target = int(self.params.get("inventory_target", self.portfolio.initial_inventory))
        imbalance = self.portfolio.inventory - target
        skew = -imbalance * float(self.params.get("skew_per_unit", 0.002)) * view.tick_size
        mid = ref + skew

        size = self.size_order(int(self.params.get("quote_size", 12)), view, rng)
        bid_px = mid - half
        ask_px = mid + half
        if bid_px > 0:
            intents.append(OrderIntent(side="buy", order_type="limit", price=bid_px, quantity=size))
        intents.append(OrderIntent(side="sell", order_type="limit", price=ask_px, quantity=size))
        return intents
