"""Phase S2: TradeOps — queue lifecycle, guardrails, retries, consistency."""

import threading
import time

import pytest

from tezcat.experiments.batch import batch_id_for
from tezcat.experiments.registry import Registry
from tezcat.experiments.scenarios import version_from_spec
from tezcat.ops import OpsError, OpsLimits, OpsManager


def _spec(steps=60, replications=4, seed=42, name="ops-market"):
    return {
        "experiment_id": "exp_ops", "name": name, "root_seed": seed,
        "config": {
            "agents": [
                {"agent_type": "noise_trader", "count": 6},
                {"agent_type": "market_maker", "count": 2},
            ],
            "total_steps": steps,
        },
        "design": {
            "design_type": "baseline",
            "question": "Does the ops layer run this?",
            "hypothesis": "TradeOps executes without touching semantics.",
            "dependent_variables": ["max_drawdown", "total_trades"],
            "primary_metric": "max_drawdown",
            "replications": replications,
        },
    }


def _register(reg, **kw):
    return reg.register(version_from_spec(_spec(**kw)))


def _wait(mgr, job_id, states=("COMPLETED", "FAILED", "CANCELLED"), timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mgr.job(job_id)
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job stuck in {mgr.job(job_id)['state']}")


# ---------------------------------------------------------------------------
# Queue lifecycle
# ---------------------------------------------------------------------------
def test_job_lifecycle_completes(tmp_path):
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, chunk=2, data_dir=str(tmp_path))
    vid = _register(reg)
    job = mgr.submit(vid)
    assert job["state"] == "QUEUED" and job["planned"] == 4

    done = _wait(mgr, job["job_id"])
    assert done["state"] == "COMPLETED"
    assert done["completed"] == 4
    assert done["worker_id"] is not None
    assert len(reg.list_result_rows(done["batch_id"])) == 4
    # Job record persisted.
    assert (mgr.jobs_dir / f"{job['job_id']}.json").exists()

    s = mgr.status()
    assert s["completed"] == 1 and s["queued"] == 0 and s["running"] == 0


def test_ops_execution_matches_direct_batch_runner(tmp_path):
    """TradeOps must not alter scientific results: rows equal the direct
    BatchRunner reference byte-for-byte."""
    from tezcat.experiments.batch import BatchRunner
    reg_ops = Registry(str(tmp_path / "ops"))
    mgr = OpsManager(reg_ops, workers=1, chunk=3, data_dir=str(tmp_path / "ops"))
    vid = _register(reg_ops)
    _wait(mgr, mgr.submit(vid)["job_id"])

    reg_ref = Registry(str(tmp_path / "ref"))
    vref = _register(reg_ref)
    BatchRunner(reg_ref).run(vref)
    assert (reg_ops.list_result_rows(batch_id_for(reg_ops.load(vid)))
            == reg_ref.list_result_rows(batch_id_for(reg_ref.load(vref))))


# ---------------------------------------------------------------------------
# Duplicate guard and guardrails
# ---------------------------------------------------------------------------
def test_duplicate_execution_guard(tmp_path):
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, chunk=1,
                     data_dir=str(tmp_path))
    vid = _register(reg, replications=6)
    first = mgr.submit(vid)
    with pytest.raises(OpsError) as exc:
        mgr.submit(vid)
    assert exc.value.code == "duplicate_execution"
    assert exc.value.detail["job_id"] == first["job_id"]
    _wait(mgr, first["job_id"])
    # After completion, resubmission is allowed (it would resume/no-op).
    assert mgr.submit(vid)["state"] == "QUEUED"


def test_guardrails_reject_oversized_submissions(tmp_path):
    reg = Registry(str(tmp_path))
    limits = OpsLimits(max_replications=5, max_steps=100, max_planned_runs=5,
                       max_queue=10)
    mgr = OpsManager(reg, limits=limits, workers=1, data_dir=str(tmp_path))

    too_many_reps = _register(reg, replications=6, name="reps")
    with pytest.raises(OpsError) as e1:
        mgr.submit(too_many_reps)
    assert e1.value.code == "limit_replications"

    too_many_steps = _register(reg, steps=200, name="steps")
    with pytest.raises(OpsError) as e2:
        mgr.submit(too_many_steps)
    assert e2.value.code == "limit_steps"


def test_queue_capacity_limit(tmp_path):
    reg = Registry(str(tmp_path))
    limits = OpsLimits(max_queue=2)
    # One worker held by a long job; further submissions fill the queue.
    mgr = OpsManager(reg, limits=limits, workers=1, chunk=1,
                     data_dir=str(tmp_path))
    vids = [_register(reg, seed=i, replications=30, steps=150, name=f"q{i}")
            for i in range(4)]
    holder = mgr.submit(vids[0])
    deadline = time.time() + 30
    while mgr.job(holder["job_id"])["state"] != "RUNNING":
        assert time.time() < deadline
        time.sleep(0.02)
    mgr.submit(vids[1])           # queued
    mgr.submit(vids[2])           # queued (queue now full)
    with pytest.raises(OpsError) as exc:
        mgr.submit(vids[3])
    assert exc.value.code == "limit_queue"
    # Drain: cancel everything so worker threads quiesce.
    for j in mgr.jobs():
        if j["state"] in ("QUEUED", "RUNNING"):
            mgr.cancel(j["job_id"])


def test_concurrent_submissions_one_winner(tmp_path):
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, chunk=1, data_dir=str(tmp_path))
    vid = _register(reg, replications=6)
    results, errors = [], []

    def submit():
        try:
            results.append(mgr.submit(vid))
        except OpsError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1                      # exactly one accepted
    assert all(e.code == "duplicate_execution" for e in errors)
    _wait(mgr, results[0]["job_id"])


# ---------------------------------------------------------------------------
# Failure, retry, cancellation
# ---------------------------------------------------------------------------
def _failing_version(reg):
    spec = _spec(name="will-fail")
    spec["design"]["dependent_variables"] = ["not_a_metric"]
    spec["design"]["primary_metric"] = "not_a_metric"
    return reg.register(version_from_spec(spec))


def test_failure_surfaced_and_not_retryable_to_success(tmp_path):
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, data_dir=str(tmp_path))
    vid = _failing_version(reg)
    job = _wait(mgr, mgr.submit(vid)["job_id"])
    assert job["state"] == "FAILED"
    assert "not_a_metric" in job["error"]         # exact reason, never generic


def test_retry_preserves_seed_identity_and_resumes(tmp_path):
    """A storage fault mid-batch → FAILED with persisted rows → retry
    resumes and the final rows equal an unfaulted reference (same seeds,
    same hashes: retries never mint new scientific identities)."""
    from tezcat.experiments.batch import BatchRunner

    class FlakyRegistry(Registry):
        def __init__(self, root):
            super().__init__(root)
            self.tripped = False

        def save_result_row(self, batch_id, key, row):
            if key == "c000_r0002" and not self.tripped:
                self.tripped = True
                raise OSError("injected storage failure")
            super().save_result_row(batch_id, key, row)

    flaky = FlakyRegistry(str(tmp_path))
    mgr = OpsManager(flaky, workers=1, chunk=10, data_dir=str(tmp_path))
    vid = _register(flaky)
    job = _wait(mgr, mgr.submit(vid)["job_id"])
    assert job["state"] == "FAILED"
    assert "injected storage failure" in job["error"]
    assert 0 < job["completed"] < 4               # partial rows persisted

    retried = mgr.retry(job["job_id"])
    assert retried["attempts"] == 1
    done = _wait(mgr, job["job_id"])
    assert done["state"] == "COMPLETED" and done["completed"] == 4

    ref = Registry(str(tmp_path / "ref"))
    vref = _register(ref)
    BatchRunner(ref).run(vref)
    assert (flaky.list_result_rows(job["batch_id"])
            == ref.list_result_rows(batch_id_for(ref.load(vref))))


def test_cancel_queued_and_running(tmp_path):
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, chunk=1, data_dir=str(tmp_path))
    running_vid = _register(reg, replications=30, steps=100, name="long")
    queued_vid = _register(reg, replications=4, name="waiting")
    running = mgr.submit(running_vid)
    queued = mgr.submit(queued_vid)

    # Cancel while queued: immediate.
    assert mgr.cancel(queued["job_id"])["state"] == "CANCELLED"
    # Cancel while running: honored at the next chunk boundary.
    deadline = time.time() + 30
    while mgr.job(running["job_id"])["state"] != "RUNNING":
        assert time.time() < deadline
        time.sleep(0.02)
    mgr.cancel(running["job_id"])
    done = _wait(mgr, running["job_id"])
    assert done["state"] == "CANCELLED"
    assert done["completed"] < 30                 # stopped early
    # Cancelled jobs are retryable and resume from persisted rows.
    mgr.retry(running["job_id"])
    final = _wait(mgr, running["job_id"])
    assert final["state"] == "COMPLETED" and final["completed"] == 30

    with pytest.raises(OpsError) as exc:
        mgr.cancel(running["job_id"])
    assert exc.value.code == "not_cancellable"


def test_restart_marks_orphaned_jobs_failed(tmp_path):
    """A process restart must surface interrupted jobs, never hide them."""
    reg = Registry(str(tmp_path))
    mgr = OpsManager(reg, workers=1, chunk=1, data_dir=str(tmp_path))
    vid = _register(reg, replications=30, steps=100)
    job = mgr.submit(vid)
    deadline = time.time() + 30
    while mgr.job(job["job_id"])["state"] != "RUNNING":
        assert time.time() < deadline
        time.sleep(0.02)

    # Simulate a restart: a fresh manager over the same data dir.
    mgr2 = OpsManager(reg, workers=1, data_dir=str(tmp_path))
    revived = mgr2.job(job["job_id"])
    assert revived["state"] == "FAILED"
    assert "restarted" in revived["error"]
    # And it is retryable in the new process.
    mgr2.retry(job["job_id"])
    done = _wait(mgr2, job["job_id"], timeout=90)
    assert done["state"] == "COMPLETED"
    # Quiesce the first manager's worker before teardown.
    mgr.cancel(job["job_id"]) if mgr.job(job["job_id"])["state"] == "RUNNING" else None
    time.sleep(0.1)
