"""Phase F6: statistical primitives, design-aware analysis, and reports."""

import random

import pytest

from tezcat.analysis import AnalysisError, analyze, build_report
from tezcat.analysis import stats
from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.experiments.batch import BatchRunner
from tezcat.experiments.registry import Registry
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion, Factor


# ---------------------------------------------------------------------------
# Primitives on known fixtures
# ---------------------------------------------------------------------------
def test_bootstrap_ci_covers_known_mean_and_is_deterministic():
    rng = random.Random(1)
    sample = [rng.gauss(10.0, 2.0) for _ in range(60)]
    a = stats.bootstrap_ci(sample, seed=7)
    b = stats.bootstrap_ci(sample, seed=7)
    assert a == b  # bit-identical given the seed
    assert a["ci_low"] < 10.0 < a["ci_high"]  # covers the true mean
    assert a["ci_low"] < a["estimate"] < a["ci_high"]
    c = stats.bootstrap_ci(sample, seed=8)
    assert c != a  # different seed, different resamples


def test_bootstrap_degenerate_sample():
    out = stats.bootstrap_ci([1.0], seed=1)
    assert out["ci_low"] is None and "warning" in out


def test_cohens_d_known_shift():
    rng = random.Random(2)
    a = [rng.gauss(0.0, 1.0) for _ in range(200)]
    b = [v + 1.0 for v in a]  # exact one-sigma shift
    d = stats.cohens_d(a, b)
    assert 0.9 < d < 1.1
    assert stats.cohens_d([1.0, 1.0], [1.0, 1.0]) is None  # zero variance
    assert stats.cohens_d([1.0], [2.0, 3.0]) is None       # n < 2


def test_cliffs_delta_extremes():
    assert stats.cliffs_delta([1, 2, 3], [4, 5, 6]) == 1.0   # full separation
    assert stats.cliffs_delta([4, 5, 6], [1, 2, 3]) == -1.0
    assert stats.cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0


def test_permutation_test_behaviour():
    rng = random.Random(3)
    same = [rng.gauss(0, 1) for _ in range(30)]
    other = [rng.gauss(0, 1) for _ in range(30)]
    far = [v + 5.0 for v in other]
    p_null = stats.permutation_test(same, other, seed=5)["p_value"]
    p_far = stats.permutation_test(same, far, seed=5)["p_value"]
    assert p_far < 0.01 < p_null
    # Deterministic.
    assert stats.permutation_test(same, far, seed=5) == stats.permutation_test(same, far, seed=5)


def test_holm_adjustment_fixture():
    adjusted = stats.holm_adjust([0.01, 0.04, 0.03, None])
    # m=3: sorted (0.01, 0.03, 0.04) -> 3*0.01=0.03, 2*0.03=0.06, 1*0.04->max(0.06)
    assert adjusted[0] == pytest.approx(0.03)
    assert adjusted[2] == pytest.approx(0.06)
    assert adjusted[1] == pytest.approx(0.06)  # monotonicity enforced
    assert adjusted[3] is None


def test_interaction_2x2_known_fixture():
    # Constructed: effect of B is +1 when A low, +3 when A high -> interaction 2.
    cells = {
        "a0b0": [0.0, 0.1, -0.1, 0.05], "a0b1": [1.0, 1.1, 0.9, 1.05],
        "a1b0": [0.0, 0.05, -0.05, 0.1], "a1b1": [3.0, 3.1, 2.9, 3.05],
    }
    out = stats.interaction_2x2(cells, ["a0b0", "a0b1", "a1b0", "a1b1"], seed=9)
    assert out["estimate"] == pytest.approx(2.0, abs=0.15)
    assert out["ci_low"] < out["estimate"] < out["ci_high"]
    assert out["main_effect_b"] == pytest.approx(2.0, abs=0.15)

    degenerate = stats.interaction_2x2({"a0b0": [1.0], "a0b1": [1.0],
                                        "a1b0": [1.0], "a1b1": [1.0]},
                                       ["a0b0", "a0b1", "a1b0", "a1b1"], seed=9)
    assert degenerate["estimate"] is None


def test_describe_quantiles():
    d = stats.describe(list(range(101)))
    assert d["median"] == 50 and d["q05"] == 5.0 and d["q95"] == 95.0
    assert stats.describe([]) == {"n": 0}


# ---------------------------------------------------------------------------
# End-to-end: batch -> analysis -> report
# ---------------------------------------------------------------------------
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


def _ab_version(replications=4):
    return ExperimentVersion("exp_a", "herding-ab", _config(), DesignSpec(
        design_type="ab",
        question="Does retail herding change drawdown?",
        hypothesis="Higher herding raises max drawdown.",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown", "total_trades"],
        primary_metric="max_drawdown",
        control=Arm(name="low", overrides={"agents.1.params.herding": 0.1}),
        treatments=[Arm(name="high", overrides={"agents.1.params.herding": 0.9})],
        replications=replications))


def test_analysis_end_to_end_and_deterministic(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    BatchRunner(reg).run(vid)

    a1 = analyze(reg, vid, seed=0)
    a2 = analyze(reg, vid, seed=0)
    assert a1 == a2  # fully deterministic artifact
    assert a1["analysis_id"].startswith("ana_")
    assert reg.get_analysis(vid) == a1

    comp = a1["comparisons"][0]
    assert comp["control"] == "low" and comp["treatment"] == "high"
    assert comp["permutation"]["p_holm"] is not None
    assert set(a1["descriptives"]) == {"low", "high"}

    a3 = analyze(reg, vid, seed=1)
    assert a3["comparisons"][0]["diff"] != comp["diff"]  # seed changes resamples
    assert a3["comparisons"][0]["diff"]["estimate"] == comp["diff"]["estimate"]


def test_analysis_refuses_partial_batch_unless_allowed(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    BatchRunner(reg).run(vid, max_runs=5)  # 8 planned
    with pytest.raises(AnalysisError, match="incomplete"):
        analyze(reg, vid)
    a = analyze(reg, vid, allow_partial=True)
    assert any("PARTIAL DATA" in w for w in a["warnings"])


def test_report_numbers_match_independent_recomputation(tmp_path):
    """Spec validation: report numbers equal independently recomputed values."""
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    batch = BatchRunner(reg).run(vid)
    analysis = analyze(reg, vid, seed=0)
    report = build_report(reg, vid)

    # Independent recomputation straight from seed-level rows.
    rows = reg.list_result_rows(batch["batch_id"])
    for cell in ("low", "high"):
        vals = [r["metrics"]["max_drawdown"] for r in rows if r["cell"] == cell]
        mean = sum(vals) / len(vals)
        assert analysis["descriptives"][cell]["max_drawdown"]["mean"] == pytest.approx(mean)
        assert f"{mean:.4f}" in report  # the rendered number is the artifact number

    diff = analysis["comparisons"][0]["diff"]["estimate"]
    low = [r["metrics"]["max_drawdown"] for r in rows if r["cell"] == "low"]
    high = [r["metrics"]["max_drawdown"] for r in rows if r["cell"] == "high"]
    assert diff == pytest.approx(sum(high) / len(high) - sum(low) / len(low))

    # Provenance and honesty markers.
    assert reg.load(vid).research_hash in report
    assert "Claims **not** supported" in report
    assert "persisted artifacts" in report
    assert reg.get_report(vid) == report


def test_report_requires_analysis(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_ab_version())
    BatchRunner(reg).run(vid)
    with pytest.raises(ValueError, match="artifacts only"):
        build_report(reg, vid)


def test_factorial_analysis_produces_interaction(tmp_path):
    reg = Registry(str(tmp_path))
    design = DesignSpec(
        design_type="factorial",
        question="Does herding interact with liquidity?",
        hypothesis="Drawdown is superadditive in herding x thin books.",
        independent_variables=["agents.1.params.herding", "agents.2.count"],
        dependent_variables=["max_drawdown"],
        primary_metric="max_drawdown",
        factors=[
            Factor(name="herding", path="agents.1.params.herding", levels=[0.1, 0.9]),
            Factor(name="mm", path="agents.2.count", levels=[1, 3]),
        ],
        replications=3)
    vid = reg.register(ExperimentVersion("exp_f", "fx", _config(), design))
    BatchRunner(reg).run(vid)
    a = analyze(reg, vid, seed=0)
    it = a["interaction"]
    assert it is not None and it["estimate"] is not None
    assert it["cell_order"] == ["herding=0.1|mm=1", "herding=0.1|mm=3",
                               "herding=0.9|mm=1", "herding=0.9|mm=3"]
    report = build_report(reg, vid)
    assert "2×2 interaction" in report


def test_sweep_analysis_trend(tmp_path):
    reg = Registry(str(tmp_path))
    design = DesignSpec(
        design_type="sweep",
        question="How does herding level shift drawdown?",
        hypothesis="Drawdown rises with herding.",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown"],
        primary_metric="max_drawdown",
        factors=[Factor(name="herding", path="agents.1.params.herding",
                        levels=[0.1, 0.5, 0.9])],
        replications=2)
    vid = reg.register(ExperimentVersion("exp_s", "sweep", _config(), design))
    BatchRunner(reg).run(vid)
    a = analyze(reg, vid, seed=0)
    assert a["trend"] is not None
    assert -1.0 <= a["trend"]["level_mean_correlation"] <= 1.0
    assert a["trend"]["levels_used"] == 3
