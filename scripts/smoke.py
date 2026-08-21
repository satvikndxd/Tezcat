"""Headless smoke test: run each preset, print summary stats."""

import sys
import time

sys.path.insert(0, ".")

from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.presets import PRESETS, build_config


def run_preset(pid: str, seed: int = 42):
    cfg = build_config(pid)
    eng = EcologyEngine(f"smoke_{pid}", cfg, seed)
    t0 = time.time()
    while not eng.done:
        eng.step()
        if eng.step_num % 500 == 0:
            eng.check_invariants()
    eng.check_invariants()
    dt = time.time() - t0
    rep = eng.build_report()
    prices = [s["last_price"] for s in eng.snapshots]
    print(f"\n=== {pid} (seed={seed}) — {cfg.total_steps} steps in {dt:.1f}s ===")
    print(f"  price: start=100.00 min={min(prices):.2f} max={max(prices):.2f} final={rep['final_price']:.2f}")
    print(f"  return={rep['total_return']:+.2%} maxDD={rep['max_drawdown']:.2%} vol={rep['realized_volatility']:.5f}")
    print(f"  avg_spread={rep['average_spread']:.3f} volume={rep['total_volume']} trades={rep['total_trades']}")
    print(f"  crash={rep['crash_detected']} liq_crisis={rep['liquidity_crisis_detected']} "
          f"spread_mult={rep['max_spread_mult']} depth_frac={rep['min_depth_frac']}")
    print(f"  regimes={rep['regime_step_share']} transitions={rep['regime_transition_count']} shocks={rep['shock_count']}")
    for t, d in rep["agent_pnl_by_type"].items():
        print(f"    {t:<24} n={d['agents']:<3} total_pnl={d['total_pnl']:>10.2f}")
    return rep


def determinism_check(pid: str = "flash_crash"):
    cfg = build_config(pid)
    a = EcologyEngine("det_a", cfg, 7)
    b = EcologyEngine("det_b", cfg, 7)
    while not a.done:
        a.step()
    while not b.done:
        b.step()
    pa = [s["last_price"] for s in a.snapshots]
    pb = [s["last_price"] for s in b.snapshots]
    assert pa == pb, "determinism violated!"
    # F2 contract: full event/state hash equality, not just price paths.
    # (run_id differs between a and b, so compare hashes that exclude it.)
    assert a.state_hash() == b.state_hash(), "state hash determinism violated!"
    c = EcologyEngine("det_c", cfg, 8)
    while not c.done:
        c.step()
    pc = [s["last_price"] for s in c.snapshots]
    assert a.state_hash() != c.state_hash(), "different seeds must diverge!"
    print(f"\ndeterminism: same seed identical = True; diff seed differs = {pa != pc}")


if __name__ == "__main__":
    for pid in PRESETS:
        run_preset(pid)
    determinism_check()
