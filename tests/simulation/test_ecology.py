"""Simulation-level validation: determinism, invariants, preset behavior."""

import pytest

from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.presets import build_config


def _run(preset, seed=42, steps=None):
    cfg = build_config(preset)
    if steps:
        payload = cfg.model_dump(mode="json")
        payload["total_steps"] = steps
        payload["shocks"] = [s for s in payload["shocks"]
                             if (s["trigger"].get("step") or 0) < steps]
        from tezcat.core.config import ExperimentConfig
        cfg = ExperimentConfig.model_validate(payload)
    eng = EcologyEngine(f"test_{preset}_{seed}", cfg, seed)
    while not eng.done:
        eng.step()
    eng.check_invariants()
    return eng


def test_determinism_same_seed():
    a = _run("stable_baseline", seed=7, steps=300)
    b = _run("stable_baseline", seed=7, steps=300)
    assert [s["last_price"] for s in a.snapshots] == [s["last_price"] for s in b.snapshots]
    assert len(a.trades) == len(b.trades)


def test_different_seed_differs():
    a = _run("stable_baseline", seed=1, steps=300)
    b = _run("stable_baseline", seed=2, steps=300)
    assert [s["last_price"] for s in a.snapshots] != [s["last_price"] for s in b.snapshots]


def test_trades_occur_and_invariants_hold():
    eng = _run("stable_baseline", steps=300)
    assert len(eng.trades) > 100
    assert all(t["quantity"] > 0 for t in eng.trades)
    assert all(t["price"] > 0 for t in eng.trades)


def test_stable_preset_is_calm():
    eng = _run("stable_baseline")
    rep = eng.build_report()
    assert rep["max_drawdown"] < 0.05
    assert not rep["crash_detected"]


@pytest.mark.slow
def test_flash_crash_preset_crashes_and_recovers():
    eng = _run("flash_crash")
    rep = eng.build_report()
    assert rep["max_drawdown"] > 0.10, "flash crash preset should produce a sharp drawdown"
    assert rep["crash_detected"]
    assert rep["liquidity_crisis_detected"]
    assert any(e.new_regime == "crisis" for e in eng.regimes.events)
    assert any(e.new_regime == "recovery" for e in eng.regimes.events)
    # whale shock fired and moved the market
    whale_events = [e for e in eng.shocks.events if e.shock_type == "whale_order"]
    assert whale_events
    ev = whale_events[0]
    assert ev.market_after["last_price"] < ev.market_before["last_price"]


@pytest.mark.slow
def test_bubble_preset_overextends():
    eng = _run("bubble_formation")
    rep = eng.build_report()
    prices = [s["last_price"] for s in eng.snapshots]
    assert max(prices) > 115.0, "bubble should overextend upward"
    assert max(prices) - rep["final_price"] > 5.0, "bubble should deflate from its peak"


def test_manual_shock_injection_mid_run():
    cfg = build_config("stable_baseline")
    eng = EcologyEngine("manual", cfg, 42)
    for _ in range(200):
        eng.step()
    price_before = eng.last_price
    eng.shocks.inject_manual("whale_order", "sell", 1200, 20)
    for _ in range(60):
        eng.step()
    assert any(e.trigger_reason == "manual" for e in eng.shocks.events)
    assert eng.last_price < price_before


def test_mm_withdrawal_widens_spread():
    cfg = build_config("stable_baseline")
    eng = EcologyEngine("mmw", cfg, 42)
    for _ in range(200):
        eng.step()
    spreads_before = [s["spread"] for s in eng.snapshots[-50:] if s["spread"]]
    eng.shocks.inject_manual("mm_withdrawal", None, 1.0, 60)
    for _ in range(60):
        eng.step()
    spreads_after = [s["spread"] for s in eng.snapshots[-40:] if s["spread"]]
    assert sum(spreads_after) / len(spreads_after) > sum(spreads_before) / len(spreads_before)


def test_memory_serializes_and_reacts():
    eng = _run("flash_crash", steps=1000)
    agent = eng.agents[0]
    d = agent.memory.to_dict()
    assert set(d) >= {"confidence", "fear", "trend_belief", "volatility_estimate"}
    # someone should have felt fear during the crash
    assert any(a.memory.fear > 0.1 for a in eng.agents)
