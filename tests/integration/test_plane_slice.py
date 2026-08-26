"""S5-D acceptance: the full vertical slice, linked and reproducible.

Forecast → Portfolio → Tezcat Worlds → Nautilus → Risk → Report — every
stage a linked artifact, the edge-decay pipeline measured (never
fabricated), and the entire chain reproduced hash-for-hash.
"""

import pytest

pytest.importorskip("nautilus_trader",
                    reason="the slice executes through the S4 bridge")

from tezcat.experiments.registry import Registry
from tezcat.plane import ArtifactGraph
from tezcat.plane.portfolio import CostModel
from tezcat.plane.slice import reproduce_slice, run_full_slice

from tests.unit.test_worlds import small_version


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    td = str(tmp_path_factory.mktemp("plane_slice"))
    registry = Registry(td)
    vid = registry.register(small_version(experiment_id="exp_plane",
                                          total_steps=260))
    return td, vid


@pytest.fixture(scope="module")
def slice_result(env):
    td, vid = env
    return run_full_slice(
        td, history_ref=vid, history_cell="control",
        stress_cells=["control", "high_herding"],
        horizon=40, n_paths=120, seed=7,
        optimizer_params={"risk_aversion": 0.0},
        costs=CostModel(transaction_cost_bps=0.0),
        capital=200_000)


def test_full_chain_artifacts_linked(env, slice_result):
    td, _ = env
    graph = ArtifactGraph(td)
    chain = graph.lineage(slice_result["report_artifact"])
    types = [a.artifact_type for a in chain]
    assert types[0] == "market_world"
    for expected in ("external_dataset", "forecast", "portfolio",
                     "backtest", "risk_report", "research_report"):
        assert expected in types
    # every node except world roots names its parents (worlds are the
    # graph's roots; they wrap native world hashes as external_identity)
    for node in chain:
        if node.artifact_type == "market_world":
            assert node.external_identity  # native world hash carried
        else:
            assert node.parent_hashes


def test_portfolio_allocated_and_sized_execution(env, slice_result):
    td, _ = env
    assert slice_result["risky_weight"] > 0
    assert len(slice_result["backtest_artifacts"]) == 2
    graph = ArtifactGraph(td)
    bt = graph.payload(slice_result["backtest_artifacts"][0])
    assert bt["lab_result_id"].startswith("lab_")
    assert bt["metrics"]["regime_breakdown"]


def test_edge_decay_measured_not_fabricated(slice_result):
    ed = slice_result["edge_decay"]
    assert ed["portfolio_edge_gross"] == pytest.approx(
        slice_result["risky_weight"] * ed["model_edge_expected"], abs=1e-7)
    assert ed["execution_realized_median"] is not None
    assert ed["execution_realized_worst"] <= ed["execution_realized_best"]
    assert "nothing is fabricated" in ed["note"]


def test_report_from_artifacts_only(env, slice_result):
    td, _ = env
    graph = ArtifactGraph(td)
    payload = graph.payload(slice_result["report_artifact"])
    md = payload["markdown"]
    assert "Edge-decay pipeline (measured)" in md
    assert "probabilistic research observation — not an order" in md
    assert "SYNTHETIC" in md
    assert "## Limitations" in md
    assert payload["artifact_ids"]["forecast"] == \
        slice_result["forecast_artifact"]


def test_zero_allocation_skips_execution_explicitly(env):
    td, vid = env
    out = run_full_slice(td, history_ref=vid, history_cell="control",
                         stress_cells=["control"], horizon=40, n_paths=120,
                         seed=7, costs=CostModel(transaction_cost_bps=500.0),
                         capital=200_000)
    assert out["risky_weight"] == 0.0
    assert out["backtest_artifacts"] == []
    graph = ArtifactGraph(td)
    risk = graph.payload(out["risk_artifact"])
    assert risk["per_world"][0]["skipped"] == "portfolio allocated zero weight"


def test_whole_chain_reproduces(env, slice_result):
    td, _ = env
    rep = reproduce_slice(td, slice_result["report_artifact"])
    assert rep["success"], rep["checks"]
    assert rep["identical"]
    names = [c["check"] for c in rep["checks"]]
    assert "forecast artifact reproduces" in names
    assert "backtest artifacts reproduce" in names
    assert any(c["check"].startswith("environment:") for c in rep["checks"])
