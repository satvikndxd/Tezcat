"""Comparable-company (trading comps) valuation (S6).

Raw peer data (sourced facts) is kept strictly separate from calculated
multiples (derived). For each peer:

    market cap = price × diluted shares
    EV         = market cap + debt − cash
    EV/Revenue, EV/EBITDA, EV/EBIT, P/E

Peer statistics (mean, median, quartiles) are computed per multiple over
the peers where the multiple is meaningful (denominator > 0 — a peer
with negative EBITDA is *excluded from that multiple and counted*, never
clamped). The analyst explicitly selects which multiple and which
statistic to apply; the implied valuation records both, plus the peer
dispersion, so a tight-looking point estimate cannot hide a wide range.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from tezcat.core.config import FrozenModel
from tezcat.finance.statements import CompanyFinancials, FinanceError, Provenance


class PeerCompany(FrozenModel):
    """Sourced facts for one comparable company."""

    name: str = Field(..., min_length=1)
    ticker: str = ""
    revenue: float = Field(..., gt=0)
    ebitda: float
    ebit: float
    net_income: float
    cash: float = Field(..., ge=0)
    debt: float = Field(..., ge=0)
    diluted_shares: float = Field(..., gt=0)
    share_price: float = Field(..., gt=0)
    provenance: Provenance

    # -- derived --------------------------------------------------------
    @property
    def market_cap(self) -> float:
        return self.share_price * self.diluted_shares

    @property
    def enterprise_value(self) -> float:
        return self.market_cap + self.debt - self.cash

    def multiples(self) -> Dict[str, Optional[float]]:
        ev = self.enterprise_value
        return {
            "ev_revenue": round(ev / self.revenue, 6),
            "ev_ebitda": round(ev / self.ebitda, 6) if self.ebitda > 0 else None,
            "ev_ebit": round(ev / self.ebit, 6) if self.ebit > 0 else None,
            "pe": (round(self.market_cap / self.net_income, 6)
                   if self.net_income > 0 else None),
        }


MULTIPLES = ("ev_revenue", "ev_ebitda", "ev_ebit", "pe")
STATISTICS = ("mean", "median", "q25", "q75")


def _stat(values: List[float], stat: str) -> float:
    xs = sorted(values)
    n = len(xs)
    if stat == "mean":
        return sum(xs) / n
    idx = {"q25": 0.25, "median": 0.5, "q75": 0.75}[stat] * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


class CompsSelection(FrozenModel):
    """The analyst's explicit choice of multiple and statistic."""

    multiple: Literal["ev_revenue", "ev_ebitda", "ev_ebit", "pe"] = "ev_ebitda"
    statistic: Literal["mean", "median", "q25", "q75"] = "median"
    rationale: str = ""


def run_comps(target: CompanyFinancials, peers: List[PeerCompany],
              selection: CompsSelection) -> Dict[str, Any]:
    """Peer multiples → statistics → implied valuation of the target."""
    if len(peers) < 3:
        raise FinanceError(f"trading comps needs >= 3 peers; got {len(peers)}")

    peer_rows = [{"name": p.name, "ticker": p.ticker,
                  "enterprise_value": round(p.enterprise_value, 4),
                  "market_cap": round(p.market_cap, 4),
                  **p.multiples()} for p in peers]

    stats: Dict[str, Any] = {}
    for m in MULTIPLES:
        values = [row[m] for row in peer_rows if row[m] is not None]
        stats[m] = ({"n": len(values), "excluded": len(peers) - len(values),
                     **{s: round(_stat(values, s), 6) for s in STATISTICS}}
                    if len(values) >= 3 else
                    {"n": len(values), "excluded": len(peers) - len(values),
                     "warning": "fewer than 3 meaningful peer values"})

    m, s = selection.multiple, selection.statistic
    if "warning" in stats[m]:
        raise FinanceError(
            f"selected multiple {m!r} has only {stats[m]['n']} meaningful "
            "peer values — choose another multiple or add peers")
    chosen = stats[m][s]

    latest = target.latest
    denominator = {"ev_revenue": latest.revenue, "ev_ebitda": latest.ebitda,
                   "ev_ebit": latest.ebit, "pe": latest.net_income}[m]
    if denominator <= 0:
        raise FinanceError(
            f"target metric for {m!r} is {denominator:.2f} <= 0; the "
            "multiple cannot be applied to this target")

    if m == "pe":
        equity_value = chosen * denominator
        enterprise_value = equity_value + latest.net_debt
    else:
        enterprise_value = chosen * denominator
        equity_value = enterprise_value - latest.net_debt
    per_share = equity_value / latest.diluted_shares

    # dispersion disclosure: implied per-share at peer q25 and q75
    def implied_at(mult_value: float) -> float:
        if m == "pe":
            eq = mult_value * denominator
        else:
            eq = mult_value * denominator - latest.net_debt
        return round(eq / latest.diluted_shares, 6)

    return {
        "kind": "derived",
        "method": "trading_comps",
        "selection": selection.model_dump(mode="json"),
        "peers": peer_rows,
        "n_peers": len(peers),
        "statistics": stats,
        "selected_multiple_value": round(chosen, 6),
        "target_metric": round(denominator, 6),
        "implied_enterprise_value": round(enterprise_value, 6),
        "implied_equity_value": round(equity_value, 6),
        "implied_value_per_share": round(per_share, 6),
        "per_share_at_peer_q25": implied_at(stats[m]["q25"]),
        "per_share_at_peer_q75": implied_at(stats[m]["q75"]),
        "disclosure": "peer dispersion bounds are reported alongside the "
                      "point estimate; peers with non-meaningful "
                      "denominators are excluded and counted, not clamped",
    }
