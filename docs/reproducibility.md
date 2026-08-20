# Reproducibility Contract (Phase F2)

A Tezcat result is *reproducible* when the same *research identity* produces
the same canonical hashes. A `seed` alone is **not** an identity. The
identity components, in comparison order:

```
1. schema_version        (tezcat.core.config.SCHEMA_VERSION)
2. config_hash           (sha256 over {schema_version, config} canonical JSON)
3. code_version          (tezcat.__version__; CI records the commit)
4. seed (+ allocator)    (root seed; derived seeds via seeds.ALLOCATOR_VERSION)
5. event_hash            (trades, snapshots, shock events, regime events)
6. state_hash            (book, portfolios, env, regime, step, last price)
7. artifact checksums    (manifest.json → sha256 per artifact)
```

On any mismatch, the *first* differing component identifies the divergence
class: schema drift, config drift, code drift, seed-plan drift, or
nondeterminism.

## What is guaranteed (tested in `tests/unit/test_determinism.py`)

- Same config + seed ⇒ identical `state_hash` and `event_hash`, in-process
  across engine instances **and** across fresh Python processes.
- Different seeds ⇒ divergent event hashes.
- All identifiers are **run-scoped and deterministic**: trade IDs
  (`trd_…`), shock event IDs (`shk_…`), regime event IDs (`rgm_…`), and the
  report ID (`rpt_<run_id>`) restart per run and repeat exactly on
  reproduction. (Before F2 these were process-global `itertools.count`
  counters — semantically identical runs produced different IDs.)
- Seed derivation (`tezcat.core.seeds`) is pure SHA-256 over the component
  path and pinned by test constants; changing the derivation rule requires
  bumping `ALLOCATOR_VERSION`.
- Configs are frozen (immutable pydantic models). `Experiment.verify_hash()`
  is called before every run launch and fails loudly (HTTP 409) if the
  stored hash does not match the config content.

## What is NOT yet guaranteed (deliberate scope limits)

- `experiment_id` / `run_id` remain UUID-based **display identifiers**; they
  are provenance metadata, not content addresses. Content identity is the
  hash tuple above.
- `created_at` / `started_at` timestamps are wall-clock provenance, excluded
  from all hashes.
- The engine uses a single named RNG (`random.Random(seed)`); per-purpose
  named streams (`SeedPlan.stream_seed`) exist but are not yet wired into
  the engine, to keep the F0 baseline byte-identical. Wiring them is an
  event-hash-breaking change and will ship with a `SCHEMA_VERSION` bump.
- Live-run pause/resume/shock-injection makes a run's event stream depend on
  operator actions; such runs are demonstrations. Only untouched runs are
  reproducible research objects.
- Checkpoint/restore and event replay are Phase F3.

## Hash canonicalization rules

- Canonical JSON: sorted keys, minimal separators, **strict** typing — any
  non-JSON-native value raises (`default=str` fallbacks are forbidden in
  hash paths).
- Floats inside `state_hash` are encoded with `repr` (shortest exact
  round-trip), so states hash equal iff bit-identical on the same
  float64 platform.
- `schema_version` is embedded in every hash payload: identical parameters
  hash differently across schema revisions, because a hash names semantics.

## Run artifacts and manifest

Every completed run persists, in this order: `report/report.json`,
`trades/trades.json`, `snapshots/snapshots.json`,
`metrics/step_metrics.json`, `shocks/shock_events.json`,
`regimes/regime_events.json`, and finally `manifest.json` containing
`{run_id, experiment_id, config_hash, seed, schema_version, code_version,
state_hash, event_hash, artifact_checksums}`. The manifest is written last,
so its presence implies the artifacts it names exist. The report embeds the
same provenance block.

## Reproducing a run

```python
from tezcat.engine.ecology import EcologyEngine
eng = EcologyEngine(run_id, config, seed)   # config from the experiment record
while not eng.done:
    eng.step()
assert eng.event_hash() == manifest["event_hash"]
assert eng.state_hash() == manifest["state_hash"]
```

A `tezcat reproduce <hash>` CLI over this contract is planned for a later
phase (F10); the contract itself is in force now.
