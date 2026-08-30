"""Operating forecast: explicit assumptions → projected periods (S6).

Every driver is an **explicit analyst assumption** — one value per
forecast year, stated in the spec, never buried in a formula. The
projection is a pure deterministic function of (historical anchor,
assumptions); projected periods are labeled derived.

Driver set (deliberately parsimonious; each is documented in
docs/finance.md):

    revenue_growth[t]     revenue_t = revenue_{t-1} × (1 + g_t)
    ebitda_margin[t]      EBITDA_t = revenue_t × margin_t
    da_pct_revenue[t]     D&A_t = revenue_t × pct
    capex_pct_revenue[t]  capex_t = revenue_t × pct
    nwc_pct_revenue[t]    NWC level_t = revenue_t × pct (ΔNWC derived)
    tax_rate              single marginal rate for unlevered FCF

Unlevered FCF_t = EBIT_t (1 − tax) + D&A_t − capex_t − ΔNWC_t.
"""

from __future__ import annotations

from typing import List

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel
from tezcat.finance.statements import CompanyFinancials, FinanceError


class ForecastAssumptions(FrozenModel):
    """Per-year operating drivers. Lengths must agree (= horizon)."""

    revenue_growth: List[float] = Field(..., min_length=1)
    ebitda_margin: List[float] = Field(..., min_length=1)
    da_pct_revenue: List[float] = Field(..., min_length=1)
    capex_pct_revenue: List[float] = Field(..., min_length=1)
    nwc_pct_revenue: List[float] = Field(..., min_length=1)
    tax_rate: float = Field(..., ge=0, lt=1)
    rationale: str = Field("", description="Analyst note on driver basis")

    @property
    def horizon(self) -> int:
        return len(self.revenue_growth)

    @model_validator(mode="after")
    def _validate(self) -> "ForecastAssumptions":
        n = self.horizon
        for name in ("ebitda_margin", "da_pct_revenue", "capex_pct_revenue",
                     "nwc_pct_revenue"):
            if len(getattr(self, name)) != n:
                raise FinanceError(
                    f"assumption vector {name!r} has "
                    f"{len(getattr(self, name))} entries; horizon is {n} — "
                    "every driver needs one value per forecast year")
        for i, g in enumerate(self.revenue_growth):
            if g <= -1.0:
                raise FinanceError(f"revenue_growth[{i}] = {g} implies "
                                   "non-positive revenue")
        for i, m in enumerate(self.ebitda_margin):
            if not -1.0 < m < 1.0:
                raise FinanceError(f"ebitda_margin[{i}] = {m} outside "
                                   "(-100%, 100%)")
        for name in ("da_pct_revenue", "capex_pct_revenue"):
            for i, v in enumerate(getattr(self, name)):
                if not 0.0 <= v < 1.0:
                    raise FinanceError(f"{name}[{i}] = {v} outside [0, 100%)")
        return self


class ProjectedPeriod(FrozenModel):
    """One derived forecast year (kind is fixed: these are not facts)."""

    kind: str = "derived_forecast"
    year: int = Field(..., ge=1, description="Forecast year index (1-based)")
    revenue: float
    ebitda: float
    depreciation_amortization: float
    ebit: float
    nopat: float
    capex: float
    nwc_level: float
    change_in_nwc: float
    unlevered_fcf: float


def build_forecast(financials: CompanyFinancials,
                   assumptions: ForecastAssumptions) -> List[ProjectedPeriod]:
    """Project operating periods from the latest historical anchor."""
    anchor = financials.latest
    revenue = anchor.revenue
    nwc_prev = anchor.net_working_capital
    tax = assumptions.tax_rate
    out: List[ProjectedPeriod] = []
    for i in range(assumptions.horizon):
        revenue = revenue * (1.0 + assumptions.revenue_growth[i])
        ebitda = revenue * assumptions.ebitda_margin[i]
        da = revenue * assumptions.da_pct_revenue[i]
        ebit = ebitda - da
        nopat = ebit * (1.0 - tax)
        capex = revenue * assumptions.capex_pct_revenue[i]
        nwc = revenue * assumptions.nwc_pct_revenue[i]
        change_nwc = nwc - nwc_prev
        fcf = nopat + da - capex - change_nwc
        out.append(ProjectedPeriod(
            year=i + 1,
            revenue=round(revenue, 6), ebitda=round(ebitda, 6),
            depreciation_amortization=round(da, 6), ebit=round(ebit, 6),
            nopat=round(nopat, 6), capex=round(capex, 6),
            nwc_level=round(nwc, 6), change_in_nwc=round(change_nwc, 6),
            unlevered_fcf=round(fcf, 6)))
        nwc_prev = nwc
    return out
