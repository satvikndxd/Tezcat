"""Enterprise discounted-cash-flow valuation (S6).

Standard unlevered DCF with end-of-period discounting:

    PV(FCF)  = Σ_t  FCF_t / (1 + WACC)^t ,  t = 1..N
    TV_gordon = FCF_N (1+g) / (WACC − g)          [requires g < WACC]
    TV_exit   = EBITDA_N × exit multiple
    PV(TV)   = TV / (1 + WACC)^N
    EV       = PV(FCF) + PV(TV)
    Equity   = EV − net debt
    Per share = Equity / diluted shares

Validation is loud: WACC bounds, g < WACC for Gordon growth, positive
exit multiple with positive terminal EBITDA, positive share count. The
output records the terminal-value share of EV as a mandatory
disclosure — a DCF dominated by its terminal value should say so.
A mid-year discounting convention is a documented extension point, not
a hidden switch.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel
from tezcat.finance.forecast import ProjectedPeriod
from tezcat.finance.statements import CompanyFinancials, FinanceError


class DCFAssumptions(FrozenModel):
    """Explicit valuation assumptions (all analyst inputs)."""

    wacc: float = Field(..., gt=0, lt=0.50,
                        description="Discount rate; sanity-bounded")
    terminal_method: Literal["gordon_growth", "exit_multiple"]
    terminal_growth: Optional[float] = Field(
        None, gt=-0.10, lt=0.10, description="g for Gordon growth")
    exit_multiple: Optional[float] = Field(
        None, gt=0, description="EV/EBITDA exit multiple")
    rationale: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "DCFAssumptions":
        if self.terminal_method == "gordon_growth":
            if self.terminal_growth is None:
                raise FinanceError("gordon_growth requires terminal_growth")
            if self.terminal_growth >= self.wacc:
                raise FinanceError(
                    f"terminal growth ({self.terminal_growth:.2%}) must be "
                    f"strictly below WACC ({self.wacc:.2%}); the perpetuity "
                    "otherwise diverges")
        if self.terminal_method == "exit_multiple" and self.exit_multiple is None:
            raise FinanceError("exit_multiple method requires exit_multiple")
        return self


def run_dcf(financials: CompanyFinancials,
            projections: List[ProjectedPeriod],
            assumptions: DCFAssumptions) -> Dict[str, Any]:
    """Value the enterprise; returns a fully-disclosed derived payload."""
    if not projections:
        raise FinanceError("DCF requires at least one projected period")
    wacc = assumptions.wacc
    n = len(projections)

    discounted = []
    pv_fcf = 0.0
    for p in projections:
        factor = (1.0 + wacc) ** p.year
        pv = p.unlevered_fcf / factor
        pv_fcf += pv
        discounted.append({"year": p.year, "fcf": p.unlevered_fcf,
                           "discount_factor": round(1.0 / factor, 8),
                           "pv": round(pv, 6)})

    terminal_fcf = projections[-1].unlevered_fcf
    terminal_ebitda = projections[-1].ebitda
    if assumptions.terminal_method == "gordon_growth":
        g = assumptions.terminal_growth
        if terminal_fcf <= 0:
            raise FinanceError(
                f"terminal-year FCF is {terminal_fcf:.2f} <= 0; a Gordon "
                "perpetuity on non-positive FCF is meaningless — extend the "
                "horizon or use an exit multiple with positive EBITDA")
        terminal_value = terminal_fcf * (1.0 + g) / (wacc - g)
    else:
        if terminal_ebitda <= 0:
            raise FinanceError(
                f"terminal-year EBITDA is {terminal_ebitda:.2f} <= 0; an "
                "exit multiple cannot be applied")
        terminal_value = terminal_ebitda * assumptions.exit_multiple

    pv_terminal = terminal_value / (1.0 + wacc) ** n
    enterprise_value = pv_fcf + pv_terminal
    if enterprise_value <= 0:
        raise FinanceError(
            f"enterprise value is {enterprise_value:.2f} <= 0 under these "
            "assumptions — the model refuses to report it as a valuation; "
            "revisit the forecast")

    net_debt = financials.latest.net_debt
    equity_value = enterprise_value - net_debt
    shares = financials.latest.diluted_shares
    value_per_share = equity_value / shares

    return {
        "kind": "derived",
        "method": "unlevered_dcf",
        "discounting": "end_of_period",
        "assumptions": assumptions.model_dump(mode="json"),
        "horizon_years": n,
        "discounted_fcf": discounted,
        "pv_forecast_fcf": round(pv_fcf, 6),
        "terminal_value_undiscounted": round(terminal_value, 6),
        "pv_terminal_value": round(pv_terminal, 6),
        "terminal_value_pct_of_ev": round(pv_terminal / enterprise_value, 6),
        "enterprise_value": round(enterprise_value, 6),
        "net_debt": round(net_debt, 6),
        "equity_value": round(equity_value, 6),
        "diluted_shares": shares,
        "implied_value_per_share": round(value_per_share, 6),
        "disclosure": ("implied values inherit the uncertainty of every "
                       "assumption above; see terminal_value_pct_of_ev for "
                       "terminal-value dependence"),
    }
