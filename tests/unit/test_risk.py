"""Phase F8: margin, liquidation process, tail metrics, event reconciliation."""

import pytest

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MarketConfig, RiskPolicy,
    ShockConfig, ShockTrigger, ShockType,
)
from pydantic import ValidationError
from tezcat.engine.ecology import EcologyEngine
from tezcat.events.log import EventLog
from tezcat.events.replay import ReplayKernel
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook
from tezcat.risk.engine import RiskEngine
from tezcat.risk.metrics import tail_risk
from tezcat.risk.stress import leverage_liquidity_grid, stressed_base_config


def _policy(**kw):
    base = dict(enabled=True, initial_margin=0.5, maintenance_margin=0.25,
                liquidation_delay=2, liquidation_fraction=0.25)
    base.update(kw)
    return RiskPolicy(**base)


def _order(oid, agent, side, otype, price, qty):
    return Order(order_id=oid, agent_id=agent, side=side, order_type=otype,
                 price=price, quantity=qty, remaining=qty)


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------
def test_maintenance_must_be_below_initial():
    with pytest.raises(ValidationError, match="maintenance_margin"):
        RiskPolicy(enabled=True, initial_margin=0.2, maintenance_margin=0.3)


# ---------------------------------------------------------------------------
# Margin boundary (hand-calculated)
# ---------------------------------------------------------------------------
def test_initial_margin_boundary():
    pf = Portfolio(cash=500.0, inventory=0)
    risk = RiskEngine(_policy(), {"a": pf})
    risk.mark_price = 100.0
    # equity 500, im 0.5 -> max exposure 1000 -> max 10 shares at 100.
    ok = _order("o1", "a", "buy", "limit", 100.0, 10)
    too_much = _order("o2", "a", "buy", "limit", 100.0, 11)
    assert risk.check_order(ok, None) is None
    assert risk.check_order(too_much, None) == "initial margin exceeded"
    assert risk.max_buy_qty(pf, 100.0) == 10
    # Sells always pass (they reduce exposure).
    assert risk.check_order(_order("o3", "a", "sell", "limit", 100.0, 999), None) is None


def test_committed_reservations_count_toward_exposure():
    pf = Portfolio(cash=500.0, inventory=0)
    pf.reserve_cash(600.0)  # resting buys already commit 600 of exposure
    risk = RiskEngine(_policy(), {"a": pf})
    risk.mark_price = 100.0
    # Headroom: 500/0.5 - 600 = 400 -> 4 shares at 100.
    assert risk.max_buy_qty(pf, 100.0) == 4
    assert risk.check_order(_order("o", "a", "buy", "limit", 100.0, 5), None) \
        == "initial margin exceeded"


def test_margin_buying_beyond_cash_through_matching():
    """The actual leverage mechanic: buying with borrowed cash."""
    book = OrderBook(0.05)
    buyer = Portfolio(cash=500.0, inventory=0)
    seller = Portfolio(cash=0.0, inventory=100)
    pfs = {"a": buyer, "b": seller}
    risk = RiskEngine(_policy(), pfs)
    risk.mark_price = 100.0
    me = MatchingEngine(book, pfs, MarketConfig(), risk=risk)
    me.submit(_order("s1", "b", "sell", "limit", 100.0, 20), 1)
    trades, o = me.submit(_order("b1", "a", "buy", "market", None, 20), 1)
    # Margin cap: equity 500 / im 0.5 = 1000 notional -> 10 shares, not 20.
    assert sum(t.quantity for t in trades) == 10
    assert buyer.cash == pytest.approx(-500.0)   # borrowed half the notional
    assert buyer.inventory == 10
    # Cash is conserved even with borrowing (transfers are zero-sum).
    assert buyer.cash + seller.cash == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# The liquidation process
# ---------------------------------------------------------------------------
def test_margin_call_delay_then_partial_liquidation():
    pf = Portfolio(cash=-800.0, inventory=10)  # levered long
    events = EventLog("r")
    risk = RiskEngine(_policy(liquidation_delay=2, liquidation_fraction=0.5),
                      {"a": pf}, events=events)
    # mark 100: equity 200, pos 1000, ratio 0.2 < 0.25 -> breach at step 1.
    assert risk.evaluate(1, 100.0) == []      # margin call, clock starts
    assert risk.margin_call_count == 1
    assert events.events[-1]["type"] == "margin_call"
    assert risk.evaluate(2, 100.0) == []      # delay not yet elapsed
    forced = risk.evaluate(3, 100.0)          # delay=2 elapsed
    assert forced == [("a", 5)]               # 50% of 10 shares
    assert events.events[-1]["type"] == "liquidation"
    assert events.events[-1]["data"]["quantity"] == 5


def test_margin_restored_clears_breach():
    pf = Portfolio(cash=-800.0, inventory=10)
    events = EventLog("r")
    risk = RiskEngine(_policy(), {"a": pf}, events=events)
    risk.evaluate(1, 100.0)                   # breach (ratio 0.2)
    forced = risk.evaluate(2, 150.0)          # equity 700/pos 1500 = 0.47
    assert forced == []
    assert events.events[-1]["type"] == "margin_restored"
    # A later breach starts a fresh clock.
    risk.evaluate(3, 100.0)
    assert risk.margin_call_count == 2


def test_default_emitted_once():
    pf = Portfolio(cash=-100.0, inventory=0)
    events = EventLog("r")
    risk = RiskEngine(_policy(), {"a": pf}, events=events)
    risk.evaluate(1, 100.0)
    risk.evaluate(2, 100.0)
    defaults = [e for e in events.events if e["type"] == "default"]
    assert len(defaults) == 1
    assert defaults[0]["data"]["debt"] == pytest.approx(-100.0)
    assert risk.default_count == 1


def test_liquidation_capped_by_available_inventory():
    pf = Portfolio(cash=-800.0, inventory=10)
    pf.reserve_inventory(10)  # everything already committed to resting sells
    risk = RiskEngine(_policy(liquidation_delay=0), {"a": pf})
    assert risk.evaluate(1, 100.0) == []  # nothing available; clock persists
    pf.release_inventory(6)
    forced = risk.evaluate(2, 100.0)
    assert forced == [("a", 2)]  # 25% of 10 = 2, within the 6 available


# ---------------------------------------------------------------------------
# Tail metrics (fixtures)
# ---------------------------------------------------------------------------
def test_tail_risk_known_sample():
    losses = [float(v) for v in range(1, 101)]  # 1..100
    out = tail_risk(losses, alpha=0.95)
    assert out["var"] == pytest.approx(95.05)   # interpolated 95th percentile
    assert out["es"] == pytest.approx((96 + 97 + 98 + 99 + 100) / 5)
    assert out["n"] == 100 and out["method"] == "historical"
    assert "warning" not in out


def test_tail_risk_degenerate_and_small_samples():
    assert tail_risk([])["var"] is None
    small = tail_risk([1.0, 2.0, 3.0])
    assert "small sample" in small["warning"]
    assert small["es"] >= small["var"]


# ---------------------------------------------------------------------------
# Full-run integration: endogenous forced selling
# ---------------------------------------------------------------------------
def _risk_config(**risk_kw):
    # Full default horizon: at seed 42 the liquidation cascade develops
    # after step ~250 (the dump plants the damage; the spiral arrives later).
    return stressed_base_config(**risk_kw)


def _run(cfg, seed=42, run_id="run_risk"):
    eng = EcologyEngine(run_id, cfg, seed)
    while not eng.done:
        eng.step()
    eng.check_invariants()
    return eng


def test_stress_run_produces_forced_flow_and_reconciles():
    eng = _run(_risk_config())
    events = eng.events_log.events
    liq = [e for e in events if e["type"] == "liquidation"]
    calls = [e for e in events if e["type"] == "margin_call"]
    assert calls, "stress scenario must produce margin calls"
    assert liq, "stress scenario must produce forced liquidation"

    report = eng.build_report()
    # Report reconciles to the event ledger.
    assert report["risk_margin_calls"] == len(calls)
    assert report["risk_liquidation_slices"] == len(liq)
    assert report["risk_forced_volume"] > 0
    assert report["risk_max_leverage"] > 1.0  # leverage actually emerged

    # Forced selling is attributable: each liquidation event is followed by
    # market-sell acceptance for the same agent at the same step.
    for lev in liq[:10]:
        aid, step = lev["data"]["agent_id"], lev["step"]
        follow = [e for e in events
                  if e["type"] == "order_accepted" and e["step"] == step
                  and e["data"]["agent_id"] == aid
                  and e["data"]["side"] == "sell"
                  and e["data"]["order_type"] == "market"]
        assert follow, f"liquidation of {aid} at step {step} produced no forced order"


def test_replay_exact_with_risk_enabled():
    """The event log stays complete under margin + forced flow."""
    cfg = _risk_config()
    eng = _run(cfg)
    rk = ReplayKernel(cfg, eng.run_id)
    rk.apply_all(eng.events_log.events)
    assert rk.state_hash() == eng.state_hash()


def test_checkpoint_restore_with_risk_state():
    cfg = _risk_config()
    baseline = _run(cfg)
    eng = EcologyEngine("run_risk", cfg, 42)
    for _ in range(160):
        eng.step()
    restored = EcologyEngine.restore(eng.checkpoint(), cfg)
    while not restored.done:
        restored.step()
    assert restored.state_hash() == baseline.state_hash()
    assert restored.events_log.chain == baseline.events_log.chain
    assert restored.build_report() == baseline.build_report()


def test_risk_disabled_configs_have_no_risk_keys():
    cfg = ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=5)],
        total_steps=50)
    eng = _run(cfg, run_id="run_plain")
    report = eng.build_report()
    assert not any(k.startswith("risk_") for k in report)


# ---------------------------------------------------------------------------
# Stress Lab
# ---------------------------------------------------------------------------
def test_leverage_liquidity_grid_registers(tmp_path):
    from tezcat.experiments.registry import Registry
    ev = leverage_liquidity_grid(replications=2)
    assert ev.design.planned_runs() == 8
    assert ev.design.primary_metric == "risk_forced_volume"
    cells = [c["cell"] for c in ev.cell_configs]
    assert cells == ["im=0.5|mm=1", "im=0.5|mm=3", "im=0.1|mm=1", "im=0.1|mm=3"]
    # Cell configs really vary the margin cap.
    assert ev.cell_config("im=0.1|mm=1").risk.initial_margin == 0.1
    Registry(str(tmp_path)).register(ev)  # registers cleanly
