"""Phase F3: canonical event log and replay validation.

The core claim: the event log is *complete* — replaying it into a clean
kernel (no agents, no RNG) reconstructs the exact market state the live
engine reached, verified via the shared state hash.
"""

import pytest

from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MarketConfig, ShockConfig,
    ShockTrigger, ShockType,
)
from tezcat.engine.ecology import EcologyEngine
from tezcat.events.log import EventLog, ReplayError
from tezcat.events.replay import ReplayKernel


def _config(total_steps=150, self_trade_policy="allow", shocks=True):
    shock_list = []
    if shocks:
        shock_list = [
            ShockConfig(shock_id="whale1", shock_type=ShockType.WHALE_ORDER,
                        trigger=ShockTrigger(kind="scheduled", step=40),
                        side="sell", magnitude=600, duration=5),
            ShockConfig(shock_id="mm1", shock_type=ShockType.MM_WITHDRAWAL,
                        trigger=ShockTrigger(kind="scheduled", step=60),
                        magnitude=1.0, duration=15),
            ShockConfig(shock_id="sent1", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=80),
                        side="sell", magnitude=0.7, duration=20),
        ]
    return ExperimentConfig(
        market=MarketConfig(self_trade_policy=self_trade_policy),
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=8),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.MOMENTUM_TRADER, count=4),
            AgentGroupConfig(agent_type=AgentType.MEAN_REVERSION_TRADER, count=3),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        shocks=shock_list,
        total_steps=total_steps,
    )


def _run(config, seed=42, run_id="run_r"):
    eng = EcologyEngine(run_id, config, seed)
    while not eng.done:
        eng.step()
    eng.check_invariants()
    return eng


# ---------------------------------------------------------------------------
# Replay completeness
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("policy", ["allow", "cancel_resting"])
def test_replay_reconstructs_exact_state(policy):
    """Replaying the event log alone reproduces the live state hash."""
    cfg = _config(self_trade_policy=policy)
    eng = _run(cfg)
    rk = ReplayKernel(cfg, eng.run_id)
    rk.apply_all(eng.events_log.events)
    assert rk.state_hash() == eng.state_hash()
    assert rk.trades_applied == len(eng.trades)


def test_replay_without_shocks():
    cfg = _config(shocks=False)
    eng = _run(cfg)
    rk = ReplayKernel(cfg, eng.run_id)
    rk.apply_all(eng.events_log.events)
    assert rk.state_hash() == eng.state_hash()


def test_replay_partial_prefix_tracks_intermediate_state():
    """Replay to an intermediate step_ended matches a shorter live run."""
    cfg = _config(total_steps=150)
    long = _run(cfg)
    short = EcologyEngine("run_r", cfg, 42)
    for _ in range(70):
        short.step()

    rk = ReplayKernel(cfg, "run_r")
    for ev in long.events_log.events:
        rk.apply(ev)
        if ev["type"] == "step_ended" and ev["step"] == 70:
            break
    assert rk.state_hash() == short.state_hash()


# ---------------------------------------------------------------------------
# Chain integrity and duplicate policy
# ---------------------------------------------------------------------------
def test_event_chain_verifies_and_detects_tampering():
    cfg = _config(total_steps=60, shocks=False)
    eng = _run(cfg)
    events = eng.events_log.events
    assert EventLog.verify_chain(eng.run_id, events) == eng.events_log.chain

    tampered = [dict(e) for e in events]
    tampered[10]["data"] = dict(tampered[10]["data"])
    if "quantity" in tampered[10]["data"]:
        tampered[10]["data"]["quantity"] = 999_999
    else:
        tampered[10]["data"]["tampered"] = True
    assert EventLog.verify_chain(eng.run_id, tampered) != eng.events_log.chain


def test_duplicate_event_rejected():
    cfg = _config(total_steps=60, shocks=False)
    eng = _run(cfg)
    events = list(eng.events_log.events)
    events.insert(5, events[4])  # duplicate seq
    rk = ReplayKernel(cfg, eng.run_id)
    with pytest.raises(ReplayError, match="sequence violation"):
        rk.apply_all(events)


def test_missing_event_rejected():
    cfg = _config(total_steps=60, shocks=False)
    eng = _run(cfg)
    events = [e for e in eng.events_log.events if e["seq"] != 3]
    rk = ReplayKernel(cfg, eng.run_id)
    with pytest.raises(ReplayError, match="sequence violation"):
        rk.apply_all(events)

    with pytest.raises(ReplayError, match="sequence violation"):
        EventLog.verify_chain(eng.run_id, events)


# ---------------------------------------------------------------------------
# Forensics: the crash chain is reconstructible from events alone
# ---------------------------------------------------------------------------
def test_forensic_shock_chain_from_events_alone():
    cfg = _config(total_steps=150)
    eng = _run(cfg)
    events = eng.events_log.events

    shocks = [e for e in events if e["type"] == "shock"]
    assert [s["data"]["shock_id"] for s in shocks] == ["whale1", "mm1", "sent1"]

    # Whale sell flow is attributable: trades in the whale window where the
    # whale is the seller, traceable to accepted whale orders.
    whale_step = shocks[0]["step"]
    whale_orders = {e["data"]["order_id"] for e in events
                    if e["type"] == "order_accepted"
                    and e["data"]["agent_id"] == "whale"}
    assert whale_orders, "whale orders must appear in the event log"
    whale_trades = [e for e in events if e["type"] == "trade"
                    and e["data"]["sell_order_id"] in whale_orders]
    assert whale_trades
    assert all(e["step"] >= whale_step for e in whale_trades)

    # Every trade references an accepted order on both sides.
    accepted = {e["data"]["order_id"] for e in events if e["type"] == "order_accepted"}
    for e in events:
        if e["type"] == "trade":
            assert e["data"]["buy_order_id"] in accepted
            assert e["data"]["sell_order_id"] in accepted

    # step_ended events carry the environment path (sentiment shock visible).
    sent_step = shocks[2]["step"]
    sentiments = {e["step"]: e["data"]["sentiment"] for e in events
                  if e["type"] == "step_ended"}
    assert sentiments[sent_step] < -0.5  # sentiment shock applied that step


def test_event_log_deterministic():
    cfg = _config(total_steps=80, shocks=False)
    a, b = _run(cfg), _run(cfg)
    assert a.events_log.chain == b.events_log.chain
    assert a.events_log.events == b.events_log.events
    c = _run(cfg, seed=43)
    assert a.events_log.chain != c.events_log.chain
