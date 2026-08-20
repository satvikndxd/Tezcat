"""Phase F2: deterministic identity, seed derivation, and provenance.

The reproducibility contract (docs/reproducibility.md) requires that the same
config + schema version + seed produce identical state hashes, event hashes,
and event/trade identifiers — in-process, across engine instances, and across
fresh Python processes.
"""

import json
import subprocess
import sys

import pytest

from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, ShockConfig, ShockTrigger,
    ShockType, config_hash,
)
from tezcat.core.seeds import ALLOCATOR_VERSION, SeedPlan, derive_seed
from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.model import ConfigHashMismatch, Experiment


def _config(total_steps=120):
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=8),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5),
        ],
        shocks=[ShockConfig(
            shock_id="whale1", shock_type=ShockType.WHALE_ORDER,
            trigger=ShockTrigger(kind="scheduled", step=50),
            side="sell", magnitude=400, duration=5)],
        total_steps=total_steps,
    )


def _run(seed, total_steps=120):
    eng = EcologyEngine("run_x", _config(total_steps), seed)
    while not eng.done:
        eng.step()
    eng.check_invariants()
    return eng


# ---------------------------------------------------------------------------
# In-process reproduction
# ---------------------------------------------------------------------------
def test_same_seed_same_hashes():
    a, b = _run(42), _run(42)
    assert a.state_hash() == b.state_hash()
    assert a.event_hash() == b.event_hash()


def test_different_seed_diverges():
    a, b = _run(42), _run(43)
    assert a.event_hash() != b.event_hash()


def test_trade_and_event_ids_are_run_scoped():
    """A second engine in the same process restarts all ID counters."""
    a, b = _run(42), _run(42)
    assert a.trades, "expected trades in this scenario"
    assert a.trades[0]["trade_id"] == b.trades[0]["trade_id"] == "trd_00000001"
    assert [t["trade_id"] for t in a.trades] == [t["trade_id"] for t in b.trades]
    assert ([e.event_id for e in a.shocks.events]
            == [e.event_id for e in b.shocks.events])
    assert a.shocks.events[0].event_id == "shk_000001"


def test_deterministic_report_id():
    a = _run(42)
    assert a.build_report()["report_id"] == "rpt_run_x"


# ---------------------------------------------------------------------------
# Fresh-process reproduction
# ---------------------------------------------------------------------------
_SUBPROCESS_SNIPPET = """
import json
from tezcat.core.config import (AgentGroupConfig, AgentType, ExperimentConfig,
                                ShockConfig, ShockTrigger, ShockType)
from tezcat.engine.ecology import EcologyEngine
cfg = ExperimentConfig(
    agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=8),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5)],
    shocks=[ShockConfig(shock_id="whale1", shock_type=ShockType.WHALE_ORDER,
                        trigger=ShockTrigger(kind="scheduled", step=50),
                        side="sell", magnitude=400, duration=5)],
    total_steps=120)
eng = EcologyEngine("run_x", cfg, 42)
while not eng.done:
    eng.step()
print(json.dumps({"state": eng.state_hash(), "event": eng.event_hash()}))
"""


def test_fresh_process_reproduces_hashes():
    a = _run(42)
    out = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SNIPPET],
        capture_output=True, text=True, check=True, timeout=120,
    )
    remote = json.loads(out.stdout.strip().splitlines()[-1])
    assert remote["state"] == a.state_hash()
    assert remote["event"] == a.event_hash()


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------
def test_seed_derivation_is_pinned():
    """Guards the allocator: changing derivation must bump ALLOCATOR_VERSION."""
    assert ALLOCATOR_VERSION == 1
    assert derive_seed(42, "replication", 0) == derive_seed(42, "replication", 0)
    # Pinned values: if these change, the allocator changed silently.
    assert derive_seed(42, "replication", 0) == 790239264237624453
    assert derive_seed(42, "replication", 1) == 2654088454420261332
    assert derive_seed(0, "replication", 0, "stream", "agent_decisions") == 3637863401737680417


def test_seed_plan_allocation():
    plan = SeedPlan(root_seed=42)
    seeds = plan.replication_seeds(50)
    assert len(set(seeds)) == 50  # no collisions in a small plan
    assert seeds[7] == plan.replication_seed(7)  # order-independent
    assert plan.stream_seed(0, "a") != plan.stream_seed(0, "b")
    with pytest.raises(ValueError):
        SeedPlan(root_seed=42, allocator_version=999)
    with pytest.raises(TypeError):
        derive_seed(42, 1.5)


def test_seed_components_are_positionally_distinct():
    assert derive_seed(42, "a", 1) != derive_seed(42, "a1")
    assert derive_seed(42, 1) != derive_seed(42, "1")  # int vs str tagged


# ---------------------------------------------------------------------------
# Experiment identity
# ---------------------------------------------------------------------------
def test_experiment_hash_verification():
    exp = Experiment.create("t", _config())
    exp.verify_hash()  # fresh experiment verifies
    exp.config_hash = "0" * 64
    with pytest.raises(ConfigHashMismatch):
        exp.verify_hash()


def test_config_hash_covers_schema_version():
    """Same parameters, same hash — and stable within a schema version."""
    assert config_hash(_config()) == config_hash(_config())
    assert config_hash(_config()) != config_hash(_config(total_steps=121))
