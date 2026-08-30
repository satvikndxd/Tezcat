"""Precedent-transaction valuation (S6).

Historical M&A transactions as sourced facts (target, acquirer, date,
enterprise value, target revenue/EBITDA, premium where known), with
derived transaction multiples and statistics, applied to the target the
same way as trading comps — but labeled separately: precedent multiples
embed control premia and deal-specific dynamics, so they are never
averaged together with trading multiples.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from tezcat.core.config import FrozenModel
from tezcat.finance.comps import STATISTICS, _stat
from tezcat.finance.statements import CompanyFinancials, FinanceError, Provenance


class PrecedentTransaction(FrozenModel):
    """Sourced facts for one historical transaction."""

    target: str = Field(..., min_length=1)
    acquirer: str = Field(..., min_length=1)
    announced: str = Field(..., min_length=4, description="e.g. '2024-05'")
    enterprise_value: float = Field(..., gt=0)
    target_revenue: float = Field(..., gt=0)
    target_ebitda: float
    premium_pct: Optional[float] = Field(
        None, gt=-1, description="Premium to unaffected price, if disclosed")
    sector: str = ""
    rationale: str = ""
    provenance: Provenance

    def multiples(self) -> Dict[str, Optional[float]]:
        return {
            "ev_revenue": round(self.enterprise_value / self.target_revenue, 6),
            "ev_ebitda": (round(self.enterprise_value / self.target_ebitda, 6)
                          if self.target_ebitda > 0 else None),
        }


class PrecedentSelection(FrozenModel):
    multiple: Literal["ev_revenue", "ev_ebitda"] = "ev_ebitda"
    statistic: Literal["mean", "median", "q25", "q75"] = "median"
    rationale: str = ""


def run_precedents(target: CompanyFinancials,
                   transactions: List[PrecedentTransaction],
                   selection: PrecedentSelection) -> Dict[str, Any]:
    if len(transactions) < 3:
        raise FinanceError(
            f"precedent analysis needs >= 3 transactions; got "
            f"{len(transactions)}")

    rows = [{"target": t.target, "acquirer": t.acquirer,
             "announced": t.announced,
             "enterprise_value": t.enterprise_value,
             "premium_pct": t.premium_pct, **t.multiples()}
            for t in transactions]

    stats: Dict[str, Any] = {}
    for m in ("ev_revenue", "ev_ebitda"):
        values = [r[m] for r in rows if r[m] is not None]
        stats[m] = ({"n": len(values),
                     "excluded": len(transactions) - len(values),
                     **{s: round(_stat(values, s), 6) for s in STATISTICS}}
                    if len(values) >= 3 else
                    {"n": len(values), "warning": "fewer than 3 values"})
    premiums = [r["premium_pct"] for r in rows if r["premium_pct"] is not None]
    premium_stats = ({s: round(_stat(premiums, s), 6) for s in STATISTICS}
                     if len(premiums) >= 3 else
                     {"warning": f"only {len(premiums)} disclosed premiums"})

    m, s = selection.multiple, selection.statistic
    if "warning" in stats[m]:
        raise FinanceError(f"selected multiple {m!r} lacks enough "
                           "meaningful transaction values")
    chosen = stats[m][s]
    latest = target.latest
    denominator = {"ev_revenue": latest.revenue,
                   "ev_ebitda": latest.ebitda}[m]
    if denominator <= 0:
        raise FinanceError(f"target metric for {m!r} is non-positive")
    enterprise_value = chosen * denominator
    equity_value = enterprise_value - latest.net_debt
    return {
        "kind": "derived",
        "method": "precedent_transactions",
        "selection": selection.model_dump(mode="json"),
        "transactions": rows,
        "n_transactions": len(transactions),
        "statistics": stats,
        "premium_statistics": premium_stats,
        "selected_multiple_value": round(chosen, 6),
        "implied_enterprise_value": round(enterprise_value, 6),
        "implied_equity_value": round(equity_value, 6),
        "implied_value_per_share": round(equity_value / latest.diluted_shares, 6),
        "disclosure": "precedent multiples embed control premia and "
                      "deal-specific conditions; they are reported "
                      "separately from trading comps by design",
    }
