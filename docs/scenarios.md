# Custom Scenarios & TradeOps (Phase S2)

S2 turns the three presets from "the complete set of things Tezcat can do"
into "examples of what Tezcat can do": users design their own market
experiments, and an operational layer runs the laboratory.

    Research defines what should be experimented on.
    TradeOps manages how those experiments execute.

## Scenario / Experiment / Run — the semantic separation

| Object | Nature | Identity |
|---|---|---|
| **Scenario** | Mutable draft of a spec (editable, duplicable, deletable) | display id `scn_…`, no research identity |
| **Experiment** | Immutable research object minted from a spec | content-addressed `expv_…` + research hash |
| **Run** | One execution of an experiment | seed-derived, provenance-complete row |

Editing a draft is always allowed; registering an edited draft mints a
**new** experiment identity. Historical research objects are never mutated
(tested).

## One canonical path — no second machinery

The builder produces exactly the JSON spec the CLI consumes. Validation,
identity preview, and registration all pass through the existing
`ExperimentVersion` machinery:

```
Builder / template / import / preset conversion
      ↓ (same spec format)
POST /api/scenarios/validate      → exact identity preview or exact failure
POST /api/scenarios/register      → immutable experiment (idempotent)
      ↓
TradeOps queue → existing BatchRunner (chunked) → artifacts
      ↓
analyze → report → reproduce      (unchanged S1 loop)
```

A dashboard-built experiment is byte-for-byte as reproducible as a
CLI-registered one — the integration tests register the same spec both
ways and get the same research hash.

### What the builder exposes

Only parameters that exist in the engine: market (initial price, tick
size, order size/age caps), agent groups (the five types, counts,
cash/inventory/frequency), real behavioral params (herding, momentum
threshold, mean-reversion anchor/band, MM half-spread/quote size/skew,
base sizes), the F8 risk policy (initial/maintenance margin shown with the
implied leverage cap, liquidation delay/fraction), the three shock types on
a schedule timeline, and simulation settings (steps, replications, root
seed, primary metric, question + hypothesis). Liquidity is deliberately
**not** a dial — it emerges from the market-maker population. Nothing
decorative; nothing the engine can't execute.

Import/export uses the canonical spec JSON (no parallel format); invalid
imports surface the exact validation failure.

Templates ("try an idea") — High-Leverage Spiral, Retail Panic, Momentum
Bubble, Liquidity Vacuum — plus preset→scenario conversion are ordinary
specs validated through the same path (tested registrable, all with
distinct research hashes).

## TradeOps

An in-process queue + worker threads wrapping the existing `BatchRunner`
in budgeted chunks. Everything scientific is inherited, not reimplemented:

- **Retries never mint new identities**: seeds are re-derived per
  (cell, replication) by the versioned allocator; a retried batch resumes
  from persisted rows and ends byte-identical to an unfaulted reference
  (tested with injected storage failures).
- **One state machine**: `QUEUED → RUNNING → COMPLETED | FAILED |
  CANCELLED`. Cancellation is cooperative at chunk boundaries; cancelled
  jobs are retryable.
- **Failures are surfaced, never generic**: the exact error, rows
  persisted, and resumability are shown per job.
- **Duplicate-execution guard**: submitting an experiment that is already
  queued/running returns the existing job (HTTP 409 with the job record).
  Exactly one of N concurrent submissions wins (tested).
- **Restart honesty**: jobs orphaned by a process restart are marked
  `FAILED — process restarted while job was active` and are retryable.
- **Guardrails at the submission boundary only** (`TEZCAT_MAX_*` env vars,
  docs/docker.md): operator policy for shared deployments; experiment
  semantics are never modified — an over-limit experiment is still a
  valid, registrable, reproducible research object.

### API surface

```
GET  /api/scenarios/templates          GET  /api/ops/status
GET  /api/scenarios/from-preset/{id}   GET  /api/ops/workers
POST /api/scenarios/validate           GET  /api/ops/jobs[?state=]
POST /api/scenarios/register           GET  /api/ops/jobs/{id}
GET/POST /api/scenarios                POST /api/ops/jobs            (submit)
GET/PUT/DELETE /api/scenarios/{id}     POST /api/ops/jobs/{id}/cancel
POST /api/scenarios/{id}/duplicate     POST /api/ops/jobs/{id}/retry
```

The dashboard adds **BUILD** (the wizard: market → agents → behavior →
risk → shocks → simulation → review, with the research identity computed
server-side on the review screen) and **TRADEOPS** (status tiles, worker
table, job queue with cancel/retry). The RESEARCH page's RUN BATCH now
submits through the TradeOps queue.

## S1 compatibility

All 208 S1 tests remain green untouched; the seed-42 baseline remains
byte-identical; the S1 research API endpoints are unchanged (the direct
`/api/research/experiments/{ref}/batch` endpoint still exists for
backward compatibility).
