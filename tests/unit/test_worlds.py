"""Market Worlds (S4): determinism, fingerprint, stream contract, store."""

import pytest

from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, ShockConfig, ShockTrigger,
    ShockType,
)
from tezcat.experiments.registry import Registry
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion
from tezcat.worlds import (
    STEP_NS, WORLD_EPOCH_NS, EcologyFingerprint, WorldError, WorldStore,
    build_world,
)


def small_version(total_steps=200, herding=0.5, shock_mag=None,
                  experiment_id="exp_worlds"):
    shocks = []
    if shock_mag is not None:
        shocks = [ShockConfig(shock_id="s", shock_type=ShockType.WHALE_ORDER,
                              trigger=ShockTrigger(kind="scheduled", step=100),
                              side="sell", magnitude=shock_mag, duration=5)]
    config = ExperimentConfig(
        total_steps=total_steps, step_delay_ms=0, shocks=shocks,
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5,
                             params={"herding": herding}),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ])
    design = DesignSpec(
        design_type="ab", question="world probe question?",
        hypothesis="world probe hypothesis",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown"], primary_metric="max_drawdown",
        control=Arm(name="control"),
        treatments=[Arm(name="high_herding",
                        overrides={"agents.1.params.herding": 0.95})],
        replications=2)
    return ExperimentVersion(experiment_id, "worlds-test", config, design)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    reg = Registry(str(tmp_path_factory.mktemp("worlds")))
    reg.register(small_version())
    return reg


@pytest.fixture(scope="module")
def world(registry):
    return build_world(registry, registry.list()[0]["version_id"],
                       cell="control", replication=0)


class TestDeterminism:
    def test_same_reference_same_world(self, registry, world):
        again = build_world(registry, registry.list()[0]["version_id"],
                            cell="control", replication=0)
        assert again.world_hash == world.world_hash
        assert again.quotes == world.quotes
        assert again.trades == world.trades
        assert again.annotations == world.annotations

    def test_different_cell_different_world(self, registry, world):
        other = build_world(registry, registry.list()[0]["version_id"],
                            cell="high_herding", replication=0)
        assert other.world_hash != world.world_hash

    def test_different_replication_different_world(self, registry, world):
        other = build_world(registry, registry.list()[0]["version_id"],
                            cell="control", replication=1)
        assert other.world_hash != world.world_hash
        assert other.manifest["seed"] != world.manifest["seed"]

    def test_unknown_cell_rejected(self, registry):
        with pytest.raises(WorldError, match="unknown cell"):
            build_world(registry, registry.list()[0]["version_id"],
                        cell="nope")


class TestStreamContract:
    def test_time_mapping(self, world):
        q0 = world.quotes[0]
        assert q0["ts_ns"] == WORLD_EPOCH_NS + q0["step"] * STEP_NS + STEP_NS - 1
        # strictly increasing, quotes and trades separately
        for rows in (world.quotes, world.trades):
            ts = [r["ts_ns"] for r in rows]
            assert ts == sorted(ts) and len(set(ts)) == len(ts)

    def test_trades_precede_step_quote(self, world):
        by_step = {}
        for t in world.trades:
            by_step.setdefault(t["step"], []).append(t["ts_ns"])
        for q in world.quotes:
            for t_ts in by_step.get(q["step"], []):
                assert t_ts < q["ts_ns"]

    def test_quotes_uncrossed_and_positive(self, world):
        for q in world.quotes:
            assert 0 < q["bid"] <= q["ask"]
            assert q["bid_size"] > 0 and q["ask_size"] > 0

    def test_aggressor_is_null_not_guessed(self, world):
        assert all(t["aggressor"] is None for t in world.trades)

    def test_regime_intervals_cover_run(self, world):
        ivs = world.annotations["regime_intervals"]
        assert ivs[0]["start_step"] == 0
        assert ivs[-1]["end_step"] == 200
        for a, b in zip(ivs, ivs[1:]):
            assert a["end_step"] == b["start_step"]
        assert world.regime_at(world.quotes[0]["ts_ns"]) in (
            "stable", "crisis", "recovery")


class TestFingerprint:
    def test_versioned_and_hashable(self, world):
        fp = world.fingerprint()
        assert fp.fingerprint_version == 1
        assert len(fp.fingerprint_hash()) == 64
        assert fp.fingerprint_hash() == world.fingerprint().fingerprint_hash()

    def test_derived_values(self, world):
        fp = world.fingerprint()
        assert fp.n_steps == 200
        assert fp.mean_relative_spread > 0
        assert fp.depth_q25 <= fp.depth_q50 <= fp.depth_q75
        assert abs(sum(fp.regime_occupancy.values()) - 1.0) < 1e-9
        # composition fractions are rounded to 6dp for stable hashing
        assert abs(sum(fp.agent_composition.values()) - 1.0) < 1e-5
        assert fp.total_agents == 13
        assert fp.trades_per_step > 0

    def test_composition_from_config_not_realization(self, world):
        fp = world.fingerprint()
        assert fp.agent_composition["noise_trader"] == pytest.approx(6 / 13,
                                                                     abs=1e-6)


class TestWorldStore:
    def test_save_load_roundtrip_verifies_hashes(self, tmp_path, world):
        store = WorldStore(str(tmp_path))
        wid = store.save(world)
        assert store.save(world) == wid  # idempotent
        loaded = store.load(wid)
        assert loaded.world_hash == world.world_hash
        assert loaded.quotes == world.quotes

    def test_tampered_artifact_refused(self, tmp_path, world):
        import json
        store = WorldStore(str(tmp_path))
        wid = store.save(world)
        path = store.root / wid / "quotes.json"
        rows = json.loads(path.read_text())
        rows[0]["bid"] = 1.23
        path.write_text(json.dumps(rows))
        with pytest.raises(WorldError, match="checksum"):
            store.load(wid)

    def test_unknown_world(self, tmp_path):
        with pytest.raises(WorldError, match="unknown world"):
            WorldStore(str(tmp_path)).load("mw_nope")

    def test_index_row(self, tmp_path, world):
        store = WorldStore(str(tmp_path))
        store.save(world)
        (row,) = store.list()
        assert row["cell"] == "control"
        assert "regime_occupancy" in row
