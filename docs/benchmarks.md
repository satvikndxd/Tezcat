# Benchmarks, Parallel Execution, and Fault Tolerance (Phase F11)

## Protocol

`python benchmarks/bench.py` (full, ~2 min) or `--quick` (CI smoke, ~15s).
Every result carries environment metadata: Python, platform, CPU count,
**cgroup CPU quota**, code commit. The rules:

1. Numbers are *observations for one environment*, never capability claims.
2. Regression thresholds may be set only after ≥3 stable runs on dedicated
   hardware. Shared CI runners get a does-it-run smoke — timing assertions
   on shared runners are flaky by construction and are not used anywhere
   in the test suite.
3. A benchmark that bounds coverage says so; a benchmark whose environment
   throttles it says so (see below).

## Observations (this dev sandbox, commit `666fec3`)

Environment: Python 3.11.2, Linux x86-64, `cpu_count=48`,
**cgroup CPU quota = 1.0 effective CPU** (see honesty note).

| Scenario | Steps/s | Events/s | Notes |
|---|---|---|---|
| stable_baseline (1,500 steps, 45 agents) | ~1,200 | ~100k | |
| flash_crash (2,000 steps, 44 agents) | ~1,300 | ~96k | 150k events total |
| 60 agents | 656 | 104k | |
| 210 agents | 172 | 78k | |
| 510 agents | 66 | 69k | ~linear-ish in agents |
| 1,000 agents | 31 | 60k | |

- **Memory**: flash-crash run peaks ~82 MB traced (events dominate).
- **Checkpoint**: capture 0.4 ms; serialize 174 ms / 27.6 MB; restore 389 ms
  (stable-baseline, 1,500 steps of history carried).
- **Batch**: ~11 runs/s serial on the 200-step benchmark scenario.

## Parallel execution — and an honesty note

`BatchRunner.run(..., workers=N)` (CLI: `tezcat run --workers N`) fans
independent replications out to worker processes through **the same
`execute_run` function the serial path uses**, with a single writer in the
parent. Correctness properties, all tested:

- workers **1/4/8 produce byte-for-byte identical result rows** (seeds are
  per-(cell, replication) via the versioned allocator — order- and
  worker-independent);
- serial-partial → parallel-resume equals the serial reference;
- a failed worker leaves its key in explicit `failed_keys` + `pending_keys`
  and the batch stays resumable;
- concurrent duplicate runners on one batch converge to the serial
  reference with no duplicate or corrupt rows.

**Measured speedup here: none — and the benchmark says why.** This sandbox
caps CPU time at 1.0 effective CPU (`cgroup cpu.max = 100000/100000`) while
advertising 48 cores; parallel efficiency ≈ 1/workers is the *expected*
outcome under that quota, verified with a raw multiprocessing burn test.
The benchmark now records `cgroup_cpu_quota`/`effective_cpus` so a report
from a throttled environment explains itself instead of implying the worker
adapter is broken. Speedup claims await a multi-core environment; the
worker adapter's *correctness* does not.

Also honest: process startup dominates sub-second runs, so parallelism only
pays when per-run cost ≫ worker startup (long runs, big populations, risk
mechanics). The serial path remains authoritative.

## Fault injection (what the harness actually caught)

`tests/unit/test_parallel_and_faults.py` injects storage failures, garbage
tmp files, worker crashes, and duplicate concurrent runners. It caught a
**real bug** on first contact: `Registry._write` (and `LocalStore._write`)
used a *shared* tmp filename for atomic writes, so two writers racing on
the same key collided — one `os.replace` won and the other's tmp vanished
(`FileNotFoundError`). Fixed with writer-unique tmp names
(`<name>.<pid>.<tid>.tmp`), safe across threads and processes; stale tmp
files never match the `*.json` read globs.

Covered fault cases: mid-batch storage failure → loud error, persisted rows
survive, healthy runner resumes to the exact serial-reference rows; garbage
tmp from a crashed writer → reads and rewrites unaffected; worker
exceptions → explicit failure accounting, resumable; event-stream faults
(duplicate/missing/reordered) → `ReplayError` (Phase F3 tests).

## Differential reference matcher

`tests/unit/test_differential_matching.py` implements the documented
matching rules (docs/semantics.md) as a deliberately naive flat-list
matcher sharing only the `Portfolio` class, and drives both implementations
with identical randomized streams (6 seeds × 400 orders): trades must agree
trade-by-trade (price, quantity, counterparties), final book composition
must match, and portfolio floats must be **bit-identical**. Any future
optimization of the matching engine must keep passing this test unchanged.

## Known limits / deferred

- Distributed execution beyond one machine (remote workers, queues) stays
  out until a real workload demands it — the process-pool adapter plus
  content-addressed resumability is the foundation it would build on.
- The event log is the main memory/throughput cost at scale (by design:
  research mode is event-complete). Compressed encodings are the first
  optimization candidate, gated behind the differential and replay tests.
