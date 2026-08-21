"""Phase F3: checkpoint, restore, and fork validation.

Contract: restoring a checkpoint and continuing must be indistinguishable —
bit-for-bit — from the uninterrupted run; a fork shares the parent's exact
prefix and diverges only through the declared intervention.
"""

import json

import pytest

from tezcat.checkpoints import checkpoint_hash, lineage
from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, ShockConfig, ShockTrigger,
    ShockType,
)
from tezcat.engine.ecology import EcologyEngine


def _config(total_steps=200):
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=8),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.MOMENTUM_TRADER, count=4),
            AgentGroupConfig(agent_type=AgentType.MEAN_REVERSION_TRADER, count=3),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        shocks=[ShockConfig(
            shock_id="whale1", shock_type=ShockType.WHALE_ORDER,
            trigger=ShockTrigger(kind="scheduled", step=50),
            side="sell", magnitude=500, duration=5)],
        total_steps=total_steps,
    )


def _run_to(engine, step):
    while engine.step_num < step and not engine.done:
        engine.step()
    return engine


# ---------------------------------------------------------------------------
# Restore continuity
# ---------------------------------------------------------------------------
def test_restore_continues_bit_identically():
    cfg = _config()
    baseline = _run_to(EcologyEngine("run_c", cfg, 42), 200)

    eng = _run_to(EcologyEngine("run_c", cfg, 42), 100)
    cp = eng.checkpoint()
    restored = EcologyEngine.restore(cp, cfg)
    _run_to(restored, 200)
    restored.check_invariants()

    assert restored.state_hash() == baseline.state_hash()
    assert restored.event_hash() == baseline.event_hash()
    assert restored.events_log.chain == baseline.events_log.chain
    assert restored.trades == baseline.trades
    assert restored.build_report() == baseline.build_report()


def test_checkpoint_survives_json_roundtrip():
    cfg = _config()
    eng = _run_to(EcologyEngine("run_c", cfg, 42), 100)
    cp = json.loads(json.dumps(eng.checkpoint()))
    baseline = _run_to(EcologyEngine("run_c", cfg, 42), 200)
    restored = _run_to(EcologyEngine.restore(cp, cfg), 200)
    assert restored.state_hash() == baseline.state_hash()
    assert restored.events_log.chain == baseline.events_log.chain


def test_restore_at_checkpoint_matches_source_state():
    cfg = _config()
    eng = _run_to(EcologyEngine("run_c", cfg, 42), 120)
    restored = EcologyEngine.restore(eng.checkpoint(), cfg)
    assert restored.state_hash() == eng.state_hash()
    assert restored.step_num == eng.step_num
    assert restored.events_log.chain == eng.events_log.chain


def test_checkpoint_hash_is_content_addressed():
    cfg = _config()
    a = _run_to(EcologyEngine("run_c", cfg, 42), 80).checkpoint()
    b = _run_to(EcologyEngine("run_c", cfg, 42), 80).checkpoint()
    c = _run_to(EcologyEngine("run_c", cfg, 42), 81).checkpoint()
    assert checkpoint_hash(a) == checkpoint_hash(b)
    assert checkpoint_hash(a) != checkpoint_hash(c)


# ---------------------------------------------------------------------------
# Compatibility guards
# ---------------------------------------------------------------------------
def test_restore_rejects_mismatched_config():
    cfg = _config()
    cp = _run_to(EcologyEngine("run_c", cfg, 42), 50).checkpoint()
    other = _config(total_steps=300)
    with pytest.raises(ValueError, match="config_hash"):
        EcologyEngine.restore(cp, other)


def test_restore_rejects_unknown_checkpoint_version():
    cfg = _config()
    cp = _run_to(EcologyEngine("run_c", cfg, 42), 50).checkpoint()
    cp["checkpoint_version"] = 999
    with pytest.raises(ValueError, match="checkpoint version"):
        EcologyEngine.restore(cp, cfg)


# ---------------------------------------------------------------------------
# Forks
# ---------------------------------------------------------------------------
def test_fork_preserves_prefix_and_diverges_after_intervention():
    cfg = _config()
    parent = _run_to(EcologyEngine("run_parent", cfg, 42), 100)
    cp = parent.checkpoint()
    prefix_trades = list(parent.trades)
    prefix_chain = parent.events_log.chain

    intervention = {
        "name": "sentiment_crash",
        "operations": [
            {"op": "set_sentiment", "value": -0.9, "duration": 30},
            {"op": "inject_shock", "shock_type": "whale_order",
             "side": "sell", "magnitude": 800, "duration": 5},
        ],
    }
    child = EcologyEngine.fork(cp, cfg, "run_child", intervention)

    # Prefix preserved exactly (trades and history carried over).
    assert child.trades[:len(prefix_trades)] == prefix_trades
    # Intervention recorded with parent lineage before any new market event.
    iv = child.events_log.events[-1]
    assert iv["type"] == "intervention"
    assert iv["data"]["parent_run_id"] == "run_parent"
    assert iv["data"]["checkpoint_step"] == 100
    assert iv["data"]["checkpoint_hash"] == checkpoint_hash(cp)

    # Continue both worlds; they must diverge only after the fork point.
    _run_to(parent, 200)
    _run_to(child, 200)
    assert child.trades[:len(prefix_trades)] == prefix_trades
    assert child.state_hash() != parent.state_hash()
    child.check_invariants()
    parent.check_invariants()


def test_fork_without_intervention_equals_parent_continuation():
    """A no-op fork is a pure control: identical market path to the parent."""
    cfg = _config()
    parent = _run_to(EcologyEngine("run_parent", cfg, 42), 100)
    cp = parent.checkpoint()
    child = EcologyEngine.fork(cp, cfg, "run_child", None)
    _run_to(parent, 200)
    _run_to(child, 200)
    # Market outputs identical (trades don't embed run_id).
    assert child.trades == parent.trades
    assert child.state_hash() == parent.state_hash()


def test_lineage_record():
    cfg = _config()
    cp = _run_to(EcologyEngine("run_parent", cfg, 42), 60).checkpoint()
    lin = lineage(cp)
    assert lin["parent_run_id"] == "run_parent"
    assert lin["checkpoint_step"] == 60
    assert lin["checkpoint_hash"] == checkpoint_hash(cp)
    assert lin["seed"] == 42


def test_fork_rejects_unknown_operation():
    cfg = _config()
    cp = _run_to(EcologyEngine("run_parent", cfg, 42), 60).checkpoint()
    with pytest.raises(ValueError, match="unknown intervention op"):
        EcologyEngine.fork(cp, cfg, "run_child",
                           {"name": "bad", "operations": [{"op": "nope"}]})
