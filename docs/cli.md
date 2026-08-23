# Tezcat Research Guide (Phase F10)

You do not need to know anything about Tezcat's internals to use this guide.
The complete research loop is five commands.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
export PATH="$PWD/.venv/bin:$PATH"        # or call .venv/bin/tezcat directly
```

## The loop

```bash
tezcat run examples/margin_spiral_ab.json   # register + execute (40 runs, ~30s)
tezcat analyze <version>                    # bootstrap CIs, permutation p, effects
tezcat report  <version> -o report.md       # markdown report from artifacts
tezcat reproduce <hash>                     # re-execute and verify every hash
tezcat list                                 # what's registered
```

`<version>` is the `expv_…` id printed by `run`; `<hash>` may be the full
research hash or any unambiguous prefix (≥8 chars) — exactly what you'd cite
in a paper.

### What `run` does

Registers an **immutable, content-addressed experiment version** from your
spec (validated *before* anything executes — a missing control, fewer than
2 replications, a typo'd parameter path, or a budget overrun is rejected
with a clear message), then executes its batch with deterministic
per-(cell, replication) seeds. Interrupt it or cap it with `--max-runs N`;
re-running the same command **resumes** — completed runs are never
re-executed, and nothing is silently dropped.

### What `reproduce` does

```
✓ experiment identified  (expv_417305c181af)
✓ research hash verified  (417305c181afbf9c…)
✓ stored batch rows found  (40 rows in bat_417305c181af)
✓ c000_r0000: seed allocation
✓ c000_r0000: state hash
✓ c000_r0000: event hash
✓ c000_r0000: event-log chain
✓ c000_r0000: metrics
…
✓ REPRODUCTION SUCCESSFUL — 3/3 sampled runs match all stored hashes
```

It re-verifies the research identity (schema/code/seed-allocator drift
fails loudly), re-executes a deterministic sample of runs (`--full` for
all), and compares **every** stored hash byte-for-byte: state hash, event
hash, event-log chain, and metrics. A single falsified number in a stored
artifact makes it fail — this is tested.

## Writing an experiment spec

A spec is one JSON file:

```json
{
  "experiment_id": "exp_herding",
  "name": "herding-ab",
  "root_seed": 42,
  "config": {
    "agents": [
      {"agent_type": "noise_trader",  "count": 12},
      {"agent_type": "retail_trader", "count": 10, "params": {"herding": 0.5}},
      {"agent_type": "market_maker",  "count": 3}
    ],
    "total_steps": 500
  },
  "design": {
    "design_type": "ab",
    "question":   "Does retail herding increase crash severity?",
    "hypothesis": "Higher herding raises max drawdown at fixed liquidity.",
    "independent_variables": ["agents.1.params.herding"],
    "dependent_variables":   ["max_drawdown", "total_return"],
    "primary_metric": "max_drawdown",
    "control":    {"name": "low",  "overrides": {"agents.1.params.herding": 0.2}},
    "treatments": [{"name": "high", "overrides": {"agents.1.params.herding": 0.8}}],
    "replications": 50
  }
}
```

Design types: `baseline`, `ab`, `ablation`, `sweep` (one `factors` entry),
`factorial` (≥2 `factors`). Overrides are dotted config paths
(`market.tick_size`, `agents.1.params.herding`, `risk.initial_margin`).
Add `"risk": {"enabled": true, ...}` inside `config` for margin/liquidation
mechanics — see [docs/risk.md](risk.md). Metrics you can declare as
dependent variables are the run-report keys: `max_drawdown`,
`total_return`, `realized_volatility`, `total_trades`, `total_volume`, and
(when risk is enabled) `risk_forced_volume`, `risk_liquidation_slices`,
`risk_margin_calls`, `risk_max_leverage`.

## The same loop over HTTP

Everything the CLI does is exposed under `/api/research`:

```
POST /api/research/experiments                      register (or validate_only)
GET  /api/research/experiments                      list
GET  /api/research/experiments/{ref}                record (id, hash, or prefix)
POST /api/research/experiments/{ref}/batch          execute (background, 202)
GET  /api/research/batches/{batch_id}               batch status
GET  /api/research/experiments/{ref}/summary        per-cell seed-level summary
POST /api/research/experiments/{ref}/analyze        statistical analysis
GET  /api/research/experiments/{ref}/analysis       stored analysis artifact
GET  /api/research/experiments/{ref}/report         markdown report
POST /api/research/experiments/{ref}/reproduce      verification checklist
```

The dashboard's **RESEARCH** section (top navigation) drives the same
endpoints: pick an experiment → RUN BATCH → ANALYZE → REPORT → REPRODUCE.

## The worked example

`examples/margin_spiral_ab.json` asks: *does the leverage cap determine
whether a pump-and-dump ends in a liquidation spiral?* Control: tight
margin (2× leverage cap). Treatment: loose margin (10×). Same shocks, same
ecology, 20 replications each. One observed outcome (seed 42 registry):

```
tight_margin_2x    n=20  max_drawdown mean=0.0217  q95=0.0306
loose_margin_10x   n=20  max_drawdown mean=0.1847  q95=0.4122
Δ=0.163  CI=[0.103, 0.229]  p_holm=0.0005
```

Reproduce it from the hash in the report — that's the whole point.

## Where things live

All artifacts are plain JSON under the data directory
(`$TEZCAT_DATA_DIR`, default `./data`): `registry/versions/` (immutable
experiment records), `registry/results/<batch>/` (seed-level rows with all
hashes), `registry/analysis/`, `registry/reports/`. Nothing needs a
database, the network, or AWS.

## Starting from an external market observation (Phase S3)

The `tezcat markets` group feeds the same five-verb loop from real
prediction-market data — read-only, offline by default, with immutable
checksummed datasets:

```bash
tezcat markets import kalshi <ticker> --fixture <bundle.json>  # or live with TEZCAT_EXTERNAL_LIVE=1
tezcat markets show exd_…            # lineage + observations (labeled)
tezcat markets signature exd_…       # hashed, versioned event signature
tezcat markets propose exd_…         # candidate mechanisms (hypotheses)
tezcat markets research exd_… --mechanisms herding,mm_withdrawal --run
tezcat markets compare exd_A exd_B   # cross-provider divergence + lead/lag
```

`markets research` mints an ordinary `ExperimentVersion` whose research
manifest binds the experiment hash to the exact dataset version, so
`tezcat reproduce` verifies the experiment *with respect to that data*.
Full guide: [docs/markets.md](markets.md).
