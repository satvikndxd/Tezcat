# Phase F0 — Repository Audit and Baseline Freeze

**Baseline commit:** `2dbeb8c1db41f9e7377faa6365854224a1b49432`
**Environment:** Python 3.11.2 (Debian 12), venv install of `tezcat[dev]`
**Date:** 2026-08-20

This document freezes the observed baseline before any Fable phase changes
(F1+). Figures below are **repository evidence**, not scientific results:
they demonstrate that the scenarios execute deterministically at one seed;
they are not Monte Carlo estimates and carry no uncertainty statement.

## Baseline commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests -q          # 51 passed, 1 warning (~7s)
.venv/bin/python scripts/smoke.py            # seed-42 preset demonstration
```

## Test suite

`51 passed, 1 warning in 7.06s` — matches the audit record exactly
(51 passed, 1 warning). The warning is a Starlette test-client deprecation,
unrelated to engine semantics.

## Seed-42 smoke demonstration (verbatim from `scripts/smoke.py`)

| Preset | Steps | Final return | Max drawdown | Volatility | Crash flag | Liquidity flag | Regime shares |
| --- | --- | --- | --- | --- | --- | --- | --- |
| stable_baseline | 1,500 | -0.05% | 0.10% | 0.00050 | false | false | stable 100% |
| flash_crash | 2,000 | +1.85% | 29.39% | 0.00434 | true | true | stable 58.25%, crisis 15.75%, recovery 26.00% |
| bubble_formation | 2,200 | +15.45% | 17.04% | 0.00065 | true | false | stable 100% |

Determinism check (engine-output level): same seed identical = True;
different seed differs = True.

Note the definition-leakage example the audit calls out: the bubble run
reports `crash=True` purely because max drawdown (17.04%) exceeds the 15%
`crash_drawdown_threshold`, while the regime engine stays `stable` for the
whole run. "Crash detected" is currently a drawdown threshold, not a
validated crash event (to be separated in a later phase).

## Implemented vs. aspirational (frozen verdict)

Implemented at MVP level: synthetic CLOB with price-time priority and partial
fills; five heterogeneous agent strategies; reservation-based accounting with
basic conservation checks; scheduled/manual shocks; rule-based regimes;
seeded single-run engine determinism; REST API + React dashboard; optional
AWS adapters; 51-test suite.

Not implemented at baseline (roadmap claims, not current facts): immutable
experiment versions; deterministic artifact identity (UUIDs, module-level
`itertools.count` counters for trades/reports/shock/regime events);
complete event sourcing; checkpoints/forks/replay; Monte Carlo replications,
sweeps, factorials, ablations; statistical inference; microstructure/TCA
layer; leverage/margin/liquidation/contagion; calibration against real data;
CI; benchmark thresholds.

## Known correctness risks frozen at baseline

1. `Experiment` is a mutable dataclass storing a creation-time hash — a
   caller can mutate `config` without invalidating `config_hash`.
2. `uuid.uuid4()` run/experiment IDs and module-level counters
   (`matching._trade_counter`, `metrics._report_counter`,
   `shocks._event_counter`, `regimes._event_counter`) make identifiers
   process-order dependent even when market state is reproducible.
3. `OrderBook.cancel` pops from `_orders` before confirming side removal —
   under internal inconsistency a reservation could leak silently.
4. No duplicate-order-ID or self-trade policy is defined.
5. Live runs persist artifacts only at completion; API state is
   process-local.
6. Cash uses binary floats with an implicit `1e-6` tolerance; no explicit
   monetary precision policy.

These are the inputs to phases F1 (financial semantics) and F2 (deterministic
identity and provenance).
