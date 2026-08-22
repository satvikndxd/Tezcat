"""TradeOps: the operational layer around experiment execution (Phase S2).

    Research asks the question. TradeOps runs the laboratory.

TradeOps owns queues, workers, failures, retries, and capacity — and owns
NOTHING scientific. Execution goes through the existing ``BatchRunner`` in
budgeted chunks, so every S1 guarantee is inherited untouched:

- deterministic per-(cell, replication) seeds (a retry re-derives the same
  seed — a failed replication can never silently acquire a new identity);
- content-addressed batches (re-running is always a resume; completed rows
  are never re-executed);
- explicit partial/failed accounting (never a generic "something went
  wrong").

Job state machine (one, explicit)::

    QUEUED → RUNNING → COMPLETED
                     → FAILED     (retryable; resumes from persisted rows)
                     → CANCELLED  (queued: immediate; running: at the next
                                   chunk boundary)

Guardrails (`OpsLimits`, environment-configurable) act at the *submission
boundary* only — they are operator policy for shared deployments and never
modify experiment semantics.

The queue is in-process; job records are persisted as JSON so history
survives restarts (a restarted process lists prior jobs as historical
records; re-submitting resumes their batches from persisted rows).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tezcat.experiments.batch import BatchRunner, batch_id_for
from tezcat.experiments.registry import Registry

log = logging.getLogger("tezcat.ops")

QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = (
    "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
ACTIVE_STATES = (QUEUED, RUNNING)


class OpsError(ValueError):
    def __init__(self, message: str, code: str = "ops_error",
                 detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


class OpsLimits:
    """Operator policy from environment variables (documented in
    docs/docker.md). Defaults suit a small public demo."""

    def __init__(self, max_queue: int = 20, max_active: int = 4,
                 max_replications: int = 200, max_steps: int = 5000,
                 max_planned_runs: int = 400):
        self.max_queue = max_queue
        self.max_active = max_active
        self.max_replications = max_replications
        self.max_steps = max_steps
        self.max_planned_runs = max_planned_runs

    @classmethod
    def from_env(cls) -> "OpsLimits":
        def get(name, default):
            try:
                return int(os.environ.get(name, default))
            except ValueError:
                return default
        return cls(
            max_queue=get("TEZCAT_MAX_QUEUE", 20),
            max_active=get("TEZCAT_MAX_ACTIVE", 4),
            max_replications=get("TEZCAT_MAX_REPLICATIONS", 200),
            max_steps=get("TEZCAT_MAX_STEPS", 5000),
            max_planned_runs=get("TEZCAT_MAX_PLANNED_RUNS", 400),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"max_queue": self.max_queue, "max_active": self.max_active,
                "max_replications": self.max_replications,
                "max_steps": self.max_steps,
                "max_planned_runs": self.max_planned_runs}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class OpsManager:
    def __init__(self, registry: Registry, limits: Optional[OpsLimits] = None,
                 workers: int = 2, chunk: int = 5, data_dir: str = "data"):
        self.registry = registry
        self.limits = limits or OpsLimits()
        self.chunk = max(1, chunk)
        self.jobs_dir = Path(data_dir) / "ops" / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._load_history()
        self._workers: List[Dict[str, Any]] = []
        for i in range(max(1, workers)):
            state = {"worker_id": f"worker-{i + 1:02d}", "state": "idle",
                     "job_id": None, "started_at": None}
            self._workers.append(state)
            threading.Thread(target=self._worker_loop, args=(state,),
                             daemon=True, name=state["worker_id"]).start()

    # -- persistence ----------------------------------------------------
    def _persist(self, job: Dict[str, Any]) -> None:
        path = self.jobs_dir / f"{job['job_id']}.json"
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(job, indent=1))
        os.replace(tmp, path)

    def _load_history(self) -> None:
        for p in self.jobs_dir.glob("job_*.json"):
            try:
                job = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if job.get("state") in ACTIVE_STATES:
                # A restart orphaned this job: mark it explicitly, never
                # silently. Its batch rows persist; retry resumes them.
                job["state"] = FAILED
                job["error"] = "process restarted while job was active"
                self._persist(job)
            self._jobs[job["job_id"]] = job

    # -- submission boundary (guardrails + duplicate guard) --------------
    def submit(self, version_id: str) -> Dict[str, Any]:
        version = self.registry.load(version_id)  # verifies research hash
        design = version.design

        if design.replications > self.limits.max_replications:
            raise OpsError(
                f"replications {design.replications} exceed the operator "
                f"limit {self.limits.max_replications}", "limit_replications")
        if version.config.total_steps > self.limits.max_steps:
            raise OpsError(
                f"total_steps {version.config.total_steps} exceed the "
                f"operator limit {self.limits.max_steps}", "limit_steps")
        if design.planned_runs() > self.limits.max_planned_runs:
            raise OpsError(
                f"planned runs {design.planned_runs()} exceed the operator "
                f"limit {self.limits.max_planned_runs}", "limit_planned_runs")

        with self._lock:
            active = [j for j in self._jobs.values()
                      if j["state"] in ACTIVE_STATES]
            dup = next((j for j in active
                        if j["version_id"] == version.version_id), None)
            if dup is not None:
                raise OpsError(
                    f"experiment {version.version_id} is already "
                    f"{dup['state'].lower()} as job {dup['job_id']}",
                    "duplicate_execution", detail=dup)
            if len([j for j in active if j["state"] == QUEUED]) >= self.limits.max_queue:
                raise OpsError(f"queue is full "
                               f"(max {self.limits.max_queue})", "limit_queue")
            if len(active) >= self.limits.max_queue + self.limits.max_active:
                raise OpsError("system at capacity", "limit_capacity")

            job = {
                "job_id": f"job_{uuid.uuid4().hex[:8]}",
                "version_id": version.version_id,
                "research_hash": version.research_hash,
                "batch_id": batch_id_for(version),
                "name": version.name,
                "state": QUEUED,
                "planned": design.planned_runs(),
                "completed": 0,
                "attempts": 0,
                "submitted_at": _now(),
                "started_at": None,
                "finished_at": None,
                "worker_id": None,
                "error": None,
            }
            self._jobs[job["job_id"]] = job
            self._cancel_events[job["job_id"]] = threading.Event()
            self._persist(job)
        self._queue.put(job["job_id"])
        return dict(job)

    # -- control ---------------------------------------------------------
    def cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            if job["state"] == QUEUED:
                job["state"] = CANCELLED
                job["finished_at"] = _now()
                self._persist(job)
            elif job["state"] == RUNNING:
                # Cooperative: honored at the next chunk boundary.
                self._cancel_events[job_id].set()
            else:
                raise OpsError(f"job {job_id} is {job['state']}; "
                               "nothing to cancel", "not_cancellable")
            return dict(job)

    def retry(self, job_id: str) -> Dict[str, Any]:
        """Re-enqueue a failed/cancelled job. The batch resumes from its
        persisted rows; seeds are re-derived by the versioned allocator, so
        a retried replication keeps its exact scientific identity."""
        with self._lock:
            job = self._require(job_id)
            if job["state"] not in (FAILED, CANCELLED):
                raise OpsError(f"job {job_id} is {job['state']}; only "
                               "FAILED/CANCELLED jobs can be retried",
                               "not_retryable")
            job["state"] = QUEUED
            job["error"] = None
            job["finished_at"] = None
            job["attempts"] += 1
            self._cancel_events[job_id] = threading.Event()
            self._persist(job)
        self._queue.put(job_id)
        return dict(job)

    # -- worker loop ------------------------------------------------------
    def _worker_loop(self, worker: Dict[str, Any]) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job["state"] != QUEUED:
                    continue  # cancelled while queued, or stale
                job["state"] = RUNNING
                job["started_at"] = _now()
                job["worker_id"] = worker["worker_id"]
                worker.update(state="busy", job_id=job_id, started_at=job["started_at"])
                self._persist(job)
            cancel = self._cancel_events[job_id]
            try:
                while True:
                    if cancel.is_set():
                        self._finish(job_id, worker, CANCELLED)
                        break
                    batch = BatchRunner(self.registry).run(
                        job["version_id"], max_runs=self.chunk)
                    with self._lock:
                        job["completed"] = batch["completed"]
                        self._persist(job)
                    if batch["failed_keys"]:
                        self._finish(job_id, worker, FAILED,
                                     error=f"{len(batch['failed_keys'])} "
                                           f"replication(s) failed: "
                                           f"{batch['failed_keys']}")
                        break
                    if batch["status"] == "completed":
                        self._finish(job_id, worker, COMPLETED)
                        break
            except Exception as exc:  # noqa: BLE001 — surface, never hide
                log.exception("job %s failed", job_id)
                self._finish(job_id, worker, FAILED, error=str(exc))

    def _finish(self, job_id: str, worker: Dict[str, Any], state: str,
                error: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["state"] = state
            job["error"] = error
            job["finished_at"] = _now()
            rows = self.registry.list_result_rows(job["batch_id"])
            job["completed"] = len(rows)
            worker.update(state="idle", job_id=None, started_at=None)
            self._persist(job)

    # -- views ------------------------------------------------------------
    def _require(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise OpsError(f"unknown job {job_id!r}", "unknown_job")
        return job

    def job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._require(job_id))

    def jobs(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            out = [dict(j) for j in self._jobs.values()
                   if state is None or j["state"] == state]
        return sorted(out, key=lambda j: j["submitted_at"], reverse=True)

    def workers(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for w in self._workers:
                row = dict(w)
                if w["job_id"]:
                    job = self._jobs.get(w["job_id"], {})
                    row["progress"] = {"completed": job.get("completed", 0),
                                       "planned": job.get("planned", 0)}
                out.append(row)
            return out

    def status(self) -> Dict[str, Any]:
        with self._lock:
            by_state: Dict[str, int] = {}
            for j in self._jobs.values():
                by_state[j["state"]] = by_state.get(j["state"], 0) + 1
            busy = sum(1 for w in self._workers if w["state"] == "busy")
            return {
                "workers_total": len(self._workers),
                "workers_busy": busy,
                "capacity_pct": round(100 * busy / len(self._workers)),
                "queued": by_state.get(QUEUED, 0),
                "running": by_state.get(RUNNING, 0),
                "completed": by_state.get(COMPLETED, 0),
                "failed": by_state.get(FAILED, 0),
                "cancelled": by_state.get(CANCELLED, 0),
                "limits": self.limits.to_dict(),
                "chunk_size": self.chunk,
            }
