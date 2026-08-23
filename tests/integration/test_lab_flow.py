"""S4 first-milestone acceptance: Strategy Under Synthetic Worlds.

A researcher can select a Tezcat experiment, build deterministic Market
Worlds, export a Nautilus-compatible stream, run a fixed strategy across
two different worlds, get PnL/drawdown/execution/regime-conditioned
metrics with full provenance, compare results, and reproduce exactly.
"""

import pytest

pytest.importorskip("nautilus_trader",
                    reason="Strategy Lab is an optional bridge")

from tezcat.experiments.registry import Registry
from tezcat.lab.backtest import run_backtest
from tezcat.lab.results import LabResultStore, reproduce_result, result_id_for
from tezcat.worlds import WorldStore, build_world

from tests.unit.test_worlds import small_version


def test_strategy_under_synthetic_worlds(tmp_path):
    registry = Registry(str(tmp_path))
    world_store = WorldStore(str(tmp_path))
    result_store = LabResultStore(str(tmp_path))

    # 1-2. select experiment; build two deterministic worlds (control vs
    #      high-herding cell of the same registered version)
    version = small_version(experiment_id="exp_lab_flow")
    vid = registry.register(version)
    worlds = [build_world(registry, vid, cell=cell)
              for cell in ("control", "high_herding")]
    assert worlds[0].world_hash != worlds[1].world_hash
    for w in worlds:
        world_store.save(w)
        assert w.manifest["research_hash"] == version.research_hash

    # 3-5. same strategy configuration across both worlds
    result_ids = []
    for w in worlds:
        result = run_backtest(w, "ema_cross", {"fast": 8, "slow": 24,
                                               "trade_size": 40},
                              starting_cash=250_000)
        rid = result_store.save(result)
        result_ids.append(rid)
        m = result["metrics"]
        assert {"total_pnl", "max_drawdown", "fill_rate", "turnover",
                "mean_slippage_vs_mid", "regime_breakdown"} <= set(m)
        # 6. provenance: research identity → world → strategy → result
        assert result["world"]["research_hash"] == version.research_hash
        assert rid == result_id_for(w.world_hash,
                                    result["strategy"]["strategy_hash"],
                                    result["backtest_config"])

    # identical strategy hash across worlds — environmental sensitivity
    records = [result_store.get(rid) for rid in result_ids]
    assert records[0]["strategy"]["strategy_hash"] == \
        records[1]["strategy"]["strategy_hash"]

    # 7. compare across worlds
    comparison = result_store.compare(result_ids)
    assert comparison["mode"] == "same strategy, different worlds"
    assert len(comparison["rows"]) == 2
    assert all("regime_pnl" in row for row in comparison["rows"])

    # 8. reproduce the full chain exactly
    for rid in result_ids:
        rep = reproduce_result(registry, world_store, result_store, rid)
        assert rep["success"], rep["checks"]
        names = [c["check"] for c in rep["checks"]]
        assert "world hash matches" in names
        assert "metrics reproduce exactly" in names


def test_environment_mismatch_fails_loudly(tmp_path):
    registry = Registry(str(tmp_path))
    world_store = WorldStore(str(tmp_path))
    result_store = LabResultStore(str(tmp_path))
    vid = registry.register(small_version(experiment_id="exp_env"))
    world = build_world(registry, vid)
    world_store.save(world)
    result = run_backtest(world, "buy_hold", starting_cash=50_000)
    result["environment"]["nautilus_trader_version"] = "0.0.0-different"
    rid = result_store.save(result)
    rep = reproduce_result(registry, world_store, result_store, rid)
    assert not rep["success"]
    assert "non-identical" in rep["reason"]
