# Statistical Analysis and Research Reports (Phase F6)

## Boundary with aggregation

Phase F5 aggregation is descriptive only (per-cell n/mean/std/quantiles with
seed-level values). Phase F6 (`tezcat/analysis/`) adds **inference** — and
only from persisted artifacts, never from live engine state.

## Methods (declared inside every analysis artifact)

| Concern | Method | Why |
| --- | --- | --- |
| Uncertainty | Percentile bootstrap (seeded, deterministic) | Simulation metrics (drawdowns, tails) are not normal; no t-distribution assumptions. |
| Hypothesis test | Two-sided permutation test on the mean difference | Assumption-light: exchangeability under the null; exact-style p with +1 correction (never zero). |
| Effect sizes | Cohen's d (pooled) **and** Cliff's delta | d for familiarity; δ is rank-based and robust to non-normality. Zero-variance d is `None` + warning, never ∞. |
| Multiple comparisons | Holm step-down across treatments vs one control | Controls family-wise error without Bonferroni's full conservatism. |
| 2×2 interaction | Cell-mean contrast `(a1b1−a1b0)−(a0b1−a0b0)` with bootstrap CI | Directly answers "does the effect of B change with A"; larger designs get descriptives + explicit warning. |
| Sweep trend | Level-order/mean correlation with permutation p | Monotonicity check without assuming linearity in the level values. |

Independence note: replications are independent **by construction**
(independent derived seeds), which is the across-observation independence
these methods need. Each observation is a whole-run summary, so within-run
time-series dependence does not enter.

## Policies (tested)

- **Primary metric first.** Inference targets the design's declared
  `primary_metric`; other dependent variables are described, not tested.
- **Completeness.** A partial batch refuses inference
  (`AnalysisError`) unless `allow_partial=True`, which stamps a prominent
  `PARTIAL DATA` warning listing the missing runs.
- **Minimum data.** Any group with n < 2 produces a warning and no interval
  or p-value — never a fabricated number.
- **Determinism.** All resampling seeds derive from the analysis seed via
  the versioned allocator; `analyze(..., seed=0)` reproduces bit-for-bit,
  and the artifact is content-addressed (`ana_<hash[:12]>`).

## Reports

`build_report(registry, version_id)` renders markdown **exclusively from
artifacts** (version record, batch summary, analysis result) — it refuses to
run without a stored analysis, and a test verifies rendered numbers equal
values independently recomputed from the seed-level rows. Every report
carries: design table, per-cell quantile tables, comparison table
(Δ, CI, d, δ, raw and Holm-adjusted p), interaction/trend section, methods
and assumptions, warnings, the model-card excerpt (including claims **not**
supported), and a reproducibility block (research hash, batch id, analysis
id + seed, reproduction commands).

## Acceptance evidence

Herding × MM-liquidity 2×2 factorial (20 replications/cell, 80 runs, calm
500-step configuration, no shocks):

- interaction estimate 0.00005, 95% CI [−0.00217, 0.00202] — **spans zero**
- main effects similarly small (herding +0.0007, mm −0.0004)

The correct reading, and the one the report supports: in this calm
configuration there is **no detectable herding×liquidity interaction**. The
pipeline reports honest null results rather than manufacturing
significance; detecting the regime where these mechanisms matter (shocked,
levered, thin-book markets) is a research question for the F8 stress
phases, not something the analysis layer should conjure.

Deferred: figure generation (matplotlib is an optional script dependency,
not a core one); reports are text/tables until a figures contract is added.
