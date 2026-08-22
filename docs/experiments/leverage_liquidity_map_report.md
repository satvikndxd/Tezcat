# leverage-x-liquidity-map

**Question.** Does increasing leverage amplify the destabilizing effect of declining market liquidity?

**Hypothesis.** Drawdown, forced liquidation, and crash frequency rise with leverage, and rise faster when market-maker liquidity is thin.

## Design

| Field | Value |
| --- | --- |
| Design type | factorial |
| Independent variables | risk.initial_margin, agents.4.count |
| Dependent variables | max_drawdown, realized_volatility, risk_forced_volume, risk_liquidation_slices, crash_detected, total_return |
| Primary metric | **max_drawdown** |
| Replications per cell | 20 |
| Planned runs | 300 |
| Root seed | 42 |

## Data

Batch `bat_60fabaa6f7ca`: 300/300 runs complete.

### Per-cell results: max_drawdown

| Cell | n | mean | std | q05 | median | q95 |
| --- | --- | --- | --- | --- | --- | --- |
| im=0.5|mm=1 | 20 | 0.0248 | 0.0058 | 0.0178 | 0.0246 | 0.0369 |
| im=0.5|mm=3 | 20 | 0.0204 | 0.0045 | 0.0164 | 0.0189 | 0.0299 |
| im=0.5|mm=5 | 20 | 0.0211 | 0.0040 | 0.0169 | 0.0197 | 0.0297 |
| im=0.25|mm=1 | 20 | 0.0826 | 0.0554 | 0.0238 | 0.0526 | 0.1836 |
| im=0.25|mm=3 | 20 | 0.0642 | 0.0648 | 0.0336 | 0.0454 | 0.1753 |
| im=0.25|mm=5 | 20 | 0.0432 | 0.0122 | 0.0234 | 0.0444 | 0.0610 |
| im=0.1667|mm=1 | 20 | 0.1133 | 0.0597 | 0.0463 | 0.1031 | 0.2000 |
| im=0.1667|mm=3 | 20 | 0.1321 | 0.1403 | 0.0470 | 0.0645 | 0.3277 |
| im=0.1667|mm=5 | 20 | 0.1507 | 0.1617 | 0.0510 | 0.0627 | 0.4932 |
| im=0.125|mm=1 | 20 | 0.1095 | 0.0580 | 0.0351 | 0.1188 | 0.1960 |
| im=0.125|mm=3 | 20 | 0.2406 | 0.1712 | 0.0582 | 0.1947 | 0.5828 |
| im=0.125|mm=5 | 20 | 0.0934 | 0.0642 | 0.0362 | 0.0621 | 0.2193 |
| im=0.1|mm=1 | 20 | 0.1333 | 0.0611 | 0.0598 | 0.1265 | 0.2269 |
| im=0.1|mm=3 | 20 | 0.1707 | 0.1163 | 0.0336 | 0.1470 | 0.3387 |
| im=0.1|mm=5 | 20 | 0.1366 | 0.1096 | 0.0469 | 0.0878 | 0.2792 |

## Methods and assumptions

- Uncertainty: percentile bootstrap (seeded, deterministic) (n_boot = 2000)
- Test: two-sided permutation test on difference in means
- Effect sizes: Cohen's d (pooled) and Cliff's delta
- Multiple comparisons: Holm step-down over treatments
- Assumes: replications are independent (independent derived seeds)
- Assumes: exchangeability under the null for permutation tests
- Assumes: no normality assumption; bootstrap/permutation based

## Warnings

- ⚠ interaction estimation implemented for 2x2 factorials only; larger designs get descriptives

## Model card (excerpt)

Mechanism laboratory for studying how heterogeneous trading behavior, liquidity, and exogenous shocks combine to generate market phenomena in a synthetic limit order book.

- Calibration: uncalibrated: parameters are hand-tuned, not fit to data
- Validation: mechanism-level tests only; no real-market validation
- Claims **not** supported: prediction of real market prices; statistically validated stylized facts; causal claims about real-world markets
- Limitations: not calibrated to any real market; no out-of-sample validation; single-seed preset outputs are demonstrations, not evidence; latency, queue position, and microstructure metrics not yet modeled

## Reproducibility

- Research hash: `60fabaa6f7ca84537f2339033a9841f8fa79576e11b27de93c69e313996f4466`
- Version: `expv_60fabaa6f7ca` (schema v2, code 0.1.0, seed allocator v1)
- Batch: `bat_60fabaa6f7ca` · Analysis: `ana_f55abb1b67d5` (seed 0)
- Reproduce: register this version from its stored record, run `BatchRunner(registry).run("expv_60fabaa6f7ca")`, then `analyze(registry, "expv_60fabaa6f7ca", seed=0)`.

*All numbers in this report are read from persisted artifacts (batch summary and analysis result); none are computed at render time. Results describe the specified synthetic model only.*