"""The ValuationCase research object and its execution (S6).

A case bundles everything one analysis needs — target financials,
forecast assumptions, valuation assumptions, optional comps/precedents
blocks, an optional transaction block, scenarios, and sensitivity
specifications — into one immutable, content-addressed research object:

    case_hash = SHA256(finance model version ‖ canonical spec)
    case id   = fin_<hash[:12]>

Execution is a pure deterministic function of the spec.
:func:`run_case` produces the complete derived results (forecast, DCF,
comps, precedents, triangulation, transaction, pro forma, scenarios,
sensitivities); :func:`register_case` persists spec and results through
the S5 research artifact graph (financial facts → case → outputs →
report, all hash-linked); :func:`reproduce_case` re-runs the persisted
spec and verifies the output artifact hash byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from tezcat.core.config import FrozenModel, canonical_json
from tezcat.finance.comps import CompsSelection, PeerCompany, run_comps
from tezcat.finance.dcf import DCFAssumptions, run_dcf
from tezcat.finance.forecast import ForecastAssumptions, build_forecast
from tezcat.finance.precedents import (
    PrecedentSelection, PrecedentTransaction, run_precedents,
)
from tezcat.finance.proforma import EarningsGrowthAssumptions, build_pro_forma
from tezcat.finance.scenarios import Scenario, apply_spec_overrides
from tezcat.finance.sensitivity import SensitivitySpec, run_sensitivity
from tezcat.finance.statements import (
    FINANCE_MODEL_VERSION, CompanyFinancials, FinanceError,
)
from tezcat.finance.transaction import (
    TransactionAssumptions, structure_transaction,
)


class ValuationCase(FrozenModel):
    """Validated, immutable case specification."""

    case_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    analyst_note: str = ""
    target: CompanyFinancials
    forecast: ForecastAssumptions
    dcf: DCFAssumptions
    comps_peers: List[PeerCompany] = Field(default_factory=list)
    comps_selection: Optional[CompsSelection] = None
    precedent_transactions: List[PrecedentTransaction] = Field(
        default_factory=list)
    precedent_selection: Optional[PrecedentSelection] = None
    acquirer: Optional[CompanyFinancials] = None
    transaction: Optional[TransactionAssumptions] = None
    earnings_growth: Optional[EarningsGrowthAssumptions] = None
    scenarios: List[Scenario] = Field(default_factory=list)
    sensitivities: List[SensitivitySpec] = Field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: Dict[str, Any]) -> "ValuationCase":
        try:
            return cls.model_validate(spec)
        except FinanceError:
            raise
        except Exception as exc:  # pydantic ValidationError → domain error
            raise FinanceError(f"invalid case spec: {exc}") from exc

    def spec_dict(self) -> Dict[str, Any]:
        return json.loads(canonical_json(self.model_dump(mode="json")))

    def case_hash(self) -> str:
        payload = {"finance_model_version": FINANCE_MODEL_VERSION,
                   "spec": self.spec_dict()}
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    @property
    def version_id(self) -> str:
        return f"fin_{self.case_hash()[:12]}"


# ---------------------------------------------------------------------------
# Execution (pure, deterministic)
# ---------------------------------------------------------------------------
def _run_pipeline(case: ValuationCase) -> Dict[str, Any]:
    """Base analytical pipeline (no scenarios/sensitivities recursion)."""
    projections = build_forecast(case.target, case.forecast)
    results: Dict[str, Any] = {
        "kind": "derived",
        "finance_model_version": FINANCE_MODEL_VERSION,
        "target_summary": case.target.summary(),
        "forecast": {
            "assumptions": case.forecast.model_dump(mode="json"),
            "projections": [p.model_dump(mode="json") for p in projections],
        },
        "dcf": run_dcf(case.target, projections, case.dcf),
    }

    if case.comps_peers:
        if case.comps_selection is None:
            raise FinanceError("comps peers supplied without a selection")
        results["comps"] = run_comps(case.target, case.comps_peers,
                                     case.comps_selection)
    if case.precedent_transactions:
        if case.precedent_selection is None:
            raise FinanceError("precedent transactions supplied without a "
                               "selection")
        results["precedents"] = run_precedents(
            case.target, case.precedent_transactions,
            case.precedent_selection)

    # -- valuation triangulation ---------------------------------------
    methods = [("dcf", results["dcf"]["implied_value_per_share"])]
    if "comps" in results:
        methods.append(("trading_comps",
                        results["comps"]["implied_value_per_share"]))
    if "precedents" in results:
        methods.append(("precedent_transactions",
                        results["precedents"]["implied_value_per_share"]))
    values = [v for _, v in methods]
    results["triangulation"] = {
        "kind": "derived",
        "methods": [{"method": m, "implied_value_per_share": v}
                    for m, v in methods],
        "low": round(min(values), 6),
        "high": round(max(values), 6),
        "midpoint": round(sum(values) / len(values), 6),
        "current_share_price": case.target.share_price,
        "note": "the range across methodologies is the result; the "
                "midpoint is a summary, not a target price",
    }

    if case.transaction is not None:
        if case.acquirer is None or case.earnings_growth is None:
            raise FinanceError("transaction analysis requires acquirer "
                               "financials and earnings growth assumptions")
        txn = structure_transaction(case.acquirer, case.target,
                                    case.transaction)
        results["transaction"] = txn
        results["pro_forma"] = build_pro_forma(case.acquirer, case.target,
                                               txn, case.earnings_growth)
    return results


def run_case(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Full case execution: base pipeline + scenarios + sensitivities."""
    case = ValuationCase.from_spec(spec)
    base_spec = case.spec_dict()
    results = _run_pipeline(case)

    def scoped_runner(overridden_spec: Dict[str, Any]) -> Dict[str, Any]:
        scoped = dict(overridden_spec)
        scoped["scenarios"], scoped["sensitivities"] = [], []
        return _run_pipeline(ValuationCase.from_spec(scoped))

    if case.scenarios:
        rows = []
        for scenario in case.scenarios:
            scenario_spec = apply_spec_overrides(base_spec,
                                                 scenario.overrides)
            r = scoped_runner(scenario_spec)
            row = {
                "scenario": scenario.name,
                "description": scenario.description,
                "overrides": scenario.overrides,
                "dcf_value_per_share": r["dcf"]["implied_value_per_share"],
                "dcf_enterprise_value": r["dcf"]["enterprise_value"],
                "terminal_value_pct_of_ev":
                    r["dcf"]["terminal_value_pct_of_ev"],
            }
            if "pro_forma" in r:
                row["year_1_accretion"] = \
                    r["pro_forma"]["years"][0]["accretion_dilution_pct"]
                row["year_1_verdict"] = r["pro_forma"]["year_1_verdict"]
            rows.append(row)
        per_share = [r["dcf_value_per_share"] for r in rows]
        results["scenario_analysis"] = {
            "kind": "derived",
            "scenarios": rows,
            "value_per_share_low": round(min(per_share), 6),
            "value_per_share_high": round(max(per_share), 6),
            "spread": round(max(per_share) - min(per_share), 6),
            "note": "scenarios are declared assumption deltas over the "
                    "base spec; the spread is a mandatory disclosure",
        }

    if case.sensitivities:
        results["sensitivities"] = [
            run_sensitivity(base_spec, s, scoped_runner)
            for s in case.sensitivities]

    results["case"] = {"case_id": case.case_id, "name": case.name,
                       "version_id": case.version_id,
                       "case_hash": case.case_hash()}
    return results


# ---------------------------------------------------------------------------
# Artifact integration (S5 research graph — one provenance system)
# ---------------------------------------------------------------------------
def register_case(data_dir: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Execute and persist: financial facts → case → outputs → report."""
    from tezcat.finance.report import render_report
    from tezcat.plane.artifacts import ArtifactGraph

    case = ValuationCase.from_spec(spec)
    graph = ArtifactGraph(data_dir)

    fin_parents = []
    target_art = graph.register(
        "company_financials",
        {"role": "target", **case.target.model_dump(mode="json")},
        config={"role": "target", "name": case.target.name},
        external_identity=case.target.content_hash())
    fin_parents.append(target_art)
    if case.acquirer is not None:
        fin_parents.append(graph.register(
            "company_financials",
            {"role": "acquirer", **case.acquirer.model_dump(mode="json")},
            config={"role": "acquirer", "name": case.acquirer.name},
            external_identity=case.acquirer.content_hash()))

    case_art = graph.register(
        "valuation_case", case.spec_dict(),
        config={"case_id": case.case_id, "name": case.name,
                "finance_model_version": FINANCE_MODEL_VERSION},
        parents=fin_parents, external_identity=case.case_hash())

    results = run_case(spec)
    output_art = graph.register(
        "valuation_output", results,
        config={"case_id": case.case_id,
                "finance_model_version": FINANCE_MODEL_VERSION},
        parents=[case_art])

    markdown = render_report(case, results, case_art, output_art)
    report_art = graph.register(
        "finance_report", {"markdown": markdown},
        config={"case_id": case.case_id},
        parents=[output_art])
    return {"case_artifact": case_art.artifact_id,
            "output_artifact": output_art.artifact_id,
            "report_artifact": report_art.artifact_id,
            "version_id": case.version_id,
            "case_hash": case.case_hash(),
            "results": results,
            "markdown": markdown}


def reproduce_case(data_dir: str, case_artifact_id: str) -> Dict[str, Any]:
    """Re-run the persisted spec; verify output + report hashes exactly."""
    from tezcat.plane.artifacts import ArtifactGraph

    graph = ArtifactGraph(data_dir)
    case_art = graph.get(case_artifact_id)
    if case_art.artifact_type != "valuation_case":
        raise FinanceError(f"{case_artifact_id} is not a valuation_case")
    spec = graph.payload(case_artifact_id)

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    case = ValuationCase.from_spec(spec)
    check("case hash re-verified", case.case_hash() == case_art.external_identity,
          case.version_id)

    stored_children = [c for c in graph.children(case_artifact_id)
                       if c["artifact_type"] == "valuation_output"]
    if not stored_children:
        raise FinanceError("no stored valuation_output for this case")
    rerun = register_case(data_dir, spec)  # idempotent for identical content
    ok = any(c["artifact_id"] == rerun["output_artifact"]
             for c in stored_children)
    check("valuation output reproduces", ok,
          rerun["output_artifact"])
    success = all(c["ok"] for c in checks)
    return {"case_artifact": case_artifact_id, "success": success,
            "checks": checks, "output_artifact": rerun["output_artifact"],
            "report_artifact": rerun["report_artifact"]}
