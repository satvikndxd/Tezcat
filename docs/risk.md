# Risk Engine: Margin, Liquidation, Tail Metrics, Stress Lab (Phase F8)

Risk mechanics are **per experiment version**: `RiskPolicy(enabled=False)` is
the default, so all legacy presets keep strict no-negative-cash semantics and
the frozen F0 baseline remains byte-identical.

## Margin model

```
equity        = cash + inventory × mark          (mark = last trade price)
position      = inventory × mark
margin ratio  = equity / position
leverage      = position / equity                (undefined when equity ≤ 0)
```

Negative cash **is** borrowing: cash transfers are zero-sum, so total-cash
conservation holds unchanged even with margin debt. A key mechanic follows
from the algebra: `ratio = 1 + cash/position`, so with *positive* cash a
falling price **raises** the ratio — only genuine debt makes falling prices
dangerous. Crises therefore require a levering-up phase first; the canonical
stress scenario is pump-then-dump for exactly this reason.

## Enforcement surfaces

1. **Order admission** (initial margin, replaces the strict cash check):
   - Limit buys: rejected (`"initial margin exceeded"`) unless
     `equity ≥ initial_margin × (position + reserved_cash + notional)` —
     resting orders' committed cash counts toward exposure.
   - Market buys: capped during matching by margin headroom (mirrors the
     legacy cash-cap), rejected only with zero headroom.
   - Sells always pass (they reduce exposure). Shorting remains disallowed.
2. **The liquidation process** — never an instantaneous mutation:
   - Each step, every account is marked. A maintenance breach emits a
     `margin_call` event and starts a clock.
   - After `liquidation_delay` steps of continuous breach, a `liquidation`
     event is emitted and a forced **market sell** for
     `liquidation_fraction × inventory` (capped by available inventory) is
     submitted *through the normal matching path* — forced flow consumes
     liquidity, moves price, and appears in the event log like any flow.
   - If the book is exhausted, the slice fails partially/fully and the
     breach clock keeps running (retried next step).
   - Recovery emits `margin_restored`; zero inventory with negative cash
     emits `default` once — the debt stays on the books.

The whale participant is exogenous and exempt.

## Events (schema v2)

`margin_call`, `margin_restored`, `liquidation`, `default` are forensic
records; the forced orders themselves are ordinary `order_accepted`/`trade`
events, so **event replay remains exact under margin** (tested: replayed
state hash equals the live engine's on stress runs) and every liquidation
event is followed in the log by its forced order for the same agent at the
same step (tested).

## Reconciliation

The run report's `risk_*` keys (margin calls, liquidation slices, forced
volume, defaults, max leverage, min margin ratio) are engine counters that
must equal what the event ledger shows — asserted in tests. The keys are
flat so batch designs can declare them as dependent variables; they are
absent entirely when risk is disabled (a design that requests them on a
risk-off config fails loudly).

## Tail metrics (`tezcat.risk.metrics.tail_risk`)

Historical VaR (interpolated quantile) and Expected Shortfall (mean of the
tail at/above VaR) over a **loss sample** — typically seed-level batch
outcomes. Method, α, and n are always in the output; ES always accompanies
VaR; n < 20 carries an explicit small-sample warning. Fixture-tested.

## Stress Lab (`tezcat.risk.stress`)

A stress scenario is an `ExperimentVersion` built by a named constructor —
it inherits all F4/F5/F6 machinery (content-addressed identity,
deterministic seeds, resumable batches, analysis, reports). Reproducing a
scenario is reproducing its version.

- `stressed_base_config()` — pump-then-dump: a sentiment pump levers up
  cash-light retail/momentum agents; a whale dump marks their collateral
  down. Cascades are endogenous: some seeds spiral (~50% drawdowns), some
  stay safe. Cross-seed variation is the point; use replications.
- `leverage_liquidity_grid()` — the canonical F8 phase experiment:
  initial margin (≈2× vs ≈10× leverage cap) × market-maker count.

## Acceptance evidence (80 runs, 20 reps/cell)

| Cell | forced vol (mean) | maxDD (mean) | maxDD ES₉₅ | liq. slices |
|---|---|---|---|---|
| im=0.5 · mm=1 | 0.0 | 0.025 | 0.038 | 0.0 |
| im=0.5 · mm=3 | 0.0 | 0.020 | 0.033 | 0.0 |
| im=0.1 · mm=1 | 1.8 | 0.133 | 0.252 | 1.2 |
| im=0.1 · mm=3 | 3.1 | 0.171 | 0.480 | 3.0 |

Readings the artifacts support: the leverage cap has a first-order effect on
drawdown and forced flow (low-margin cells liquidate; high-margin cells
never do); the leverage×liquidity interaction on forced volume is **not
resolved** at 20 reps/cell (bootstrap CI spans zero). One surfaced
subtlety: high-leverage cells with *more* MM liquidity show *more* filled
forced volume — thin books exhaust, so forced sells fail to execute; forced
volume measures fills, not intent. Liquidation *slices* tell the intent
side. No claim beyond the specified model is made.

## Known limitations

- Mark price is the last trade; no conservative/stressed marking options yet.
- `max_leverage_observed` can spike enormously as equity → 0⁺; it is real
  arithmetic, not an error, but read it alongside `min_margin_ratio`.
- Liquidation aggressiveness is market-order only; limit-order unwinds and
  open-order cancellation before liquidation are future options.
- No cross-agent lending network — the financier is implicit, so contagion
  is price-mediated only (funding-network contagion is an Ω-phase topic).
