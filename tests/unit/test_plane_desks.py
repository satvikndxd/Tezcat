"""S5-B/C: forecasting + portfolio desks — contracts, determinism, honesty."""

import math

import pytest

from tezcat.plane import ArtifactGraph, PlaneError
from tezcat.plane.forecasting import (
    SeededBlockBootstrapModel, evaluate_forecast, get_model,
    make_forecast_artifact,
)
from tezcat.plane.portfolio import (
    CostModel, PortfolioConstraints, PortfolioView, ReferenceCvarOptimizer,
    get_optimizer, make_portfolio_artifact,
)


def history(n=200, drift=0.003, seed=3):
    import random
    rng = random.Random(seed)
    prices, p = [], 100.0
    for _ in range(n):
        p *= math.exp(rng.gauss(drift, 0.01))
        prices.append(round(p, 6))
    return prices


@pytest.fixture()
def forecast_payload():
    payload = SeededBlockBootstrapModel().forecast(history(), horizon=30,
                                                   n_paths=100, seed=11)
    payload["instrument"] = "TZC"  # added by make_forecast_artifact normally
    return payload


class TestForecastDesk:
    def test_deterministic(self):
        first = SeededBlockBootstrapModel().forecast(history(), 30, 100, 11)
        again = SeededBlockBootstrapModel().forecast(history(), 30, 100, 11)
        assert again == first
        different = SeededBlockBootstrapModel().forecast(history(), 30, 100, 12)
        assert different != first

    def test_uncertainty_is_first_class(self, forecast_payload):
        t = forecast_payload["terminal"]
        for field in ("mean_return", "median_return", "dispersion", "q05",
                      "q25", "q75", "q95", "prob_up"):
            assert field in t
        assert t["q05"] < t["q95"]
        assert t["dispersion"] > 0
        assert len(forecast_payload["forecast_paths"]) == 100
        assert len(forecast_payload["step_quantiles"]) == 30

    def test_no_predictive_claim(self, forecast_payload):
        assert "NOT a prediction" in forecast_payload["disclaimer"]
        assert "NOT an order" in forecast_payload["disclaimer"]

    def test_distribution_requires_samples(self):
        with pytest.raises(PlaneError, match="n_paths >= 20"):
            SeededBlockBootstrapModel().forecast(history(), 30, 5, 1)

    def test_short_history_rejected(self):
        with pytest.raises(PlaneError, match="too short"):
            SeededBlockBootstrapModel().forecast([100.0] * 10, 30, 100, 1)

    def test_model_registry(self):
        model = get_model("bootstrap_reference", {"block_size": 5})
        assert model.block_size == 5
        with pytest.raises(PlaneError, match="ForecastModel protocol"):
            get_model("kronos")

    def test_artifact_links_dataset(self, tmp_path, forecast_payload):
        graph = ArtifactGraph(str(tmp_path))
        ds = graph.register("external_dataset", {"prices": history()})
        fc = make_forecast_artifact(graph, dataset_artifact=ds,
                                    prices=history(), instrument="TZC",
                                    model=SeededBlockBootstrapModel(),
                                    horizon=30, n_paths=100, seed=11)
        assert fc.parent_hashes == [ds.artifact_hash]
        assert graph.payload(fc.artifact_id)["instrument"] == "TZC"


class TestForecastQuality:
    def test_metrics_and_separation_note(self, forecast_payload):
        realized = history(40, drift=0.0015, seed=99)[:30]
        quality = evaluate_forecast(forecast_payload, realized)
        for field in ("mae_median_path", "rmse_median_path",
                      "interval_coverage_q05_q95", "directional_hit",
                      "crps_terminal"):
            assert field in quality
        assert 0 <= quality["interval_coverage_q05_q95"] <= 1
        assert "does not imply" in quality["note"]

    def test_realized_too_short_rejected(self, forecast_payload):
        with pytest.raises(PlaneError, match="horizon"):
            evaluate_forecast(forecast_payload, [100.0] * 5)


class TestPortfolioDesk:
    @pytest.fixture()
    def view(self, forecast_payload):
        return PortfolioView.from_forecast(forecast_payload, "f" * 64)

    def test_view_from_forecast(self, view, forecast_payload):
        assert view.expected_return == \
            forecast_payload["terminal"]["mean_return"]
        assert len(view.sample_returns) == 100

    def test_reference_optimizer_deterministic(self, view):
        opt = ReferenceCvarOptimizer(risk_aversion=1.0)
        a = opt.optimize(view, PortfolioConstraints(), CostModel(), 0.0)
        b = opt.optimize(view, PortfolioConstraints(), CostModel(), 0.0)
        assert a == b

    def test_costs_enter_before_performance(self, view):
        cheap = ReferenceCvarOptimizer(risk_aversion=0.0).optimize(
            view, PortfolioConstraints(),
            CostModel(transaction_cost_bps=0.0), 0.0)
        expensive = ReferenceCvarOptimizer(risk_aversion=0.0).optimize(
            view, PortfolioConstraints(),
            CostModel(transaction_cost_bps=10_000.0), 0.0)
        assert cheap["risky_weight"] > expensive["risky_weight"]
        assert cheap["gross_expected_return"] >= cheap["net_expected_return"]

    def test_turnover_constraint(self, view):
        result = ReferenceCvarOptimizer(risk_aversion=0.0).optimize(
            view, PortfolioConstraints(max_turnover=0.1),
            CostModel(transaction_cost_bps=0.0), previous_weight=0.2)
        assert 0.1 <= result["risky_weight"] <= 0.3 + 1e-9
        assert result["turnover"] <= 0.1 + 1e-9

    def test_decision_trace_reconstructs_why(self, view):
        result = ReferenceCvarOptimizer().optimize(
            view, PortfolioConstraints(), CostModel(), 0.0)
        steps = [row["step"] for row in result["decision_trace"]]
        assert steps == ["view", "risk_measure", "constraints", "costs",
                         "decision"]
        assert result["risk_measure"] == "CVaR95"

    def test_artifact_answers_why_this_weight(self, tmp_path, view,
                                              forecast_payload):
        graph = ArtifactGraph(str(tmp_path))
        ds = graph.register("external_dataset", {"prices": [1]})
        fc = graph.register("forecast", forecast_payload, parents=[ds])
        pf = make_portfolio_artifact(
            graph, forecast_artifact=fc, view=view,
            optimizer=ReferenceCvarOptimizer(),
            constraints=PortfolioConstraints(), costs=CostModel())
        assert pf.parent_hashes == [fc.artifact_hash]
        assert "constraints_hash" in pf.config
        payload = graph.payload(pf.artifact_id)
        assert payload["forecast_artifact_hash"] == fc.artifact_hash
        assert payload["decision_trace"]

    def test_skfolio_adapter_same_contract(self, view):
        pytest.importorskip("skfolio", reason="optional portfolio adapter")
        result = get_optimizer("skfolio_cvar").optimize(
            view, PortfolioConstraints(), CostModel(), 0.0)
        assert 0.0 <= result["risky_weight"] <= 1.0
        assert result["risk_measure"] == "CVaR95"
        assert result["decision_trace"]
        assert "skfolio_version" in result["optimizer_config"]

    def test_skfolio_zero_allocation_is_explicit(self):
        pytest.importorskip("skfolio", reason="optional portfolio adapter")
        bearish = PortfolioView(
            instrument="TZC", horizon=10, expected_return=-0.05,
            dispersion=0.01, q05=-0.07, q95=-0.03,
            sample_returns=[-0.05 + 0.0001 * i for i in range(40)],
            forecast_hash="f" * 64)
        result = get_optimizer("skfolio_cvar").optimize(
            bearish, PortfolioConstraints(), CostModel(), 0.0)
        assert result["risky_weight"] == 0.0
        assert result["refused"] is not None
