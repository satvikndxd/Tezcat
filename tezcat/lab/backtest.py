"""Nautilus backtest runner + deterministic metric derivation (Phase S4).

``run_backtest`` replays a Market World through Nautilus' BacktestEngine
with one library strategy, then derives every reported metric from
**deterministic primary records** — the strategy's own submission/fill
log plus the world's quote stream — never by scraping rendered reports.
Nautilus' own summary statistics are attached verbatim under
``nautilus_stats`` for cross-reference.

Metric definitions (all artifact-backed):

* equity curve: mark-to-market at every world quote —
  ``cash + inventory × mid``; return/drawdown derive from it.
* turnover: Σ |fill qty × fill price|.
* fill rate: filled submissions / submissions.
* slippage per fill: signed cost vs the prevailing world mid at the fill
  timestamp (buy: fill − mid; sell: mid − fill). With L1 quotes this is
  dominated by the half-spread — which is exactly the execution cost the
  ecology imposes.
* regime breakdown: every equity change and every fill is attributed to
  the world's regime interval covering its timestamp (Tezcat's own regime
  detection, exported as annotations — not reconstructed after the fact).

Determinism boundary (declared, §27): same world + same strategy config +
same ``nautilus_trader`` version + same platform ⇒ same result. The
result records the environment; reproduction fails loudly on mismatch.
"""

from __future__ import annotations

import bisect
import platform
from typing import Any, Dict, List, Optional

from tezcat.lab.nautilus_export import export_to_nautilus
from tezcat.lab.strategies import (
    LabError, build_strategy, resolve_params, strategy_hash,
)
from tezcat.worlds.world import MarketWorld

LAB_SCHEMA_VERSION = 1


def _drawdown(equity: List[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def run_backtest(world: MarketWorld, strategy_id: str,
                 params: Optional[Dict[str, Any]] = None,
                 starting_cash: float = 1_000_000.0) -> Dict[str, Any]:
    """Run one strategy against one world; return the full result payload."""
    try:
        import nautilus_trader
        from nautilus_trader.backtest.engine import (
            BacktestEngine, BacktestEngineConfig,
        )
        from nautilus_trader.config import LoggingConfig
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import TraderId, Venue
        from nautilus_trader.model.objects import Money
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise LabError(
            "nautilus_trader is not installed; install it to run Strategy "
            "Lab backtests") from exc

    resolved = resolve_params(strategy_id, params)
    instrument, ticks = export_to_nautilus(world)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("TEZCAT-LAB-001"),
        logging=LoggingConfig(log_level="ERROR")))
    try:
        engine.add_venue(venue=Venue("TEZCAT"), oms_type=OmsType.NETTING,
                         account_type=AccountType.CASH, base_currency=USD,
                         starting_balances=[Money(starting_cash, USD)])
        engine.add_instrument(instrument)
        engine.add_data(ticks)
        strategy = build_strategy(strategy_id, resolved, instrument.id)
        engine.add_strategy(strategy)
        engine.run()
        fills = list(strategy.fills)
        submissions = list(strategy.submitted)
        nautilus_stats = {
            "pnls": engine.get_result().stats_pnls,
            "returns": {k: (None if v != v else v)  # NaN → null for JSON
                        for k, v in engine.get_result().stats_returns.items()},
        }
    finally:
        engine.dispose()

    return {
        "lab_schema_version": LAB_SCHEMA_VERSION,
        "world": {
            "world_id": world.world_id,
            "world_hash": world.world_hash,
            "research_hash": world.manifest["research_hash"],
            "version_id": world.manifest["version_id"],
            "cell": world.manifest["cell"],
            "replication": world.manifest["replication"],
            "seed": world.manifest["seed"],
            "external_context": world.manifest.get("external_context"),
        },
        "strategy": {
            "strategy_id": strategy_id,
            "params": resolved,
            "strategy_hash": strategy_hash(strategy_id, resolved),
        },
        "backtest_config": {"starting_cash": starting_cash,
                            "venue": "TEZCAT",
                            "account_type": "CASH",
                            "oms_type": "NETTING"},
        "environment": {
            "nautilus_trader_version": nautilus_trader.__version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "metrics": derive_metrics(world, fills, submissions, starting_cash),
        "nautilus_stats": nautilus_stats,
    }


# ---------------------------------------------------------------------------
# Deterministic metric derivation
# ---------------------------------------------------------------------------
def derive_metrics(world: MarketWorld, fills: List[Dict[str, Any]],
                   submissions: List[Dict[str, Any]],
                   starting_cash: float) -> Dict[str, Any]:
    quote_ts = [q["ts_ns"] for q in world.quotes]
    mids = [(q["bid"] + q["ask"]) / 2 for q in world.quotes]

    def mid_at(ts_ns: int) -> Optional[float]:
        i = bisect.bisect_right(quote_ts, ts_ns) - 1
        return mids[i] if i >= 0 else None

    # -- fill-level records --------------------------------------------
    fill_rows = []
    for f in sorted(fills, key=lambda x: x["ts_ns"]):
        mid = mid_at(f["ts_ns"])
        slippage = None
        if mid is not None:
            slippage = (f["price"] - mid) if f["side"] == "BUY" else (mid - f["price"])
        fill_rows.append({**f, "mid_at_fill": mid, "slippage_vs_mid": slippage,
                          "regime": world.regime_at(f["ts_ns"])})

    turnover = sum(f["quantity"] * f["price"] for f in fill_rows)
    slippages = [f["slippage_vs_mid"] for f in fill_rows
                 if f["slippage_vs_mid"] is not None]
    slippage_cost = sum(s * f["quantity"] for s, f in
                        zip(slippages, [f for f in fill_rows
                                        if f["slippage_vs_mid"] is not None]))

    # -- mark-to-market equity curve over world quotes -----------------
    cash, inventory = starting_cash, 0.0
    fill_idx = 0
    equity: List[float] = []
    exposures: List[float] = []
    regime_pnl: Dict[str, float] = {}
    prev_equity = starting_cash
    for q, mid in zip(world.quotes, mids):
        while fill_idx < len(fill_rows) and fill_rows[fill_idx]["ts_ns"] <= q["ts_ns"]:
            f = fill_rows[fill_idx]
            sign = 1.0 if f["side"] == "BUY" else -1.0
            cash -= sign * f["quantity"] * f["price"]
            inventory += sign * f["quantity"]
            fill_idx += 1
        eq = cash + inventory * mid
        regime = world.regime_at(q["ts_ns"])
        regime_pnl[regime] = regime_pnl.get(regime, 0.0) + (eq - prev_equity)
        prev_equity = eq
        equity.append(eq)
        exposures.append(abs(inventory) * mid)

    # residual inventory marked at the final mid (open position at end)
    final_equity = equity[-1] if equity else starting_cash

    # -- regime breakdown ----------------------------------------------
    regimes = sorted({iv["regime"]
                      for iv in world.annotations["regime_intervals"]})
    breakdown = {}
    for regime in regimes:
        r_fills = [f for f in fill_rows if f["regime"] == regime]
        r_slip = [f["slippage_vs_mid"] for f in r_fills
                  if f["slippage_vs_mid"] is not None]
        breakdown[regime] = {
            "pnl": round(regime_pnl.get(regime, 0.0), 6),
            "n_fills": len(r_fills),
            "turnover": round(sum(f["quantity"] * f["price"]
                                  for f in r_fills), 6),
            "mean_slippage_vs_mid": (sum(r_slip) / len(r_slip)
                                     if r_slip else None),
        }

    n_submitted = len(submissions)
    return {
        "starting_cash": starting_cash,
        "final_equity": round(final_equity, 6),
        "total_pnl": round(final_equity - starting_cash, 6),
        "total_return": round(final_equity / starting_cash - 1.0, 8),
        "max_drawdown": round(_drawdown(equity), 8),
        "turnover": round(turnover, 6),
        "n_orders_submitted": n_submitted,
        "n_fills": len(fill_rows),
        "fill_rate": (len(fill_rows) / n_submitted) if n_submitted else None,
        "mean_slippage_vs_mid": (sum(slippages) / len(slippages)
                                 if slippages else None),
        "total_slippage_cost": round(slippage_cost, 6),
        "max_exposure": round(max(exposures), 6) if exposures else 0.0,
        "mean_exposure": (round(sum(exposures) / len(exposures), 6)
                          if exposures else 0.0),
        "residual_inventory": inventory,
        "regime_breakdown": breakdown,
        "n_equity_points": len(equity),
        "metric_definitions": "see tezcat/lab/backtest.py — all metrics "
                              "derive from the strategy's own fill log and "
                              "the world quote stream",
    }
