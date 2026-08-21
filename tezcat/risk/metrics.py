"""Tail-risk metrics over replication outcomes (Phase F8).

Historical (empirical) VaR and Expected Shortfall computed over a sample of
*losses* — typically seed-level outcomes from a batch (e.g. per-replication
max drawdown, or negated total return). Method, confidence, and sample size
are always part of the output; VaR alone is never reported without ES.

Convention: inputs are losses (larger = worse). Negate returns before
calling if needed.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence


def tail_risk(losses: Sequence[float], alpha: float = 0.95) -> Dict[str, Any]:
    """Historical VaR_alpha and ES_alpha of a loss sample.

    VaR is the empirical alpha-quantile (linear interpolation); ES is the
    mean of losses >= VaR. Samples smaller than 20 carry an explicit
    small-sample warning — the tail of 5 numbers is a guess, not an
    estimate.
    """
    n = len(losses)
    if n == 0:
        return {"var": None, "es": None, "n": 0, "alpha": alpha,
                "method": "historical", "warning": "empty sample"}
    s = sorted(float(v) for v in losses)
    idx = alpha * (n - 1)
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    var = s[lo] + (s[hi] - s[lo]) * (idx - lo)
    tail = [v for v in s if v >= var]
    es = sum(tail) / len(tail) if tail else var
    out: Dict[str, Any] = {"var": var, "es": es, "n": n, "alpha": alpha,
                           "method": "historical"}
    if n < 20:
        out["warning"] = f"small sample (n={n}); tail estimates are unstable"
    return out
