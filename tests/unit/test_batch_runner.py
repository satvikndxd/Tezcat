"""Phase F5: batch execution — replications, resume, idempotency, factorials.

Uses deliberately small configs (few agents, short runs) so the full suite
stays fast; the semantics under test are identical at scale.
"""

import pytest

from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.experiments.aggregation import aggregate
from tezcat.experiments.batch import BatchRunner, MetricMissing, batch_id_for
from tezcat.experiments.registry import Registry
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion, Factor


def _config():
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5,
                             params={"herding": 0.5}),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        total_steps=60,
    )


def _ab_version(replications=3, **design_kw):
    design = dict(
        design_type="ab",
        question="Does retail herding change drawdown?",
        hypothesis="Higher herding raises max drawdown.",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown", "total_return", "total_trades"],
        primary_metric="max_drawdown",
        control=Arm(name="low", overrides={"agents.1.params.herding": 0.1}),
        treatments=[Arm(name="high", overrides={"agents.1.params.herding": 0.9})],
        replications=replications,
    )
    design.update(design_kw)
    return ExperimentVersion("exp_b", "herding-ab", _config(), DesignSpec(**design))


# ---------------------------------------------------------------------------
def test_batch_runs_all_replications(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    batch = BatchRunner(reg).run(vid)
    assert batch["status"] == "completed"
    assert batch["planned"] == batch["completed"] == 6

    rows = reg.list_result_rows(batch["batch_id"])
    assert len(rows) == 6
    version = reg.load(vid)
    for row in rows:
        # Every run points at the immutable version with full provenance.
        assert row["version_id"] == vid
        assert row["seed"] == version.seed_for(row["cell"], row["replication"])
        assert set(row["metrics"]) == {"max_drawdown", "total_return", "total_trades"}
        assert len(row["state_hash"]) == 64
        assert len(row["event_log_chain"]) == 64


def test_batch_is_deterministic(tmp_path):
    reg_a, reg_b = Registry(str(tmp_path / "a")), Registry(str(tmp_path / "b"))
    va, vb = reg_a.register(_ab_version()), reg_b.register(_ab_version())
    BatchRunner(reg_a).run(va)
    BatchRunner(reg_b).run(vb)
    rows_a = reg_a.list_result_rows(batch_id_for(reg_a.load(va)))
    rows_b = reg_b.list_result_rows(batch_id_for(reg_b.load(vb)))
    assert rows_a == rows_b  # identical seed-level rows incl. all hashes


def test_budget_stops_early_and_resume_completes(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    partial = BatchRunner(reg).run(vid, max_runs=4)
    assert partial["status"] == "partial"
    assert partial["completed"] == 4
    assert len(partial["pending_keys"]) == 2  # explicit, never silent

    resumed = BatchRunner(reg).run(vid)
    assert resumed["status"] == "completed"
    assert resumed["executed_this_call"] == 2  # only the pending runs ran


def test_resume_is_idempotent_and_skips_existing_rows(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    batch = BatchRunner(reg).run(vid)
    # Mark a stored row; a re-run must skip (not recompute) existing rows.
    row = reg.load_result_row(batch["batch_id"], "c000_r0000")
    row["marker"] = "left-intact"
    reg.save_result_row(batch["batch_id"], "c000_r0000", row)

    again = BatchRunner(reg).run(vid)
    assert again["executed_this_call"] == 0
    assert reg.load_result_row(batch["batch_id"], "c000_r0000")["marker"] == "left-intact"


def test_aggregation_by_cell(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    BatchRunner(reg).run(vid)
    summary = aggregate(reg, vid)

    assert summary["complete"] and summary["missing_keys"] == []
    assert set(summary["cells"]) == {"low", "high"}
    for cell in summary["cells"].values():
        assert cell["n"] == 3
        dd = cell["metrics"]["max_drawdown"]
        assert len(dd["values"]) == 3          # seed-level data preserved
        assert dd["min"] <= dd["median"] <= dd["max"]
        assert len(set(cell["seeds"])) == 3
    assert reg.get_batch_summary(summary["batch_id"]) == summary


def test_partial_aggregation_reports_missing_runs(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    BatchRunner(reg).run(vid, max_runs=4)
    summary = aggregate(reg, vid)
    assert not summary["complete"]
    assert summary["completed_runs"] == 4
    assert len(summary["missing_keys"]) == 2


def test_factorial_herding_x_liquidity(tmp_path):
    """Spec acceptance: a herding x liquidity factorial with seed-level output."""
    reg = Registry(str(tmp_path))
    design = DesignSpec(
        design_type="factorial",
        question="Does herding interact with market-maker liquidity?",
        hypothesis="Drawdown is superadditive in high herding x thin liquidity.",
        independent_variables=["agents.1.params.herding", "agents.2.count"],
        dependent_variables=["max_drawdown", "total_trades"],
        primary_metric="max_drawdown",
        factors=[
            Factor(name="herding", path="agents.1.params.herding", levels=[0.1, 0.9]),
            Factor(name="mm", path="agents.2.count", levels=[1, 3]),
        ],
        replications=2,
    )
    vid = reg.register(ExperimentVersion("exp_f", "herding-x-mm", _config(), design))
    batch = BatchRunner(reg).run(vid)
    assert batch["completed"] == 8  # 4 cells x 2 replications

    summary = aggregate(reg, vid)
    assert set(summary["cells"]) == {"herding=0.1|mm=1", "herding=0.1|mm=3",
                                     "herding=0.9|mm=1", "herding=0.9|mm=3"}
    # Cells actually differ (different configs and seeds -> different markets).
    chains = {r["event_log_chain"] for r in reg.list_result_rows(batch["batch_id"])}
    assert len(chains) == 8


def test_unknown_dependent_variable_fails_loudly(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version(
        dependent_variables=["max_drawdown", "sharpe_ratio"],
        primary_metric="max_drawdown"))
    with pytest.raises(MetricMissing, match="sharpe_ratio"):
        BatchRunner(reg).run(vid)
