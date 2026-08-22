# Tezcat Architecture

## Runtime flow

```
Experiment Config ──▶ Ecology Engine ──▶ per-step loop:
                                          1. env decay + shocks (scheduled/manual/whale programs)
                                          2. regime evaluation (hysteresis) → modifiers
                                          3. expire stale resting orders
                                          4. agents observe (memory update) + decide
                                          5. validate → reserve → match → settle
                                          6. snapshot + step metrics
                                          7. terminate at total_steps → report
```

## Components

| Component | Module | Notes |
|---|---|---|
| Order book | `tezcat/market/order_book.py` | Price levels keyed by integer ticks; FIFO deques per level (time priority) |
| Matching engine | `tezcat/market/matching.py` | Execution at resting price; partial fills; market orders cash-capped |
| Portfolio | `tezcat/agents/portfolio.py` | Reservation-based: resting buys reserve cash, resting sells reserve inventory |
| Agents | `tezcat/agents/traders.py` | 5 types registered in `AgentRegistry`; add a type without touching the engine |
| Memory | `tezcat/memory/agent_memory.py` | Bounded EMA state: fear, confidence, trend/value beliefs |
| Shocks | `tezcat/shocks/engine.py` | Whale programs execute magnitude over duration steps; env shocks decay |
| Regimes | `tezcat/regimes/engine.py` | stable/crisis/recovery; needs N consecutive confirmations (no flapping) |
| Metrics | `tezcat/metrics/engine.py` | Incremental (O(1)/step); relative-spread baselines for liquidity detection |
| Run manager | `tezcat/api/runner.py` | Thread per run; pause/resume/cancel; persists artifacts on completion |
| Stores | `tezcat/persistence/` | `LocalStore` mirrors the S3 bucket layout; `AwsStore` is drop-in |

## Determinism

One `random.Random(seed)` drives agent shuffling and every agent decision.
No wall-clock or unordered-dict iteration in the hot path. Verified by
`tests/simulation/test_ecology.py::test_determinism_same_seed`.

## Why the flash crash works

The book is intentionally thin (orders expire after `max_order_age` steps, agents
quote near mid). A whale *program* (magnitude spread over duration) outpaces
liquidity refill → drawdown → crisis regime (agents' aggression up, risk down,
MM spreads wide, cancels up) → retail panic-sells (fear + sentiment) → depth
collapses. Recovery emerges because mean-reversion traders anchor partly to a
fundamental price and buy the dislocation, market makers re-enter, and fear decays.

## Cloud execution model

Local: RunManager threads. AWS: API Gateway → control Lambda (Mangum-wrapped
FastAPI) writes run metadata to DynamoDB and emits `RunChunkRequested`;
worker Lambda replays deterministically up to a chunk boundary, checkpoints,
re-emits until done, then writes artifacts to S3. See `infra/templates/template.yaml`.


## Phase S2 additions (custom scenarios + TradeOps)

Two layers were added *around* the frozen research kernel, never through it:

- **Scenarios** (`tezcat/experiments/scenarios.py`): mutable drafts and
  templates that compile into the canonical experiment spec; validation,
  identity preview, and registration reuse `ExperimentVersion` verbatim —
  there is no second execution or hashing path.
- **TradeOps** (`tezcat/ops/`): an in-process queue + worker threads that
  execute batches through the existing `BatchRunner` in budgeted chunks,
  adding job states, explicit failures, identity-preserving retries, a
  duplicate-execution guard, and env-configurable capacity guardrails at
  the submission boundary. See docs/scenarios.md and docs/docker.md.
