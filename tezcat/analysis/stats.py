"""Seeded, deterministic statistical primitives (Phase F6).

Design principles:

- **Assumption-light.** Simulation metrics (drawdowns, tail losses) are not
  normal; primary uncertainty comes from bootstrap resampling and
  permutation tests, not t-distributions. Cohen's d is reported alongside
  the nonparametric Cliff's delta.
- **Deterministic.** Every resampling routine takes an explicit seed and
  uses its own ``random.Random`` — analyses reproduce bit-for-bit.
- **Degenerate-safe.** Zero-variance and tiny samples return explicit
  ``None``/warnings instead of silently emitting infinities.

Replications are independent by construction (independent derived seeds),
which is exactly the independence these methods assume *across* runs.
Within-run time-series dependence is irrelevant here because each
observation is a whole-run summary statistic.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence


def describe(values: Sequence[float]) -> Dict[str, Any]:
    """Descriptive summary with seed-level quantiles."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    s = sorted(float(v) for v in values)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / (n - 1) if n > 1 else 0.0

    def q(p: float) -> float:
        # Linear interpolation between closest ranks.
        idx = p * (n - 1)
        lo, hi = int(math.floor(idx)), int(math.ceil(idx))
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return {"n": n, "mean": mean, "std": math.sqrt(var), "min": s[0],
            "q05": q(0.05), "q25": q(0.25), "median": q(0.50),
            "q75": q(0.75), "q95": q(0.95), "max": s[-1]}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def bootstrap_ci(values: Sequence[float], seed: int,
                 stat: Callable[[Sequence[float]], float] = None,
                 n_boot: int = 2000, alpha: float = 0.05) -> Dict[str, Any]:
    """Percentile bootstrap CI for a statistic of one sample."""
    if len(values) < 2:
        return {"estimate": float(values[0]) if values else None,
                "ci_low": None, "ci_high": None, "n_boot": 0,
                "warning": "fewer than 2 observations; no interval"}
    stat = stat or (lambda v: sum(v) / len(v))
    rng = random.Random(seed)
    vals = [float(v) for v in values]
    n = len(vals)
    reps = sorted(stat([vals[rng.randrange(n)] for _ in range(n)])
                  for _ in range(n_boot))
    lo = reps[int((alpha / 2) * n_boot)]
    hi = reps[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {"estimate": stat(vals), "ci_low": lo, "ci_high": hi,
            "n_boot": n_boot, "alpha": alpha}


def mean_diff_ci(a: Sequence[float], b: Sequence[float], seed: int,
                 n_boot: int = 2000, alpha: float = 0.05) -> Dict[str, Any]:
    """Bootstrap CI for mean(b) - mean(a) (treatment minus control)."""
    if len(a) < 2 or len(b) < 2:
        return {"estimate": None, "ci_low": None, "ci_high": None,
                "warning": "fewer than 2 observations in a group"}
    rng = random.Random(seed)
    a = [float(v) for v in a]
    b = [float(v) for v in b]
    obs = sum(b) / len(b) - sum(a) / len(a)
    reps = []
    for _ in range(n_boot):
        ra = [a[rng.randrange(len(a))] for _ in range(len(a))]
        rb = [b[rng.randrange(len(b))] for _ in range(len(b))]
        reps.append(sum(rb) / len(rb) - sum(ra) / len(ra))
    reps.sort()
    lo = reps[int((alpha / 2) * n_boot)]
    hi = reps[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {"estimate": obs, "ci_low": lo, "ci_high": hi,
            "n_boot": n_boot, "alpha": alpha}


# ---------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------
def cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Pooled-std standardized mean difference (b - a). None if degenerate."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((v - ma) ** 2 for v in a) / (na - 1)
    vb = sum((v - mb) ** 2 for v in b) / (nb - 1)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled == 0.0:
        return None  # zero variance: standardized effect undefined
    return (mb - ma) / pooled


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Nonparametric effect size in [-1, 1]: P(b > a) - P(b < a)."""
    if not a or not b:
        return None
    gt = sum(1 for x in b for y in a if x > y)
    lt = sum(1 for x in b for y in a if x < y)
    return (gt - lt) / (len(a) * len(b))


# ---------------------------------------------------------------------------
# Permutation test
# ---------------------------------------------------------------------------
def permutation_test(a: Sequence[float], b: Sequence[float], seed: int,
                     n_perm: int = 2000) -> Dict[str, Any]:
    """Two-sided permutation test on the difference in means.

    Assumption-light: exchangeability under the null. p uses the +1
    correction so it is never exactly zero.
    """
    if len(a) < 2 or len(b) < 2:
        return {"p_value": None, "warning": "fewer than 2 observations in a group"}
    rng = random.Random(seed)
    a = [float(v) for v in a]
    b = [float(v) for v in b]
    obs = abs(sum(b) / len(b) - sum(a) / len(a))
    pooled = a + b
    na = len(a)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pooled)
        pa, pb = pooled[:na], pooled[na:]
        if abs(sum(pb) / len(pb) - sum(pa) / len(pa)) >= obs - 1e-15:
            hits += 1
    return {"p_value": (hits + 1) / (n_perm + 1), "n_perm": n_perm,
            "observed_abs_diff": obs}


# ---------------------------------------------------------------------------
# Multiple comparisons
# ---------------------------------------------------------------------------
def holm_adjust(p_values: List[Optional[float]]) -> List[Optional[float]]:
    """Holm step-down adjustment; Nones pass through untouched."""
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    indexed.sort(key=lambda t: t[1])
    m = len(indexed)
    adjusted: Dict[int, float] = {}
    running_max = 0.0
    for rank, (i, p) in enumerate(indexed):
        adj = min(1.0, (m - rank) * p)
        running_max = max(running_max, adj)  # enforce monotonicity
        adjusted[i] = running_max
    return [adjusted.get(i) for i in range(len(p_values))]


# ---------------------------------------------------------------------------
# 2x2 factorial interaction
# ---------------------------------------------------------------------------
def interaction_2x2(cells: Dict[str, List[float]], order: List[str],
                    seed: int, n_boot: int = 2000,
                    alpha: float = 0.05) -> Dict[str, Any]:
    """Interaction estimate for a 2x2 design with bootstrap CI.

    ``order`` names the four cells as [a0b0, a0b1, a1b0, a1b1].
    interaction = (mean(a1b1) - mean(a1b0)) - (mean(a0b1) - mean(a0b0)):
    how much the effect of factor B changes when factor A is high.
    """
    if len(order) != 4 or any(len(cells[c]) < 2 for c in order):
        return {"estimate": None,
                "warning": "2x2 interaction requires 4 cells with >=2 obs each"}
    rng = random.Random(seed)
    data = {c: [float(v) for v in cells[c]] for c in order}

    def stat(sample: Dict[str, List[float]]) -> float:
        m = {c: sum(v) / len(v) for c, v in sample.items()}
        a0b0, a0b1, a1b0, a1b1 = (m[order[0]], m[order[1]],
                                  m[order[2]], m[order[3]])
        return (a1b1 - a1b0) - (a0b1 - a0b0)

    obs = stat(data)
    reps = []
    for _ in range(n_boot):
        resampled = {c: [v[rng.randrange(len(v))] for _ in range(len(v))]
                     for c, v in data.items()}
        reps.append(stat(resampled))
    reps.sort()
    lo = reps[int((alpha / 2) * n_boot)]
    hi = reps[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    main_a = ((sum(data[order[2]]) / len(data[order[2]])
               + sum(data[order[3]]) / len(data[order[3]])) / 2
              - (sum(data[order[0]]) / len(data[order[0]])
                 + sum(data[order[1]]) / len(data[order[1]])) / 2)
    main_b = ((sum(data[order[1]]) / len(data[order[1]])
               + sum(data[order[3]]) / len(data[order[3]])) / 2
              - (sum(data[order[0]]) / len(data[order[0]])
                 + sum(data[order[2]]) / len(data[order[2]])) / 2)
    return {"estimate": obs, "ci_low": lo, "ci_high": hi,
            "main_effect_a": main_a, "main_effect_b": main_b,
            "n_boot": n_boot, "alpha": alpha, "cell_order": order}
