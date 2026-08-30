"""S6 acceptance: full valuation/transaction case, artifacts, reproduction."""

import json
from pathlib import Path

import pytest

from tezcat.finance.case import (
    ValuationCase, register_case, reproduce_case, run_case,
)
from tezcat.finance.scenarios import apply_spec_overrides
from tezcat.finance.statements import FinanceError
from tezcat.plane.artifacts import ArtifactGraph

SPEC_PATH = (Path(__file__).resolve().parents[2] / "examples" / "finance"
             / "meridian_case.json")


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC_PATH.read_text())


@pytest.fixture(scope="module")
def results(spec):
    return run_case(spec)


class TestCaseExecution:
    def test_deterministic(self, spec, results):
        again = run_case(spec)
        assert again == results

    def test_case_identity_content_addressed(self, spec):
        case = ValuationCase.from_spec(spec)
        assert case.version_id.startswith("fin_")
        changed = apply_spec_overrides(spec, {"dcf.wacc": 0.10})
        assert ValuationCase.from_spec(changed).case_hash() != \
            case.case_hash()

    def test_triangulation_spans_methods(self, results):
        tri = results["triangulation"]
        assert len(tri["methods"]) == 3
        values = [m["implied_value_per_share"] for m in tri["methods"]]
        assert tri["low"] == pytest.approx(min(values))
        assert tri["high"] == pytest.approx(max(values))
        # precedent multiples embed control premia → highest method here
        by_method = {m["method"]: m["implied_value_per_share"]
                     for m in tri["methods"]}
        assert by_method["precedent_transactions"] > by_method["dcf"]

    def test_transaction_reconciles(self, results):
        txn = results["transaction"]
        assert txn["total_sources"] == pytest.approx(txn["total_uses"])
        own = txn["ownership"]
        assert own["acquirer_shareholders"] + own["target_shareholders"] == \
            pytest.approx(1.0)

    def test_pro_forma_bridge_reconciles_every_year(self, results):
        for y in results["pro_forma"]["years"]:
            assert y["pro_forma_net_income"] == pytest.approx(
                sum(y["bridge"].values()), abs=1e-6)

    def test_scenarios_ordered_and_disclosed(self, results):
        sa = results["scenario_analysis"]
        by_name = {r["scenario"]: r for r in sa["scenarios"]}
        assert by_name["downside"]["dcf_value_per_share"] < \
            by_name["base"]["dcf_value_per_share"] < \
            by_name["upside"]["dcf_value_per_share"]
        assert sa["spread"] == pytest.approx(
            sa["value_per_share_high"] - sa["value_per_share_low"], abs=1e-6)

    def test_sensitivity_grid_monotone_in_wacc(self, results):
        grid = results["sensitivities"][0]["grid"]  # rows: rising WACC
        mid_col = len(grid[0]) // 2
        column = [row[mid_col] for row in grid]
        assert all(a > b for a, b in zip(column, column[1:]))

    def test_sensitivity_isolation(self, spec, results):
        """Changing WACC must not move comps/precedents/transaction."""
        changed = run_case(apply_spec_overrides(spec, {"dcf.wacc": 0.105}))
        assert changed["dcf"]["implied_value_per_share"] != \
            results["dcf"]["implied_value_per_share"]
        assert changed["comps"] == results["comps"]
        assert changed["precedents"] == results["precedents"]
        assert changed["transaction"] == results["transaction"]


class TestFailuresLoud:
    def test_unknown_scenario_path(self, spec):
        with pytest.raises(FinanceError, match="unknown segment"):
            apply_spec_overrides(spec, {"forecast.revenue_groth": [0.1]})

    def test_invalid_wacc_combination(self, spec):
        bad = apply_spec_overrides(spec, {"dcf.terminal_growth": 0.12})
        with pytest.raises(FinanceError):
            run_case(bad)

    def test_invalid_sensitivity_cells_reported_not_interpolated(self, spec):
        bad = apply_spec_overrides(
            spec, {"sensitivities.0.axis2.values":
                   [0.02, 0.025, 0.09, 0.10, 0.12]})
        results = run_case(bad)
        s = results["sensitivities"][0]
        assert s["n_invalid_cells"] > 0
        flat = [c for row in s["grid"] for c in row]
        assert any(isinstance(c, str) and c.startswith("invalid") for c in flat)


class TestArtifactsAndReproduction:
    @pytest.fixture(scope="class")
    def registered(self, tmp_path_factory, spec):
        td = str(tmp_path_factory.mktemp("finance"))
        return td, register_case(td, spec)

    def test_lineage_chain(self, registered):
        td, reg = registered
        graph = ArtifactGraph(td)
        chain = graph.lineage(reg["report_artifact"])
        types = [a.artifact_type for a in chain]
        assert types.count("company_financials") == 2  # target + acquirer
        for t in ("valuation_case", "valuation_output", "finance_report"):
            assert t in types
        case_node = next(a for a in chain
                         if a.artifact_type == "valuation_case")
        assert case_node.external_identity == \
            ValuationCase.from_spec(graph.payload(case_node.artifact_id)
                                    ).case_hash()

    def test_report_rendered_from_persisted_content(self, registered):
        td, reg = registered
        graph = ArtifactGraph(td)
        md = graph.payload(reg["report_artifact"])["markdown"]
        for section in ("Executive summary", "Historical financial analysis",
                        "Triangulation", "Accretion / dilution",
                        "Scenario analysis", "Sensitivity analysis",
                        "Risk analysis", "Methodology and reproducibility"):
            assert section in md
        assert "not investment advice" in md
        assert reg["case_artifact"] in md

    def test_reproduce_case(self, registered):
        td, reg = registered
        rep = reproduce_case(td, reg["case_artifact"])
        assert rep["success"], rep["checks"]
        assert rep["output_artifact"] == reg["output_artifact"]

    def test_registration_idempotent(self, registered, spec):
        td, reg = registered
        again = register_case(td, spec)
        assert again["case_artifact"] == reg["case_artifact"]
        assert again["output_artifact"] == reg["output_artifact"]
