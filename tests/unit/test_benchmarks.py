"""Phase F11: the benchmark suite runs and reports the right structure.

Timing values are never asserted (shared CI runners make timing assertions
flaky by construction — see docs/benchmarks.md); this guards that the
harness itself keeps working.
"""

from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig

import benchmarks.bench as bench


def _tiny():
    return ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=5),
                AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=1)],
        total_steps=40)


def test_bench_engine_structure():
    row = bench.bench_engine("tiny", _tiny())
    assert row["steps"] == 40 and row["agents"] == 6
    assert row["steps_per_sec"] > 0 and row["events_per_sec"] > 0
    assert set(row) >= {"scenario", "seconds", "trades", "events"}


def test_bench_checkpoint_structure():
    row = bench.bench_checkpoint(_tiny())
    assert row["size_mb"] > 0
    assert row["capture_ms"] >= 0 and row["restore_ms"] >= 0


def test_environment_metadata():
    env = bench.environment()
    assert env["python"] and env["cpu_count"] >= 1
    assert "tezcat_version" in env and "git_commit" in env
