"""Canonical benchmark suite (Phase F11).

Measures engine throughput, event/artifact overhead, checkpoint cost, and
batch scaling on fixed scenarios, and records the environment alongside
every number — a benchmark without its machine is a rumor.

Usage::

    python benchmarks/bench.py            # full suite (~2-3 min)
    python benchmarks/bench.py --quick    # CI smoke (~15s)
    python benchmarks/bench.py --json out.json

Protocol (docs/benchmarks.md): numbers are *observations for this
environment*, not capability claims. Regression thresholds may be set only
after >=3 stable runs on dedicated hardware; shared CI runners get a
does-it-run smoke, never timing assertions.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tezcat import __version__  # noqa: E402
from tezcat.core.config import (  # noqa: E402
    AgentGroupConfig, AgentType, ExperimentConfig,
)
from tezcat.engine.ecology import EcologyEngine  # noqa: E402
from tezcat.experiments.presets import build_config  # noqa: E402


def _cgroup_cpu_quota():
    """Effective CPUs under a cgroup v2 quota, or None if unlimited.

    Containers routinely advertise the host's core count while capping
    actual CPU time — without this field, parallel-efficiency numbers are
    uninterpretable (parallel efficiency ~ 1/workers on a 1-CPU quota is
    the *expected* result, not a bug)."""
    try:
        parts = open("/sys/fs/cgroup/cpu.max").read().split()
        if parts and parts[0] != "max":
            return round(int(parts[0]) / int(parts[1]), 2)
    except (OSError, ValueError, IndexError):
        pass
    return None


def environment() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True,
                                timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = "unknown"
    quota = _cgroup_cpu_quota()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cgroup_cpu_quota": quota,
        "effective_cpus": quota if quota is not None else os.cpu_count(),
        "tezcat_version": __version__,
        "git_commit": commit,
    }


def _run_engine(config: ExperimentConfig, seed: int = 42):
    eng = EcologyEngine("bench", config, seed)
    t0 = time.perf_counter()
    while not eng.done:
        eng.step()
    return eng, time.perf_counter() - t0


def bench_engine(name: str, config: ExperimentConfig) -> dict:
    eng, dt = _run_engine(config)
    n_events = len(eng.events_log)
    return {
        "scenario": name,
        "steps": eng.step_num,
        "agents": len(eng.agents),
        "seconds": round(dt, 3),
        "steps_per_sec": round(eng.step_num / dt),
        "events": n_events,
        "events_per_sec": round(n_events / dt),
        "trades": len(eng.trades),
        "trades_per_sec": round(len(eng.trades) / dt),
    }


def bench_memory(config: ExperimentConfig) -> dict:
    tracemalloc.start()
    eng, _ = _run_engine(config)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"scenario": "memory_flash_crash", "steps": eng.step_num,
            "events": len(eng.events_log),
            "peak_mb": round(peak / 1024 / 1024, 1)}


def bench_checkpoint(config: ExperimentConfig) -> dict:
    eng, _ = _run_engine(config)
    t0 = time.perf_counter()
    cp = eng.checkpoint()
    dt_capture = time.perf_counter() - t0
    t0 = time.perf_counter()
    blob = json.dumps(cp)
    dt_serialize = time.perf_counter() - t0
    t0 = time.perf_counter()
    EcologyEngine.restore(json.loads(blob), config)
    dt_restore = time.perf_counter() - t0
    return {"scenario": "checkpoint", "capture_ms": round(dt_capture * 1000, 1),
            "serialize_ms": round(dt_serialize * 1000, 1),
            "restore_ms": round(dt_restore * 1000, 1),
            "size_mb": round(len(blob) / 1024 / 1024, 2)}


def bench_batch_scaling(tmp_root: str, replications: int) -> list:
    """Serial vs parallel batch throughput and parallel efficiency."""
    from tezcat.experiments.batch import BatchRunner
    from tezcat.experiments.registry import Registry
    from tezcat.experiments.schema import DesignSpec, ExperimentVersion

    config = ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=10),
                AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=8,
                                 params={"herding": 0.5}),
                AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2)],
        total_steps=200)
    design = DesignSpec(
        design_type="baseline", question="Benchmark throughput scenario?",
        hypothesis="Not a hypothesis; a fixed workload.",
        dependent_variables=["max_drawdown"], primary_metric="max_drawdown",
        replications=replications)

    out, serial_rate = [], None
    worker_counts = [1, 2, 4, min(8, os.cpu_count() or 4)]
    for workers in dict.fromkeys(worker_counts):
        reg = Registry(os.path.join(tmp_root, f"bench_w{workers}"))
        vid = reg.register(ExperimentVersion("bench", "throughput", config, design))
        t0 = time.perf_counter()
        batch = BatchRunner(reg).run(vid, workers=workers)
        dt = time.perf_counter() - t0
        rate = batch["completed"] / dt
        if workers == 1:
            serial_rate = rate
        out.append({"scenario": "batch_scaling", "workers": workers,
                    "runs": batch["completed"], "seconds": round(dt, 2),
                    "runs_per_sec": round(rate, 2),
                    "parallel_efficiency": round(rate / (serial_rate * workers), 2)
                    if serial_rate else None})
    return out


def bench_agent_scale(quick: bool) -> list:
    out = []
    for n_noise, steps in ([(50, 200), (200, 200)] if quick
                           else [(50, 500), (200, 500), (500, 500), (990, 200)]):
        groups = []
        remaining = n_noise
        while remaining > 0:                      # per-group cap is 500
            c = min(remaining, 500)
            groups.append(AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=c))
            remaining -= c
        groups.append(AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=10))
        cfg = ExperimentConfig(agents=groups, total_steps=steps)
        out.append(bench_engine(f"scale_{n_noise + 10}_agents", cfg))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="CI smoke (~15s)")
    ap.add_argument("--json", default=None, help="write results to a file")
    args = ap.parse_args(argv)

    import tempfile
    results = {"environment": environment(), "quick": args.quick, "benchmarks": []}
    b = results["benchmarks"]

    stable = build_config("stable_baseline")
    flash = build_config("flash_crash")
    if args.quick:
        # Quick mode is a throughput smoke: shorten runs and drop scheduled
        # shocks (they'd land beyond the truncated horizon).
        from tezcat.experiments.schema import apply_overrides
        stable = apply_overrides(stable, {"total_steps": 300, "shocks": []})
        flash = apply_overrides(flash, {"total_steps": 300, "shocks": []})

    b.append(bench_engine("stable_baseline", stable))
    b.append(bench_engine("flash_crash", flash))
    b.extend(bench_agent_scale(args.quick))
    b.append(bench_memory(flash))
    b.append(bench_checkpoint(stable))
    with tempfile.TemporaryDirectory() as td:
        b.extend(bench_batch_scaling(td, replications=4 if args.quick else 16))

    for row in b:
        print(json.dumps(row))
    print(json.dumps({"environment": results["environment"]}))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)
        print(f"written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
