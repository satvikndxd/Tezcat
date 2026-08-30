"""M&A transaction structuring: purchase price, funding, ownership (S6).

The engine models a stock-and/or-cash acquisition of a public-style
target:

    offer per share   = unaffected price × (1 + premium)   [or given]
    equity purchase   = offer × target diluted shares
    purchase EV       = equity purchase + target net debt

    USES   = equity purchase + advisory fees + financing fees
             (+ target debt repayment if refinanced)
    SOURCES = new debt + stock consideration + acquirer balance-sheet cash

Stock consideration = stock% × equity purchase, issued at the acquirer's
current share price. The balance-sheet cash contribution is the
residual that makes sources equal uses; the model then *verifies* the
reconciliation and enforces the minimum-cash constraint — a structure
that cannot be funded fails loudly instead of quietly borrowing more.

Ownership: pro forma shares = acquirer shares + new shares; acquirer and
target shareholders' percentages must sum to exactly 1 (invariant).

Purchase accounting (goodwill, intangible step-ups, incremental D&A) is
a documented extension point (requires target book equity and asset
fair values); it is deliberately not faked in this version.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel
from tezcat.finance.statements import CompanyFinancials, FinanceError


class SynergyAssumptions(FrozenModel):
    """Run-rate pre-tax synergies with an explicit phase-in schedule."""

    run_rate_pretax: float = Field(..., ge=0)
    phase_in: List[float] = Field(..., min_length=1,
                                  description="Fraction realized per year, "
                                              "e.g. [0.5, 1.0, 1.0]")
    rationale: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "SynergyAssumptions":
        for i, f in enumerate(self.phase_in):
            if not 0.0 <= f <= 1.0:
                raise FinanceError(f"synergy phase_in[{i}] = {f} outside [0,1]")
        return self


class TransactionAssumptions(FrozenModel):
    """Explicit deal-structure assumptions."""

    premium_pct: Optional[float] = Field(
        None, gt=-0.5, lt=3.0,
        description="Premium to the target's unaffected share price")
    offer_price_per_share: Optional[float] = Field(
        None, gt=0, description="Alternative to premium_pct")
    pct_stock: float = Field(..., ge=0, le=1,
                             description="Stock share of consideration")
    new_debt: float = Field(..., ge=0)
    advisory_fees: float = Field(..., ge=0)
    financing_fees: float = Field(..., ge=0)
    financing_fee_amortization_years: int = Field(5, ge=1)
    cost_of_new_debt: float = Field(..., ge=0, lt=0.30)
    cash_yield: float = Field(..., ge=0, lt=0.15,
                              description="Yield foregone on cash deployed")
    minimum_cash: float = Field(..., ge=0,
                                description="Post-close acquirer cash floor")
    refinance_target_debt: bool = False
    tax_rate: float = Field(..., ge=0, lt=1)
    synergies: SynergyAssumptions
    rationale: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "TransactionAssumptions":
        if (self.premium_pct is None) == (self.offer_price_per_share is None):
            raise FinanceError(
                "specify exactly one of premium_pct or offer_price_per_share")
        return self


def structure_transaction(acquirer: CompanyFinancials,
                          target: CompanyFinancials,
                          assumptions: TransactionAssumptions
                          ) -> Dict[str, Any]:
    """Compute purchase price, sources & uses, and pro forma ownership."""
    if target.share_price is None and assumptions.premium_pct is not None:
        raise FinanceError(
            "premium_pct requires the target's unaffected share price")
    if acquirer.share_price is None and assumptions.pct_stock > 0:
        raise FinanceError(
            "stock consideration requires the acquirer's share price")

    if assumptions.offer_price_per_share is not None:
        offer = assumptions.offer_price_per_share
        premium = (offer / target.share_price - 1.0
                   if target.share_price else None)
    else:
        offer = target.share_price * (1.0 + assumptions.premium_pct)
        premium = assumptions.premium_pct

    tgt = target.latest
    equity_purchase = offer * tgt.diluted_shares
    if equity_purchase <= 0:
        raise FinanceError("equity purchase price must be positive")
    purchase_ev = equity_purchase + tgt.net_debt

    stock_consideration = assumptions.pct_stock * equity_purchase
    cash_consideration = equity_purchase - stock_consideration
    debt_repayment = tgt.debt if assumptions.refinance_target_debt else 0.0

    uses = {
        "equity_purchase_price": round(equity_purchase, 6),
        "advisory_fees": assumptions.advisory_fees,
        "financing_fees": assumptions.financing_fees,
        "target_debt_repayment": round(debt_repayment, 6),
    }
    total_uses = sum(uses.values())

    balance_sheet_cash = (total_uses - assumptions.new_debt
                          - stock_consideration)
    if balance_sheet_cash < -1e-9:
        raise FinanceError(
            f"financing over-funded: new debt + stock exceed total uses by "
            f"{-balance_sheet_cash:.2f} — reduce new_debt or stock share")
    balance_sheet_cash = max(0.0, balance_sheet_cash)
    acq = acquirer.latest
    post_close_cash = acq.cash - balance_sheet_cash
    if post_close_cash < assumptions.minimum_cash - 1e-9:
        raise FinanceError(
            f"structure is not fundable: requires {balance_sheet_cash:.2f} "
            f"of balance-sheet cash but only "
            f"{acq.cash - assumptions.minimum_cash:.2f} is available above "
            f"the {assumptions.minimum_cash:.2f} minimum-cash floor")

    sources = {
        "new_debt": assumptions.new_debt,
        "stock_consideration": round(stock_consideration, 6),
        "acquirer_balance_sheet_cash": round(balance_sheet_cash, 6),
    }
    total_sources = sum(sources.values())
    if abs(total_sources - total_uses) > 1e-6:
        raise FinanceError(  # defensive: must hold by construction
            f"sources ({total_sources:.4f}) != uses ({total_uses:.4f})")

    new_shares = (stock_consideration / acquirer.share_price
                  if assumptions.pct_stock > 0 else 0.0)
    pf_shares = acq.diluted_shares + new_shares
    ownership_acquirer = acq.diluted_shares / pf_shares
    ownership_target = new_shares / pf_shares
    if abs(ownership_acquirer + ownership_target - 1.0) > 1e-12:
        raise FinanceError("ownership percentages do not reconcile")

    return {
        "kind": "derived",
        "offer_price_per_share": round(offer, 6),
        "premium_to_unaffected": (round(premium, 6)
                                  if premium is not None else None),
        "equity_purchase_price": round(equity_purchase, 6),
        "purchase_enterprise_value": round(purchase_ev, 6),
        "implied_ev_ebitda": (round(purchase_ev / tgt.ebitda, 6)
                              if tgt.ebitda > 0 else None),
        "consideration": {
            "cash": round(cash_consideration, 6),
            "stock": round(stock_consideration, 6),
            "pct_cash": round(1.0 - assumptions.pct_stock, 6),
            "pct_stock": assumptions.pct_stock,
        },
        "sources": sources,
        "uses": uses,
        "total_sources": round(total_sources, 6),
        "total_uses": round(total_uses, 6),
        "new_shares_issued": round(new_shares, 6),
        "pro_forma_shares": round(pf_shares, 6),
        "ownership": {"acquirer_shareholders": round(ownership_acquirer, 6),
                      "target_shareholders": round(ownership_target, 6)},
        "post_close_acquirer_cash": round(post_close_cash, 6),
        "pro_forma_debt": round(acq.debt + assumptions.new_debt
                                + (0.0 if assumptions.refinance_target_debt
                                   else tgt.debt), 6),
        "assumptions": assumptions.model_dump(mode="json"),
    }
