"""Phase F11: parallel worker equivalence and fault injection.

The spec's core requirements:
- workers 1/4/8 produce identical replication semantics (byte-for-byte rows)
- jobs are idempotent, resumable, and deduplicated
- storage faults leave the batch resumable, never corrupt
"""

import json
import threading

import pytest

from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.experiments.batch import BatchRunner, batch_id_for
from tezcat.experiments.registry import Registry
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion


def _version(replications=6):
    return ExperimentVersion("exp_par", "parallel-ab", ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5,
                             params={"herding": 0.5}),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        total_steps=60), DesignSpec(
        design_type="ab",
        question="Does herding change drawdown?",
        hypothesis="Higher herding raises drawdown.",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown", "total_trades"],
        primary_metric="max_drawdown",
        control=Arm(name="low", overrides={"agents.1.params.herding": 0.1}),
        treatments=[Arm(name="high", overrides={"agents.1.params.herding": 0.9})],
        replications=replications))


def _rows(reg, vid):
    return reg.list_result_rows(batch_id_for(reg.load(vid)))


# ---------------------------------------------------------------------------
# Worker-count equivalence (the F11 acceptance requirement)
# ---------------------------------------------------------------------------
def test_workers_1_4_8_produce_identical_rows(tmp_path):
    reference = None
    for workers in (1, 4, 8):
        reg = Registry(str(tmp_path / f"w{workers}"))
        vid = reg.register(_version())
        batch = BatchRunner(reg).run(vid, workers=workers)
        assert batch["status"] == "completed" and batch["completed"] == 12
        assert batch["workers"] == workers
        rows = _rows(reg, vid)
        if reference is None:
            reference = rows
        else:
            # Byte-for-byte: seeds, metrics, and every hash identical
            # regardless of worker count or completion order.
            assert rows == reference


def test_parallel_resume_after_partial_serial(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_version())
    BatchRunner(reg).run(vid, max_runs=5)             # serial partial
    batch = BatchRunner(reg).run(vid, workers=4)      # parallel resume
    assert batch["status"] == "completed"
    assert batch["executed_this_call"] == 7           # only the pending runs

    serial = Registry(str(tmp_path / "ref"))
    svid = serial.register(_version())
    BatchRunner(serial).run(svid)
    assert _rows(reg, vid) == _rows(serial, svid)     # equals serial reference


def test_parallel_budget_cap(tmp_path):
    reg = Registry(str(tmp_path))
    vid = reg.register(_version())
    batch = BatchRunner(reg).run(vid, max_runs=4, workers=4)
    assert batch["status"] == "partial"
    assert batch["completed"] == 4 and len(batch["pending_keys"]) == 8


# ---------------------------------------------------------------------------
# Duplicate concurrent runners (dedup requirement)
# ---------------------------------------------------------------------------
def test_concurrent_duplicate_runners_converge(tmp_path):
    """Two runners racing on the same batch: complete, deduplicated,
    identical to the serial reference (rows are content-identical, so the
    race is benign by construction)."""
    reg = Registry(str(tmp_path))
    vid = reg.register(_version())
    errors = []

    def work():
        try:
            BatchRunner(reg).run(vid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors

    rows = _rows(reg, vid)
    assert len(rows) == 12                            # no duplicates
    serial = Registry(str(tmp_path / "ref"))
    svid = serial.register(_version())
    BatchRunner(serial).run(svid)
    assert rows == _rows(serial, svid)


# ---------------------------------------------------------------------------
# Storage fault injection
# ---------------------------------------------------------------------------
class FlakyRegistry(Registry):
    """Registry whose row-persistence fails once at a chosen key."""

    def __init__(self, root, fail_key):
        super().__init__(root)
        self.fail_key = fail_key
        self.tripped = False

    def save_result_row(self, batch_id, key, row):
        if key == self.fail_key and not self.tripped:
            self.tripped = True
            raise OSError(f"injected storage failure at {key}")
        super().save_result_row(batch_id, key, row)


def test_storage_failure_leaves_batch_resumable(tmp_path):
    flaky = FlakyRegistry(str(tmp_path), fail_key="c000_r0003")
    vid = flaky.register(_version())
    with pytest.raises(OSError, match="injected storage failure"):
        BatchRunner(flaky).run(vid)                   # serial: fails loudly

    # Rows persisted before the fault survive; a healthy runner resumes.
    healthy = Registry(str(tmp_path))
    partial = len(_rows(healthy, vid))
    assert 0 < partial < 12
    batch = BatchRunner(healthy).run(vid)
    assert batch["status"] == "completed"
    assert batch["executed_this_call"] == 12 - partial

    serial = Registry(str(tmp_path / "ref"))
    svid = serial.register(_version())
    BatchRunner(serial).run(svid)
    assert _rows(healthy, vid) == _rows(serial, svid)


def test_atomic_writes_survive_garbage_tmp_files(tmp_path):
    """A crash mid-write leaves only a .tmp file; reads and subsequent
    writes are unaffected (tmp+rename atomicity)."""
    reg = Registry(str(tmp_path))
    vid = reg.register(_version(replications=2))
    bid = batch_id_for(reg.load(vid))
    BatchRunner(reg).run(vid)

    row_path = reg.root / "results" / bid / "c000_r0000.json"
    garbage = row_path.with_suffix(".tmp")
    garbage.write_text("{corrupted mid-write")
    # Reads ignore the tmp; the stored row is intact JSON.
    row = reg.load_result_row(bid, "c000_r0000")
    assert row is not None and row["key"] == "c000_r0000"
    json.loads(row_path.read_text())
    # A rewrite over a stale tmp file succeeds.
    reg.save_result_row(bid, "c000_r0000", row)
    assert reg.load_result_row(bid, "c000_r0000") == row


def test_worker_failure_reported_and_resumable(tmp_path):
    """A version whose report lacks a declared metric makes every worker
    fail: the batch must report failed keys explicitly and stay resumable,
    not hang or silently succeed."""
    reg = Registry(str(tmp_path))
    bad = ExperimentVersion("exp_bad", "bad-dv", ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=5)],
        total_steps=50), DesignSpec(
        design_type="baseline",
        question="Will this fail loudly?",
        hypothesis="Workers propagate MetricMissing as failures.",
        dependent_variables=["not_a_real_metric"],
        primary_metric="not_a_real_metric",
        replications=1))
    vid = reg.register(bad)
    batch = BatchRunner(reg).run(vid, workers=2)
    assert batch["status"] == "partial"
    assert batch["completed"] == 0
    assert batch["failed_keys"] == ["c000_r0000"]
    assert batch["pending_keys"] == ["c000_r0000"]    # still resumable
