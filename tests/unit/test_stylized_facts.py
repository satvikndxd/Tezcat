"""Phase F9: data providers, stylized-facts features, calibration discipline."""

import json
import math
import random

import pytest

from tezcat.analysis.stylized_facts import (
    acf, compare_features, excess_kurtosis, extract_features, hill_tail_index,
    log_returns, max_drawdown, stylized_facts_report,
)
from tezcat.calibration import (
    CalibrationError, CalibrationSpec, calibrate, validate_calibration,
)
from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.data import DataError, SeriesData, load_series_file, run_price_series
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Data providers
# ---------------------------------------------------------------------------
def _series_payload(**kw):
    base = dict(source_id="fix1", provider="file", instrument="SYN",
                date_range="synthetic", license="synthetic fixture",
                sampling="per step, close",
                prices=[100.0 + 0.1 * i for i in range(40)])
    base.update(kw)
    return base


def test_series_file_roundtrip_and_checksum(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_series_payload()))
    a, b = load_series_file(p), load_series_file(p)
    assert a.checksum == b.checksum and len(a.checksum) == 64
    assert a.lineage()["n_observations"] == 40
    # Content change changes the checksum.
    p.write_text(json.dumps(_series_payload(prices=[100.0 + 0.2 * i for i in range(40)])))
    assert load_series_file(p).checksum != a.checksum


def test_series_validation_rejects_bad_data(tmp_path):
    with pytest.raises(ValidationError, match="positive finite"):
        SeriesData.model_validate(_series_payload(prices=[100.0] * 30 + [-1.0] * 10))
    with pytest.raises(ValidationError, match="positive finite"):
        SeriesData.model_validate(_series_payload(prices=[100.0] * 39 + [float("nan")]))
    with pytest.raises(ValidationError, match="volumes length"):
        SeriesData.model_validate(_series_payload(volumes=[1.0] * 5))
    with pytest.raises(ValidationError):
        SeriesData.model_validate(_series_payload(prices=[100.0] * 10))  # too short
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(DataError, match="not valid JSON"):
        load_series_file(p)
    with pytest.raises(DataError, match="not found"):
        load_series_file(tmp_path / "missing.json")


def test_external_adapters_are_explicitly_absent():
    """Adapter isolation: unknown providers fail cleanly, offline."""
    with pytest.raises(ValidationError, match="not available"):
        SeriesData.model_validate(_series_payload(provider="alpaca"))


# ---------------------------------------------------------------------------
# Feature fixtures with known properties
# ---------------------------------------------------------------------------
def _prices_from_returns(returns, p0=100.0):
    prices = [p0]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return prices


def test_iid_gaussian_returns_have_no_structure():
    rng = random.Random(1)
    r = [rng.gauss(0, 0.01) for _ in range(4000)]
    f = extract_features(_prices_from_returns(r))
    assert abs(f["acf_returns_lag1"]) < 0.06          # no linear autocorrelation
    assert abs(f["acf_abs_returns_lag1"]) < 0.06      # no volatility clustering
    assert abs(f["return_excess_kurtosis"]) < 0.5     # thin tails
    assert f["return_std"] == pytest.approx(0.01, rel=0.1)


def test_volatility_clustered_series_detected():
    """Alternating calm/volatile blocks -> high |return| autocorrelation."""
    rng = random.Random(2)
    r = []
    for block in range(40):
        sigma = 0.002 if block % 2 == 0 else 0.03
        r.extend(rng.gauss(0, sigma) for _ in range(100))
    f = extract_features(_prices_from_returns(r))
    assert f["acf_abs_returns_lag1"] > 0.3
    assert f["volatility_clustering"] > 0.3
    assert f["return_excess_kurtosis"] > 1.0          # mixture -> heavy tails
    assert abs(f["acf_returns_lag1"]) < 0.06          # returns themselves stay unpredictable


def test_hill_estimator_recovers_pareto_alpha():
    rng = random.Random(3)
    alpha = 3.0
    xs = [rng.paretovariate(alpha) for _ in range(20000)]
    est = hill_tail_index(xs, tail_fraction=0.05)
    assert est == pytest.approx(alpha, rel=0.15)
    # Gaussian sample: much larger estimate (no power-law tail).
    gs = [rng.gauss(0, 1) for _ in range(20000)]
    # A Gaussian has no power-law tail; Hill reads it as much thinner
    # (empirically ~6 at this tail fraction) than the Pareto(3) sample.
    assert hill_tail_index(gs, tail_fraction=0.05) > 1.5 * alpha


def test_basic_primitives():
    assert log_returns([100, 110, 99]) == pytest.approx(
        [math.log(1.1), math.log(0.9)])
    assert max_drawdown([100, 120, 90, 110]) == pytest.approx(0.25)
    assert acf([1.0] * 50, 1) is None                 # zero variance
    assert excess_kurtosis([1.0, 2.0]) is None        # too short
    short = extract_features([100.0] * 10)
    assert "warning_too_short" in short


# ---------------------------------------------------------------------------
# Ensemble comparison
# ---------------------------------------------------------------------------
def _feature_ensemble(n=20, seed=5):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        r = [rng.gauss(0, 0.01) for _ in range(500)]
        out.append(extract_features(_prices_from_returns(r)))
    return out


def test_compare_flags_inside_and_outside_band():
    ensemble = _feature_ensemble()
    # A member-like series falls inside most bands...
    rng_like = random.Random(99)
    real_like = extract_features(
        _prices_from_returns([rng_like.gauss(0, 0.01) for _ in range(500)]))
    like = compare_features(real_like, ensemble)
    assert like["coverage"] > 0.5
    # ...a wildly different series falls outside the std band.
    rng = random.Random(100)
    real_far = extract_features(
        _prices_from_returns([rng.gauss(0, 0.08) for _ in range(500)]))
    far = compare_features(real_far, ensemble)
    assert far["features"]["return_std"]["inside_band"] is False
    assert abs(far["features"]["return_std"]["z_distance"]) > 3

    report = stylized_facts_report({"source_id": "x", "checksum": "abc"}, like)
    assert "Feature comparison" in report and "coverage" in report.lower()
    assert "partial match is the expected outcome" in report


def test_compare_requires_real_ensemble():
    with pytest.raises(ValueError, match=">=10 replications"):
        compare_features(_feature_ensemble(1)[0], _feature_ensemble(3))


# ---------------------------------------------------------------------------
# Calibration discipline
# ---------------------------------------------------------------------------
def _cal_config(noise=10):
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=noise),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        total_steps=150,
    )


def _series_from_config(source_id, seed_base, tick_size=0.05):
    """A pseudo-'real' target generated by the simulator itself (labeled)."""
    from tezcat.core.seeds import derive_seed
    from tezcat.engine.ecology import EcologyEngine
    from tezcat.experiments.schema import apply_overrides
    cfg = apply_overrides(_cal_config(), {"market.tick_size": tick_size})
    eng = EcologyEngine("target", cfg,
                        derive_seed(seed_base, "target", source_id))
    while not eng.done:
        eng.step()
    return SeriesData(source_id=source_id, provider="file",
                      instrument="SYN", date_range="synthetic",
                      license="synthetic self-target (labeled)",
                      sampling="per step, close",
                      prices=run_price_series(eng.snapshots)).with_checksum()


def test_no_validation_period_tuning_rejected():
    with pytest.raises(ValidationError, match="not permitted"):
        CalibrationSpec(parameter_grid={"total_steps": [80]},
                        target_features=["return_std"],
                        calibration_source_id="same",
                        validation_source_id="same")


def test_calibration_recovers_known_parameter_and_validates_oos():
    """Identifiability smoke test: the grid point that generated the target
    should win, and validation must run only on the held-out source."""
    # Tick size drives return_std strongly and monotonically, so it is
    # identifiable from a single target series. (Agent count is NOT — its
    # effect saturates below single-series noise; an identifiability fact
    # the F9 docs record.)
    spec = CalibrationSpec(
        parameter_grid={"market.tick_size": [0.01, 0.05, 0.25]},
        target_features=["return_std"],
        calibration_source_id="cal_period",
        validation_source_id="val_period",
        replications=4, max_evaluations=10, root_seed=7)
    cal_data = _series_from_config("cal_period", seed_base=1)
    val_data = _series_from_config("val_period", seed_base=2)

    result = calibrate(spec, _cal_config(), cal_data)
    assert result["frozen"] and not result["truncated"]
    assert result["n_evaluations"] == 3
    assert result["best_params"] == {"market.tick_size": 0.05}  # recovered

    v = validate_calibration(result, _cal_config(), val_data)
    assert v["out_of_sample_objective"] is not None
    assert v["validation_source"]["source_id"] == "val_period"

    # Validating on the calibration source is refused.
    with pytest.raises(CalibrationError, match="out-of-sample"):
        validate_calibration(result, _cal_config(), cal_data)
    # Validating on an undeclared source is refused.
    other = _series_from_config("other_period", seed_base=3)
    with pytest.raises(CalibrationError, match="pre-declared"):
        validate_calibration(result, _cal_config(), other)


def test_calibration_budget_truncates_explicitly():
    spec = CalibrationSpec(
        parameter_grid={"agents.0.count": [3, 6, 10, 15, 25]},
        target_features=["return_std"],
        calibration_source_id="cal_period",
        validation_source_id="val_period",
        replications=3, max_evaluations=2, root_seed=7)
    cal_data = _series_from_config("cal_period", seed_base=1)
    result = calibrate(spec, _cal_config(), cal_data)
    assert result["truncated"] is True
    assert result["n_evaluations"] == 2 and result["n_candidates"] == 5


def test_calibrate_rejects_mismatched_source():
    spec = CalibrationSpec(
        parameter_grid={"agents.0.count": [3]},
        target_features=["return_std"],
        calibration_source_id="cal_period",
        validation_source_id="val_period")
    wrong = _series_from_config("not_the_declared_one", seed_base=1)
    with pytest.raises(CalibrationError, match="does not match"):
        calibrate(spec, _cal_config(), wrong)
