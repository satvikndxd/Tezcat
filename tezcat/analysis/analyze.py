"""Design-aware analysis of batch results (Phase F6).

Produces an ``AnalysisResult`` artifact from the registry's seed-level rows
— never from live engine state. The primary metric is analyzed first and
inferentially; secondary dependent variables are described. Methods are
declared with their assumptions in the artifact itself.

Policies (explicit, tested):

- **Completeness:** inference refuses a partial batch unless
  ``allow_partial=True``, and then records a prominent warning.
- **Minimum data:** any comparison with a group of n < 2 yields no
  inference for that comparison (warning, not silence).
- **Multiple comparisons:** several treatments against one control use
  Holm-adjusted permutation p-values.
- **Determinism:** all resampling is seeded from the analysis seed via the
  versioned allocator; the same artifact reproduces bit-for-bit.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json
from tezcat.core.seeds import derive_seed
from tezcat.experiments.aggregation import aggregate
from tezcat.experiments.batch import batch_id_for
from tezcat.experiments.registry import Registry
from tezcat.analysis import stats

ANALYSIS_SCHEMA_VERSION = 1


class AnalysisError(ValueError):
    pass


def _cell_values(summary: Dict[str, Any], cell: str, metric: str) -> List[float]:
    return summary["cells"][cell]["metrics"].get(metric, {}).get("values", [])


def analyze(registry: Registry, version_id: str, seed: int = 0,
            n_boot: int = 2000, allow_partial: bool = False) -> Dict[str, Any]:
    """Analyze the canonical batch of an experiment version."""
    version = registry.load(version_id)
    design = version.design
    summary = aggregate(registry, version_id)  # recomputed from rows

    warnings: List[str] = []
    if not summary["complete"]:
        if not allow_partial:
            raise AnalysisError(
                f"batch {summary['batch_id']} is incomplete "
                f"({summary['completed_runs']}/{summary['planned_runs']} runs); "
                "run the batch to completion or pass allow_partial=True")
        warnings.append(
            f"PARTIAL DATA: only {summary['completed_runs']}/"
            f"{summary['planned_runs']} planned runs completed; "
            f"missing: {summary['missing_keys']}")

    pm = design.primary_metric
    cells = [c["cell"] for c in version.cell_configs]

    result: Dict[str, Any] = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "version_id": version_id,
        "research_hash": version.research_hash,
        "batch_id": batch_id_for(version),
        "design_type": design.design_type,
        "primary_metric": pm,
        "analysis_seed": seed,
        "n_boot": n_boot,
        "methods": {
            "uncertainty": "percentile bootstrap (seeded, deterministic)",
            "test": "two-sided permutation test on difference in means",
            "effect_sizes": "Cohen's d (pooled) and Cliff's delta",
            "multiple_comparisons": "Holm step-down over treatments",
            "assumptions": [
                "replications are independent (independent derived seeds)",
                "exchangeability under the null for permutation tests",
                "no normality assumption; bootstrap/permutation based",
            ],
        },
        "descriptives": {},
        "comparisons": [],
        "interaction": None,
        "trend": None,
        "warnings": warnings,
    }

    # Descriptives for every cell and dependent variable.
    for cell in cells:
        result["descriptives"][cell] = {
            dv: stats.describe(_cell_values(summary, cell, dv))
            for dv in design.dependent_variables
        }

    # Design-specific inference on the primary metric.
    if design.design_type in ("ab", "ablation"):
        control = design.control.name
        cv = _cell_values(summary, control, pm)
        raw_p: List[Optional[float]] = []
        for t in design.treatments:
            tv = _cell_values(summary, t.name, pm)
            cseed = derive_seed(seed, "analysis", "compare", t.name)
            comp = {
                "control": control, "treatment": t.name, "metric": pm,
                "diff": stats.mean_diff_ci(cv, tv, cseed, n_boot=n_boot),
                "cohens_d": stats.cohens_d(cv, tv),
                "cliffs_delta": stats.cliffs_delta(cv, tv),
                "permutation": stats.permutation_test(
                    cv, tv, derive_seed(seed, "analysis", "perm", t.name),
                    n_perm=n_boot),
            }
            if len(cv) < 2 or len(tv) < 2:
                warnings.append(f"comparison {control} vs {t.name}: "
                                "a group has n < 2; no inference")
            if comp["cohens_d"] is None and len(cv) >= 2 and len(tv) >= 2:
                warnings.append(f"comparison {control} vs {t.name}: "
                                "zero pooled variance; Cohen's d undefined")
            raw_p.append(comp["permutation"].get("p_value"))
            result["comparisons"].append(comp)
        adjusted = stats.holm_adjust(raw_p)
        for comp, adj in zip(result["comparisons"], adjusted):
            comp["permutation"]["p_holm"] = adj

    elif design.design_type == "factorial":
        f = design.factors
        if len(f) == 2 and len(f[0].levels) == 2 and len(f[1].levels) == 2:
            order = [f"{f[0].name}={a}|{f[1].name}={b}"
                     for a in f[0].levels for b in f[1].levels]
            cell_data = {c: _cell_values(summary, c, pm) for c in order}
            result["interaction"] = stats.interaction_2x2(
                cell_data, order,
                derive_seed(seed, "analysis", "interaction"), n_boot=n_boot)
        else:
            warnings.append("interaction estimation implemented for 2x2 "
                            "factorials only; larger designs get descriptives")

    elif design.design_type == "sweep":
        factor = design.factors[0]
        level_means = []
        for i, lvl in enumerate(factor.levels):
            vals = _cell_values(summary, f"{factor.name}={lvl}", pm)
            if vals:
                level_means.append((i, sum(vals) / len(vals)))
        if len(level_means) >= 3:
            # Monotonic trend: Spearman-style rank correlation of level
            # index vs cell mean, permutation p over level ordering.
            result["trend"] = _rank_trend(
                level_means, derive_seed(seed, "analysis", "trend"), n_boot)
        else:
            warnings.append("trend analysis requires >=3 complete levels")

    # Content-addressed identity, then persist.
    result["analysis_id"] = "ana_" + hashlib.sha256(
        canonical_json(result).encode()).hexdigest()[:12]
    registry.save_analysis(version_id, result)
    return result


def _rank_trend(level_means: List[tuple], seed: int, n_perm: int) -> Dict[str, Any]:
    import random as _random
    ranks_x = [float(i) for i, _ in level_means]
    ys = [m for _, m in level_means]

    def corr(xs: List[float], ys_: List[float]) -> float:
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys_) / n
        sx = (sum((v - mx) ** 2 for v in xs)) ** 0.5
        sy = (sum((v - my) ** 2 for v in ys_)) ** 0.5
        if sx == 0 or sy == 0:
            return 0.0
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys_)) / (sx * sy)

    obs = corr(ranks_x, ys)
    rng = _random.Random(seed)
    hits = 0
    perm = list(ys)
    for _ in range(n_perm):
        rng.shuffle(perm)
        if abs(corr(ranks_x, perm)) >= abs(obs) - 1e-15:
            hits += 1
    return {"level_mean_correlation": obs,
            "p_value": (hits + 1) / (n_perm + 1), "n_perm": n_perm,
            "levels_used": len(level_means)}
