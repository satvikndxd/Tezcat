"""Reference strategy library for the Strategy Lab (Phase S4).

A small set of deliberately simple, parameterized strategies with
**hashed identities**: ``strategy_hash`` covers the library version, the
strategy id, and the exact parameters, so "the same strategy across
different worlds" is a verifiable claim, not a filename convention.

These are research probes, not trading advice. They exist so the lab can
measure how a *fixed* participant behaves across *changing* market
ecologies — the strategy is the control variable.

Nautilus imports live inside ``build_strategy`` so the Tezcat kernel never
depends on the optional bridge dependency.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json

STRATEGY_LIBRARY_VERSION = 1


class LabError(ValueError):
    pass


STRATEGY_SPECS: Dict[str, Dict[str, Any]] = {
    "buy_hold": {
        "description": "Enter once at the first quote, hold, close at end. "
                       "The minimal execution baseline.",
        "params": {"trade_size": 100},
    },
    "ema_cross": {
        "description": "Long when the fast EMA of the quote mid is above "
                       "the slow EMA; flat otherwise. Market orders.",
        "params": {"fast": 10, "slow": 40, "trade_size": 100},
    },
    "mean_reversion": {
        "description": "Buy below the rolling-mean band, exit above it. "
                       "Market orders; long/flat only (cash account).",
        "params": {"window": 30, "band": 0.004, "trade_size": 100},
    },
}


def list_strategies() -> List[Dict[str, Any]]:
    return [{"strategy_id": sid,
             "description": spec["description"],
             "default_params": dict(spec["params"]),
             "library_version": STRATEGY_LIBRARY_VERSION}
            for sid, spec in sorted(STRATEGY_SPECS.items())]


def resolve_params(strategy_id: str,
                   params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if strategy_id not in STRATEGY_SPECS:
        raise LabError(f"unknown strategy {strategy_id!r}; "
                       f"available: {sorted(STRATEGY_SPECS)}")
    resolved = dict(STRATEGY_SPECS[strategy_id]["params"])
    for key, value in (params or {}).items():
        if key not in resolved:
            raise LabError(f"strategy {strategy_id!r} has no parameter "
                           f"{key!r}; available: {sorted(resolved)}")
        resolved[key] = type(resolved[key])(value)
    return resolved


def strategy_hash(strategy_id: str, params: Dict[str, Any]) -> str:
    payload = {"library_version": STRATEGY_LIBRARY_VERSION,
               "strategy_id": strategy_id, "params": params}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Nautilus strategy construction (lazy import)
# ---------------------------------------------------------------------------
def build_strategy(strategy_id: str, params: Dict[str, Any],
                   instrument_id: Any):
    """Instantiate the Nautilus Strategy object for a library entry.

    Every strategy records its own order submissions and fills into
    ``submitted`` / ``fills`` lists — the lab's metrics are computed from
    these deterministic records (plus the world stream), never scraped
    from rendered reports.
    """
    try:
        from nautilus_trader.config import StrategyConfig
        from nautilus_trader.model.enums import OrderSide
        from nautilus_trader.model.objects import Quantity
        from nautilus_trader.trading.strategy import Strategy
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise LabError(
            "nautilus_trader is not installed; the Strategy Lab is an "
            "optional bridge — install with `pip install nautilus_trader`"
        ) from exc

    class _LabConfig(StrategyConfig, frozen=True):
        instrument_id: object
        params: dict

    class _LabStrategy(Strategy):
        """Base: subscribes to quotes, records submissions and fills."""

        def __init__(self, config):
            super().__init__(config)
            self.p = dict(config.params)
            self.submitted: List[Dict[str, Any]] = []
            self.fills: List[Dict[str, Any]] = []

        def on_start(self):
            self.subscribe_quote_ticks(self.config.instrument_id)

        def on_order_filled(self, event):
            self.fills.append({
                "ts_ns": int(event.ts_event),
                "side": "BUY" if event.order_side == OrderSide.BUY else "SELL",
                "quantity": float(event.last_qty),
                "price": float(event.last_px),
            })

        # -- helpers ---------------------------------------------------
        def _inventory(self) -> float:
            bought = sum(f["quantity"] for f in self.fills if f["side"] == "BUY")
            sold = sum(f["quantity"] for f in self.fills if f["side"] == "SELL")
            return bought - sold

        def _market(self, side, qty: int):
            order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=Quantity.from_int(qty))
            self.submitted.append({"side": "BUY" if side == OrderSide.BUY
                                   else "SELL", "quantity": qty})
            self.submit_order(order)

        def _close_all(self):
            inv = int(self._inventory())
            if inv > 0:
                self._market(OrderSide.SELL, inv)

    if strategy_id == "buy_hold":
        class BuyHold(_LabStrategy):
            def __init__(self, config):
                super().__init__(config)
                self.entered = False

            def on_quote_tick(self, tick):
                if not self.entered:
                    self.entered = True
                    self._market(OrderSide.BUY, self.p["trade_size"])

            def on_stop(self):
                self._close_all()

        cls = BuyHold

    elif strategy_id == "ema_cross":
        class EmaCross(_LabStrategy):
            def __init__(self, config):
                super().__init__(config)
                self.fast_ema: Optional[float] = None
                self.slow_ema: Optional[float] = None
                self.n = 0

            def on_quote_tick(self, tick):
                mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
                kf = 2 / (self.p["fast"] + 1)
                ks = 2 / (self.p["slow"] + 1)
                self.fast_ema = mid if self.fast_ema is None else \
                    mid * kf + self.fast_ema * (1 - kf)
                self.slow_ema = mid if self.slow_ema is None else \
                    mid * ks + self.slow_ema * (1 - ks)
                self.n += 1
                if self.n < self.p["slow"]:
                    return
                inv = self._inventory()
                if self.fast_ema > self.slow_ema and inv == 0:
                    self._market(OrderSide.BUY, self.p["trade_size"])
                elif self.fast_ema < self.slow_ema and inv > 0:
                    self._market(OrderSide.SELL, int(inv))

            def on_stop(self):
                self._close_all()

        cls = EmaCross

    elif strategy_id == "mean_reversion":
        class MeanReversion(_LabStrategy):
            def __init__(self, config):
                super().__init__(config)
                self.window: List[float] = []

            def on_quote_tick(self, tick):
                mid = (float(tick.bid_price) + float(tick.ask_price)) / 2
                self.window.append(mid)
                if len(self.window) > self.p["window"]:
                    self.window.pop(0)
                if len(self.window) < self.p["window"]:
                    return
                mean = sum(self.window) / len(self.window)
                inv = self._inventory()
                if mid < mean * (1 - self.p["band"]) and inv == 0:
                    self._market(OrderSide.BUY, self.p["trade_size"])
                elif mid > mean * (1 + self.p["band"]) and inv > 0:
                    self._market(OrderSide.SELL, int(inv))

            def on_stop(self):
                self._close_all()

        cls = MeanReversion

    else:  # pragma: no cover - guarded by resolve_params
        raise LabError(f"unknown strategy {strategy_id!r}")

    return cls(_LabConfig(instrument_id=instrument_id, params=params))
