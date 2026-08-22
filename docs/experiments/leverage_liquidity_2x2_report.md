# leverage-x-liquidity-2x2

**Question.** Is crash severity superadditive in high leverage x thin liquidity?

**Hypothesis.** The drawdown cost of thin liquidity is larger at a 10x leverage cap than at a 2x cap (positive interaction).

## Design

| Field | Value |
| --- | --- |
| Design type | factorial |
| Independent variables | risk.initial_margin, agents.4.count |
| Dependent variables | max_drawdown, risk_forced_volume, crash_detected, realized_volatility |
| Primary metric | **max_drawdown** |
| Replications per cell | 30 |
| Planned runs | 120 |
| Root seed | 42 |

## Data

Batch `bat_cfc17774801a`: 120/120 runs complete.

### Per-cell results: max_drawdown

| Cell | n | mean | std | q05 | median | q95 |
| --- | --- | --- | --- | --- | --- | --- |
| im=0.5|mm=1 | 30 | 0.0243 | 0.0056 | 0.0169 | 0.0246 | 0.0358 |
| im=0.5|mm=5 | 30 | 0.0209 | 0.0038 | 0.0167 | 0.0197 | 0.0281 |
| im=0.1|mm=1 | 30 | 0.1295 | 0.0533 | 0.0629 | 0.1257 | 0.2242 |
| im=0.1|mm=5 | 30 | 0.1407 | 0.1010 | 0.0469 | 0.1068 | 0.2612 |

## 2×2 interaction (primary metric)

| Quantity | Estimate |
| --- | --- |
| Interaction | 0.0146 (95% CI [-0.0233, 0.0566]) |
| Main effect (factor A) | 0.1125 |
| Main effect (factor B) | 0.0038 |

## Methods and assumptions

- Uncertainty: percentile bootstrap (seeded, deterministic) (n_boot = 2000)
- Test: two-sided permutation test on difference in means
- Effect sizes: Cohen's d (pooled) and Cliff's delta
- Multiple comparisons: Holm step-down over treatments
- Assumes: replications are independent (independent derived seeds)
- Assumes: exchangeability under the null for permutation tests
- Assumes: no normality assumption; bootstrap/permutation based

## Model card (excerpt)

Mechanism laboratory for studying how heterogeneous trading behavior, liquidity, and exogenous shocks combine to generate market phenomena in a synthetic limit order book.

- Calibration: uncalibrated: parameters are hand-tuned, not fit to data
- Validation: mechanism-level tests only; no real-market validation
- Claims **not** supported: prediction of real market prices; statistically validated stylized facts; causal claims about real-world markets
- Limitations: not calibrated to any real market; no out-of-sample validation; single-seed preset outputs are demonstrations, not evidence; latency, queue position, and microstructure metrics not yet modeled

## Reproducibility

- Research hash: `cfc17774801a0b6235bdc4a2b06b624bfdae343270bf2c932ebfb15ba4825221`
- Version: `expv_cfc17774801a` (schema v2, code 0.1.0, seed allocator v1)
- Batch: `bat_cfc17774801a` · Analysis: `ana_0bbda8260617` (seed 0)
- Reproduce: register this version from its stored record, run `BatchRunner(registry).run("expv_cfc17774801a")`, then `analyze(registry, "expv_cfc17774801a", seed=0)`.

*All numbers in this report are read from persisted artifacts (batch summary and analysis result); none are computed at render time. Results describe the specified synthetic model only.*