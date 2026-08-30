"""Historical company financials with fact/assumption separation (S6).

The unit of record is a :class:`FiscalPeriod` — one period's income
statement, cash-flow, and balance-sheet items — grouped into
:class:`CompanyFinancials`. Values entered here are **sourced facts**
(with a provenance record); everything computed from them (margins,
free cash flow, net debt, leverage) is **derived** and exposed through
methods, never stored back as if it were an input.

Validation is strict and loud: a period missing required line items, a
statement that does not internally cohere (e.g. gross profit above
revenue), or an impossible share count is rejected at construction.
Missing *optional* data stays ``None`` — it is never imputed.

Units: all monetary values are in a single declared currency and unit
scale (e.g. USD millions); shares in millions to match. The model never
mixes scales silently.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel, canonical_json

#: Version of the finance domain model. Participates in every case hash;
#: bump on any change to formula semantics.
FINANCE_MODEL_VERSION = 1


class FinanceError(Exception):
    """Invalid financial state — always raised, never papered over.

    Deliberately NOT a ``ValueError`` subclass: pydantic would wrap a
    ``ValueError`` raised inside a model validator into its own
    ``ValidationError``, hiding the domain error type. As a plain
    ``Exception`` it propagates unchanged from validators, so callers
    always catch the same loud, named failure.
    """


class Provenance(FrozenModel):
    """Where a sourced fact came from. Fixture data must say so."""

    source: str = Field(..., min_length=1,
                        description="e.g. 'synthetic fixture', '10-K FY2025'")
    retrieved: str = Field("", description="Retrieval date if applicable")
    note: str = ""


class FiscalPeriod(FrozenModel):
    """One historical period of sourced financial facts.

    Income-statement fields are required; balance-sheet and cash-flow
    fields required for FCF/net-debt work are also required. Truly
    optional disclosures may be None and stay None.
    """

    label: str = Field(..., min_length=1, description="e.g. 'FY2023'")
    # income statement
    revenue: float = Field(..., gt=0)
    cogs: float = Field(..., ge=0)
    opex: float = Field(..., ge=0, description="Operating expenses excl. D&A")
    depreciation_amortization: float = Field(..., ge=0)
    interest_expense: float = Field(..., ge=0)
    taxes: float = Field(..., description="Tax expense (may be negative)")
    # cash flow / investment
    capex: float = Field(..., ge=0)
    net_working_capital: float = Field(
        ..., description="Period-end NWC level (change is derived)")
    # balance sheet
    cash: float = Field(..., ge=0)
    debt: float = Field(..., ge=0)
    # capitalization
    diluted_shares: float = Field(..., gt=0)
    provenance: Provenance

    # -- derived income statement lines (never stored) -----------------
    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cogs

    @property
    def ebitda(self) -> float:
        return self.gross_profit - self.opex

    @property
    def ebit(self) -> float:
        return self.ebitda - self.depreciation_amortization

    @property
    def ebt(self) -> float:
        return self.ebit - self.interest_expense

    @property
    def net_income(self) -> float:
        return self.ebt - self.taxes

    @property
    def gross_margin(self) -> float:
        return self.gross_profit / self.revenue

    @property
    def ebitda_margin(self) -> float:
        return self.ebitda / self.revenue

    @property
    def net_debt(self) -> float:
        return self.debt - self.cash

    @property
    def effective_tax_rate(self) -> Optional[float]:
        return self.taxes / self.ebt if self.ebt > 0 else None

    @model_validator(mode="after")
    def _validate(self) -> "FiscalPeriod":
        if self.cogs > self.revenue:
            raise FinanceError(
                f"{self.label}: COGS ({self.cogs}) exceeds revenue "
                f"({self.revenue}) — gross profit would be negative; if "
                "this is genuinely the case, model it explicitly")
        if self.ebt > 0 and self.taxes > self.ebt:
            raise FinanceError(
                f"{self.label}: tax expense ({self.taxes}) exceeds pre-tax "
                f"income ({self.ebt:.1f})")
        return self


class CompanyFinancials(FrozenModel):
    """A company's historical record plus descriptive metadata."""

    name: str = Field(..., min_length=1)
    ticker: str = ""
    sector: str = ""
    description: str = ""
    currency: str = Field("USD", min_length=3, max_length=3)
    units: str = Field("millions", description="Monetary/share unit scale")
    periods: List[FiscalPeriod] = Field(..., min_length=2,
                                        description=">=2 periods: trends "
                                                    "need history")
    share_price: Optional[float] = Field(
        None, gt=0, description="Current/unaffected price per share "
                                "(market fact, needed for market-based "
                                "multiples)")

    @model_validator(mode="after")
    def _validate(self) -> "CompanyFinancials":
        labels = [p.label for p in self.periods]
        if len(set(labels)) != len(labels):
            raise FinanceError(f"duplicate period labels: {labels}")
        return self

    # -- derived views --------------------------------------------------
    @property
    def latest(self) -> FiscalPeriod:
        return self.periods[-1]

    def unlevered_fcf(self, period_index: int, tax_rate: float) -> float:
        """Unlevered FCF = EBIT(1-t) + D&A - capex - ΔNWC.

        ΔNWC needs a prior period; index 0 is therefore not computable
        and raises (never assume zero working-capital change).
        """
        if period_index < 1:
            raise FinanceError(
                "unlevered FCF requires a prior period for the "
                "working-capital change; index 0 has none")
        p, prev = self.periods[period_index], self.periods[period_index - 1]
        change_nwc = p.net_working_capital - prev.net_working_capital
        return (p.ebit * (1.0 - tax_rate) + p.depreciation_amortization
                - p.capex - change_nwc)

    def revenue_cagr(self) -> float:
        first, last = self.periods[0].revenue, self.periods[-1].revenue
        years = len(self.periods) - 1
        return (last / first) ** (1.0 / years) - 1.0

    def market_cap(self) -> Optional[float]:
        if self.share_price is None:
            return None
        return self.share_price * self.latest.diluted_shares

    def enterprise_value(self) -> Optional[float]:
        mc = self.market_cap()
        return None if mc is None else mc + self.latest.net_debt

    def leverage_ratio(self) -> Optional[float]:
        """Net debt / EBITDA of the latest period (None if EBITDA <= 0)."""
        ebitda = self.latest.ebitda
        return self.latest.net_debt / ebitda if ebitda > 0 else None

    def summary(self) -> Dict[str, Any]:
        """Derived-metric summary for reporting (labeled derived)."""
        latest = self.latest
        return {
            "kind": "derived",
            "latest_period": latest.label,
            "revenue": latest.revenue,
            "revenue_cagr": round(self.revenue_cagr(), 6),
            "ebitda": round(latest.ebitda, 4),
            "ebitda_margin": round(latest.ebitda_margin, 6),
            "net_income": round(latest.net_income, 4),
            "net_debt": round(latest.net_debt, 4),
            "leverage_net_debt_ebitda": (
                round(self.leverage_ratio(), 4)
                if self.leverage_ratio() is not None else None),
            "diluted_shares": latest.diluted_shares,
            "market_cap": self.market_cap(),
            "enterprise_value": self.enterprise_value(),
        }

    def content_hash(self) -> str:
        payload = {"finance_model_version": FINANCE_MODEL_VERSION,
                   "financials": self.model_dump(mode="json")}
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
