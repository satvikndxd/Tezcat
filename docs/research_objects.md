# Research Objects: Experiment Versions, Designs, Batches (Phases F4/F5)

## The unit of scientific intent

A run is not a research object; an **experiment version** is
(`tezcat.experiments.schema.ExperimentVersion`): a base config, a validated
`DesignSpec`, a deterministic seed plan, a model card, and a content hash.

```
Experiment (id, human intent)
   └── ExperimentVersion  (immutable; research_hash; optional parent link)
         ├── DesignSpec   (question, hypothesis, IVs/DVs, control,
         │                 treatments/factors, replications, budget)
         ├── design matrix: cells × replications → (cell, rep, seed, config_hash)
         └── Batch (canonical, content-addressed: bat_<research_hash[:12]>)
               ├── result rows  (one per run: seed-level metrics + provenance)
               └── summary      (per-cell descriptive statistics)
```

### Research identity

`research_hash = H(schema_version, seed_allocator_version, code_version,
base_config, design, root_seed, parent_version_id)`. It names *semantics*:
change the code version, the event schema, the seed allocator, the design,
or a single parameter and the identity changes. `version_id` is derived from
it (`expv_<hash[:12]>`), so registration is content-addressed and idempotent.
`Registry.load` re-verifies the hash; a hand-edited stored record fails
loudly.

## Design validation (rejected before anything runs)

- `primary_metric` must be one of `dependent_variables`.
- Non-baseline designs require **≥ 2 replications** — one seed is not
  evidence.
- `ab`/`ablation`: control arm + ≥ 1 treatment, unique arm names, treatments
  must actually differ from control, `independent_variables` required.
- `sweep`: exactly one factor with ≥ 2 levels. `factorial`: ≥ 2 factors,
  full cartesian product.
- Every cell config is resolved and validated at version construction:
  unknown override paths (`KeyError`) and invalid values
  (`ValidationError`) are rejected up front. Override paths are strict
  except that a *final* segment may be created inside a strategy `params`
  dict (open-ended by design).
- Budget: `planned_runs = cells × replications` must fit `max_runs`.

## Deterministic seeds

`seed_for(cell, replication) = derive_seed(root_seed, "cell", cell_name,
"replication", index)` — order- and worker-independent, versioned by the
seed allocator, pinned by tests.

## Batch execution (`tezcat.experiments.batch`)

- One canonical batch per version (same identity → always a resume).
- Each completed run persists a **seed-level row**: cell, replication, seed,
  config hash, declared metrics, state hash, event hash, event-log chain.
- Existing rows are never re-executed (idempotent resume, tested).
- `max_runs` caps a call and leaves an explicit `partial` status with
  `pending_keys` — no silent truncation.
- A declared dependent variable missing from the run report raises
  (`MetricMissing`), it is not skipped.
- Execution is serial by design: the trusted serial reference precedes any
  distributed adapter (F11).

## Aggregation (`tezcat.experiments.aggregation`)

Descriptive only: per-cell `n`, mean, sample std, min, median, max, and the
full seed-level `values` list per dependent variable. Partial batches
aggregate with `complete: false` and explicit `missing_keys`. No p-values,
no intervals, no conclusions — statistical inference is Phase F6, on top of
these seed-level artifacts.

## Lineage

Versions link to parents (`parent_version_id`), giving an experiment DAG:
`Registry.children`, `Registry.lineage` (cycle-safe). Fork lineage at the
*run* level (checkpoint hash + intervention) is Phase F3
([docs/events.md](events.md)); the two compose: a follow-up experiment
version can cite the parent version whose results motivated it.

## Model cards

Every version carries a `ModelCard`; the default card states what the
current model **does not** support (price prediction, validated stylized
facts, real-world causal claims) and its calibration status
(`uncalibrated`). Reports built in F6 must surface it.

## Acceptance evidence

- Registered a baseline and a controlled herding treatment **without
  executing them** (`test_register_baseline_and_herding_treatment_without_running`).
- Herding × market-maker-liquidity 2×2 factorial, 20 replications/cell
  (80 runs, ~23s serial): completed with per-cell seed-level distributions
  and zero missing runs. Observed fatter max-drawdown tails in high-herding
  cells (max 0.0185/0.0140 vs 0.0060/0.0020) — reported as a demonstration;
  no inferential claim is made before Phase F6.
- Registry queries at 10,000 index records complete well under the 5s test
  bound.
