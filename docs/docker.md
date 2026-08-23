# Containers, Environment Variables, and Daytona Readiness (Phase S2)

## One-command launch

```bash
docker compose up --build
# open http://localhost:8000  — dashboard, research API, TradeOps workers
```

Everything stateful lives under the mounted `./data` directory (registry,
scenarios, ops job records, run artifacts). Startup is deterministic: no
network calls, no migrations, no external services.

The image also carries the research CLI and the example specs:

```bash
docker compose exec tezcat tezcat run examples/margin_spiral_ab.json
docker compose exec tezcat tezcat reproduce 417305c1
```

A standalone worker is just the CLI against the same mounted data dir —
there is no separate worker daemon to operate.

## Environment variables (complete list)

| Variable | Default | Meaning |
|---|---|---|
| `TEZCAT_STORE` | `local` | `local` JSON store or `aws` (S3/DynamoDB adapters) |
| `TEZCAT_DATA_DIR` | `data` | Root for registry, scenarios, ops jobs, artifacts |
| `TEZCAT_FRONTEND_DIST` | `frontend/dist` | Built dashboard location |
| `TEZCAT_OPS_WORKERS` | `2` | TradeOps worker threads inside the API process |
| `TEZCAT_OPS_CHUNK` | `5` | Runs per execution chunk (cancel granularity) |
| `TEZCAT_MAX_QUEUE` | `20` | Max queued jobs (submission rejected beyond) |
| `TEZCAT_MAX_ACTIVE` | `4` | Active-job capacity component |
| `TEZCAT_MAX_REPLICATIONS` | `200` | Max replications per submitted experiment |
| `TEZCAT_MAX_STEPS` | `5000` | Max simulation steps per submitted experiment |
| `TEZCAT_MAX_PLANNED_RUNS` | `400` | Max total planned runs per submission |
| `TEZCAT_S3_BUCKET` etc. | — | AWS store only; see docs/deployment.md |

The `TEZCAT_MAX_*` guardrails are **operator policy applied at the TradeOps
submission boundary**. They never modify experiment semantics: an
experiment too large for one deployment's limits is still a valid,
registrable, reproducible research object — it simply won't be executed on
that deployment. Public demos should lower these values (the compose file
shows a demo-sized set).

## Public demo posture

- Bounded replications/steps/queue via the variables above.
- The research registry, scenario drafts, and ops job history survive
  restarts on the mounted volume; interrupted jobs are surfaced as
  `FAILED — process restarted while job was active` and are retryable
  (batches resume from persisted rows with identical seeds).

## Daytona readiness

What exists now — the properties a Daytona workspace launch needs:

- reproducible container image (pinned base images, `pip install .` of a
  hash-versioned package);
- deterministic startup with a single command;
- all environment variables documented above;
- persistent state confined to one mountable directory (`/data`);
- experiment identity independent of the host: `tezcat reproduce <hash>`
  inside any container with the same code version verifies stored results.

**Future scope (not implemented):** Daytona-specific orchestration — the
"experiment hash → pinned workspace → run → reproduce" flow as a service,
workspace lifecycle management, and multi-workspace scheduling. The
container contract above is deliberately the entire interface such an
orchestrator would need; nothing in the research kernel would change.

## What is deliberately absent

- No live external-provider scraping in containers by default: the S3
  Kalshi/Polymarket layer ([docs/markets.md](markets.md)) is offline-first
  and requires an explicit server-side `TEZCAT_EXTERNAL_LIVE=1` opt-in.
- No multi-machine distributed execution (single-machine TradeOps workers
  \+ `--workers N` process pools exist; remote workers would build on the
  same content-addressed resumability).
