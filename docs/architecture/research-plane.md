# The Research Control Plane (Phase S5)

Tezcat S5 turns the platform into a **reproducible research control plane
for experimental finance**: forecasts, portfolio decisions, execution
behavior, external observations, and synthetic market mechanisms become
experimentally testable components of one provenance-linked system.

```
Observation → Hypothesis → Forecast → Portfolio Decision
    → Market World → Execution → Risk → Counterfactual → Reproduction
```

## What Tezcat is — and is not

Tezcat is **research infrastructure**: an experimental operating layer
for constructing, stressing, comparing, and reproducing multi-stage
quantitative workflows. It is *not* a hedge fund, a trading bot, a
prediction system, a brokerage, or a replacement for any specialized
desk it integrates. The unique value is **the seam between specialized
systems** — typed contracts, one provenance graph, controlled
counterfactuals.

## Kernel protection (non-negotiable)

The dependency direction is `external systems → adapters → Tezcat`,
never the reverse. Nothing in `tezcat.core`/`tezcat.engine` imports
forecasting, portfolio, Nautilus, or AI packages; every adapter is a
lazily imported optional extra. If every integration disappeared, the
synthetic-market laboratory still runs — the base test suite passes with
none of the optional dependencies installed.

## The desks

| Desk | Contract | Reference implementation | Optional adapter |
|---|---|---|---|
| Forecasting | `ForecastModel` (tezcat/plane/forecasting.py) | seeded block-bootstrap (no predictive claim) | Kronos or any model implementing the protocol |
| Portfolio | `PortfolioOptimizer` (tezcat/plane/portfolio.py) | deterministic CVaR grid | skfolio MeanRisk/CVaR |
| Execution | S4 Strategy Lab (tezcat/lab) | — | NautilusTrader (backtest only) |
| Observation | S3 external layer (tezcat/external) | labeled fixtures | Kalshi / Polymarket read-only |

No vendor stack is hard-coded into Tezcat semantics; each desk is
replaceable behind its contract, and every adapter records provider,
version, and adapter version so dependency drift is detected at
reproduction time — old artifacts remain historically valid, new runs
get new environment records.

## The artifact graph (see artifact-graph.md)

Every workflow stage is a typed, hashed, immutable node with parent
hashes. The graph is the product: the connected libraries are
interchangeable nodes.

## The first vertical slice (S5-D)

`tezcat plane slice <experiment-ref>` executes
**forecast → portfolio → stress worlds → Nautilus → risk → report** as
linked artifacts, with the measured **edge-decay pipeline** (model edge →
portfolio gross → net of declared costs → realized across stress worlds)
and whole-chain reproduction (`tezcat plane reproduce <report>`).

## Deferred, deliberately (per the phase discipline)

* **Experiment DAG executor / TradeOps job types** — the graph records
  provenance today; generalized DAG scheduling is a later phase.
* **AI research analyst (Vibe-Trading/MCP)** — when added, it reads
  artifacts and proposes hypotheses behind a human/policy gate; it never
  places orders, mutates artifacts, or enters the measurement loop
  (metrics, statistics, hashes stay deterministic).
* **Infrastructure parity** (same DAG on Local/FLoCI/Daytona/AWS with
  hash comparison) — the environment fingerprint on every artifact is
  the hook; the cross-backend harness is future work.
* **Market–strategy coevolution** — gated on the S4 co-simulation
  contract (docs/lab.md).
