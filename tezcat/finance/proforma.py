"""Pro forma earnings and accretion/dilution (S6).

The pro forma model is a transparent per-year **bridge**, not a black
box: every component that turns standalone earnings into pro forma
earnings is itemized, and the sum of components must reconcile exactly
with the reported pro forma net income (invariant, tested).

Per forecast year t (1..N):

    acquirer standalone NI_t   (explicit growth assumptions)
  + target standalone NI_t     (explicit growth assumptions)
  + after-tax synergies_t      run-rate × phase-in × (1 − tax)
  − after-tax incremental interest on new debt
  − after-tax foregone interest on balance-sheet cash deployed
  − after-tax financing-fee amortization (straight-line)
  = pro forma net income_t

    standalone EPS_t = acquirer NI_t / acquirer diluted shares
    pro forma EPS_t  = pro forma NI_t / (acquirer + new shares)
    accretion_t      = pro forma EPS_t / standalone EPS_t − 1

One-time advisory fees are disclosed but excluded from recurring EPS
(standard practice; the exclusion is explicit in the output, not
silent). Debt principal is held constant across the horizon
(no amortization schedule in v1 — a documented extension point).
Verdict labels: accretive / dilutive / approximately neutral, with the
neutrality threshold (|Δ| < 0.5%) stated in the output.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel
from tezcat.finance.statements import CompanyFinancials, FinanceError

NEUTRAL_THRESHOLD = 0.005  # |accretion| below this → "approximately neutral"


class EarningsGrowthAssumptions(FrozenModel):
    """Explicit standalone net-income growth paths for both companies."""

    acquirer_growth: List[float] = Field(..., min_length=1)
    target_growth: List[float] = Field(..., min_length=1)
    rationale: str = ""

    @property
    def horizon(self) -> int:
        return len(self.acquirer_growth)

    @model_validator(mode="after")
    def _validate(self) -> "EarningsGrowthAssumptions":
        if len(self.target_growth) != self.horizon:
            raise FinanceError("acquirer_growth and target_growth must have "
                               "the same horizon")
        for name in ("acquirer_growth", "target_growth"):
            for i, g in enumerate(getattr(self, name)):
                if g <= -1.0:
                    raise FinanceError(f"{name}[{i}] = {g} implies "
                                       "non-positive earnings")
        return self


def build_pro_forma(acquirer: CompanyFinancials, target: CompanyFinancials,
                    transaction: Dict[str, Any],
                    growth: EarningsGrowthAssumptions) -> Dict[str, Any]:
    """Multi-year pro forma bridge and accretion/dilution verdicts."""
    assumptions = transaction["assumptions"]
    tax = assumptions["tax_rate"]
    after_tax = 1.0 - tax
    new_debt = transaction["sources"]["new_debt"]
    cash_used = transaction["sources"]["acquirer_balance_sheet_cash"]
    pf_shares = transaction["pro_forma_shares"]
    acq_shares = acquirer.latest.diluted_shares
    if pf_shares <= 0 or acq_shares <= 0:
        raise FinanceError("impossible share counts in pro forma model")

    acq_ni = acquirer.latest.net_income
    tgt_ni = target.latest.net_income
    if acq_ni <= 0:
        raise FinanceError(
            f"acquirer standalone net income is {acq_ni:.2f} <= 0; EPS "
            "accretion analysis is not meaningful — model earnings first")

    synergy = assumptions["synergies"]
    fee_amort_years = assumptions["financing_fee_amortization_years"]
    fee_amort_annual = assumptions["financing_fees"] / fee_amort_years

    incr_interest_at = new_debt * assumptions["cost_of_new_debt"] * after_tax
    foregone_at = cash_used * assumptions["cash_yield"] * after_tax

    years: List[Dict[str, Any]] = []
    for t in range(growth.horizon):
        acq_ni *= (1.0 + growth.acquirer_growth[t])
        tgt_ni *= (1.0 + growth.target_growth[t])
        phase = (synergy["phase_in"][t]
                 if t < len(synergy["phase_in"])
                 else synergy["phase_in"][-1])
        synergies_at = synergy["run_rate_pretax"] * phase * after_tax
        fee_at = (fee_amort_annual * after_tax
                  if t < fee_amort_years else 0.0)

        bridge = {
            "acquirer_standalone_ni": round(acq_ni, 6),
            "target_standalone_ni": round(tgt_ni, 6),
            "after_tax_synergies": round(synergies_at, 6),
            "after_tax_incremental_interest": round(-incr_interest_at, 6),
            "after_tax_foregone_cash_interest": round(-foregone_at, 6),
            "after_tax_financing_fee_amortization": round(-fee_at, 6),
        }
        pf_ni = sum(bridge.values())
        standalone_eps = acq_ni / acq_shares
        pf_eps = pf_ni / pf_shares
        accretion = pf_eps / standalone_eps - 1.0
        verdict = ("approximately neutral"
                   if abs(accretion) < NEUTRAL_THRESHOLD
                   else "accretive" if accretion > 0 else "dilutive")
        years.append({
            "year": t + 1,
            "bridge": bridge,
            "pro_forma_net_income": round(pf_ni, 6),
            "standalone_eps": round(standalone_eps, 6),
            "pro_forma_eps": round(pf_eps, 6),
            "eps_impact": round(pf_eps - standalone_eps, 6),
            "accretion_dilution_pct": round(accretion, 6),
            "verdict": verdict,
        })

    return {
        "kind": "derived",
        "method": "pro_forma_eps_bridge",
        "horizon_years": growth.horizon,
        "pro_forma_shares": pf_shares,
        "acquirer_shares": acq_shares,
        "new_shares_issued": transaction["new_shares_issued"],
        "one_time_advisory_fees_excluded_from_recurring_eps":
            assumptions["advisory_fees"],
        "neutrality_threshold": NEUTRAL_THRESHOLD,
        "growth_assumptions": growth.model_dump(mode="json"),
        "years": years,
        "year_1_verdict": years[0]["verdict"],
        "disclosure": ("bridge components sum exactly to pro forma net "
                       "income (tested invariant); debt principal held "
                       "constant over the horizon; purchase accounting "
                       "(goodwill, step-up D&A) is a documented extension "
                       "point, not silently approximated"),
    }
