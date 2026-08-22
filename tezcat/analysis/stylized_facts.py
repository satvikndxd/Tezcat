"""Stylized-facts feature extraction and real-vs-synthetic comparison (F9).

Features follow Cont (2001): absence of linear return autocorrelation,
heavy tails (excess kurtosis + Hill tail index), volatility clustering
(slowly decaying autocorrelation of |returns|), plus drawdown and optional
volume clustering.

Comparison philosophy: the synthetic side is an **ensemble** (many
replications), so uncertainty comes from the ensemble's spread. For each
feature we report the real value, the ensemble q05–q95 band, whether the
real value falls inside it, and a z-distance. A model is expected to match
*some* features and fail others — the report exists to say which, not to
declare victory.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from tezcat.analysis.stats import describe


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def log_returns(prices: Sequence[float]) -> List[float]:
    out = []
    for a, b in zip(prices, prices[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def acf(xs: Sequence[float], lag: int) -> Optional[float]:
    """Autocorrelation at a lag (biased normalization, standard for ACF)."""
    n = len(xs)
    if lag <= 0 or n <= lag + 1:
        return None
    mean = sum(xs) / n
    var = sum((v - mean) ** 2 for v in xs)
    if var == 0:
        return None
    cov = sum((xs[i] - mean) * (xs[i + lag] - mean) for i in range(n - lag))
    return cov / var


def excess_kurtosis(xs: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 4:
        return None
    mean = sum(xs) / n
    m2 = sum((v - mean) ** 2 for v in xs) / n
    if m2 == 0:
        return None
    m4 = sum((v - mean) ** 4 for v in xs) / n
    return m4 / (m2 * m2) - 3.0


def skewness(xs: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mean = sum(xs) / n
    m2 = sum((v - mean) ** 2 for v in xs) / n
    if m2 == 0:
        return None
    m3 = sum((v - mean) ** 3 for v in xs) / n
    return m3 / (m2 ** 1.5)


def hill_tail_index(xs: Sequence[float], tail_fraction: float = 0.1) -> Optional[float]:
    """Hill estimator of the tail index alpha over the top |x| fraction.

    Smaller alpha = heavier tail (alpha ≈ 3–5 is typical for real equity
    returns; a Gaussian has no power-law tail and yields large estimates).
    """
    abs_x = sorted((abs(v) for v in xs if v != 0), reverse=True)
    k = max(5, int(len(abs_x) * tail_fraction))
    if len(abs_x) <= k or abs_x[k] <= 0:
        return None
    top, x_k = abs_x[:k], abs_x[k]
    s = sum(math.log(v / x_k) for v in top)
    return k / s if s > 0 else None


def max_drawdown(prices: Sequence[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for p in prices:
        peak = max(peak, p)
        if peak > 0:
            mdd = max(mdd, (peak - p) / peak)
    return mdd


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------
ACF_LAGS = (1, 2, 5, 10)


def extract_features(prices: Sequence[float],
                     volumes: Optional[Sequence[float]] = None,
                     tail_fraction: float = 0.1) -> Dict[str, Optional[float]]:
    """Flat scalar feature vector for one price series."""
    r = log_returns(prices)
    n = len(r)
    feats: Dict[str, Optional[float]] = {"n_returns": float(n)}
    if n < 30:
        feats["warning_too_short"] = 1.0
        return feats
    mean = sum(r) / n
    var = sum((v - mean) ** 2 for v in r) / (n - 1)
    feats["return_mean"] = mean
    feats["return_std"] = math.sqrt(var)
    feats["return_skewness"] = skewness(r)
    feats["return_excess_kurtosis"] = excess_kurtosis(r)
    feats["hill_tail_index"] = hill_tail_index(r, tail_fraction)
    feats["max_drawdown"] = max_drawdown(prices)
    abs_r = [abs(v) for v in r]
    for lag in ACF_LAGS:
        feats[f"acf_returns_lag{lag}"] = acf(r, lag)
        feats[f"acf_abs_returns_lag{lag}"] = acf(abs_r, lag)
    vals = [feats[f"acf_abs_returns_lag{l}"] for l in ACF_LAGS]
    vals = [v for v in vals if v is not None]
    feats["volatility_clustering"] = sum(vals) / len(vals) if vals else None
    if volumes is not None and len(volumes) >= 30:
        feats["acf_volume_lag1"] = acf(list(volumes), 1)
    return feats


# ---------------------------------------------------------------------------
# Real vs synthetic-ensemble comparison
# ---------------------------------------------------------------------------
def compare_features(real: Dict[str, Optional[float]],
                     ensemble: List[Dict[str, Optional[float]]]) -> Dict[str, Any]:
    """Compare one real feature vector against a synthetic ensemble.

    Requires >= 10 ensemble members — a band from 3 runs is decoration.
    """
    if len(ensemble) < 10:
        raise ValueError(f"ensemble has {len(ensemble)} members; "
                         ">=10 replications are required for a usable band")
    rows: Dict[str, Any] = {}
    inside = total = 0
    skip = {"n_returns", "warning_too_short"}
    for name, real_val in real.items():
        if name in skip or real_val is None:
            continue
        vals = [e.get(name) for e in ensemble]
        vals = [v for v in vals if v is not None]
        if len(vals) < 10:
            rows[name] = {"real": real_val, "warning": "insufficient ensemble values"}
            continue
        d = describe(vals)
        in_band = d["q05"] <= real_val <= d["q95"]
        z = ((real_val - d["mean"]) / d["std"]) if d["std"] > 0 else None
        rows[name] = {"real": real_val, "ensemble_mean": d["mean"],
                      "ensemble_std": d["std"], "band_q05": d["q05"],
                      "band_q95": d["q95"], "inside_band": in_band,
                      "z_distance": z, "n_ensemble": d["n"]}
        total += 1
        inside += in_band
    return {"features": rows, "n_features": total, "n_inside_band": inside,
            "coverage": inside / total if total else None,
            "n_ensemble": len(ensemble)}


def stylized_facts_report(real_lineage: Dict[str, Any],
                          comparison: Dict[str, Any]) -> str:
    """Markdown rendering of a comparison — from artifact dicts only."""
    lines = ["# Stylized-facts comparison", "",
             "## Data lineage", ""]
    for k, v in real_lineage.items():
        lines.append(f"- **{k}**: {v}")
    lines += ["", f"Synthetic ensemble: {comparison['n_ensemble']} replications.",
              "", "## Feature comparison", "",
              "| Feature | Real | Ensemble mean | q05–q95 band | Inside | z |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, row in sorted(comparison["features"].items()):
        if "warning" in row:
            lines.append(f"| {name} | {row['real']:.5f} | — | — | — | {row['warning']} |")
            continue
        z = f"{row['z_distance']:.2f}" if row["z_distance"] is not None else "—"
        mark = "✔" if row["inside_band"] else "✘"
        lines.append(
            f"| {name} | {row['real']:.5f} | {row['ensemble_mean']:.5f} | "
            f"[{row['band_q05']:.5f}, {row['band_q95']:.5f}] | {mark} | {z} |")
    cov = comparison["coverage"]
    lines += ["", f"**Coverage: {comparison['n_inside_band']}/"
                  f"{comparison['n_features']} features inside the ensemble "
                  f"band ({cov:.0%}).**" if cov is not None else "",
              "",
              "*A partial match is the expected outcome: the report exists to "
              "state which properties the model reproduces and which it does "
              "not. No claim about real markets beyond this comparison is "
              "made.*"]
    return "\n".join(lines)
