"""Batch execution of experiment versions (Phase F5).

Runs every (cell, replication) of a registered experiment version's design
matrix with deterministic, order-independent seeds. Execution is:

- **content-addressed**: one canonical batch per experiment version
  (``bat_<research_hash[:12]>``), so re-running is always a resume;
- **resumable/idempotent**: each completed run persists a seed-level result
  row keyed by its position in the design matrix; existing rows are never
  re-executed;
- **budgeted**: an optional per-call ``max_runs`` cap stops early with an
  explicit ``partial`` status — never a silent truncation;
- **provenance-complete**: every row records seed, config hash, state hash,
  event hash, and event-log chain, so any row can be reproduced and audited.

The serial path is the authoritative reference. ``workers > 1`` (Phase F11)
fans independent replications out to worker *processes* through
``execute_run`` — the exact same function the serial path uses — with a
single writer in the parent process. Seeds are derived per (cell,
replication) by the versioned allocator, so results are identical for any
worker count and any completion order (tested for workers 1/4/8).
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.registry import Registry
from tezcat.experiments.schema import ExperimentVersion

log = logging.getLogger("tezcat.batch")


def batch_id_for(version: ExperimentVersion) -> str:
    return f"bat_{version.research_hash[:12]}"


def run_key(cell_index: int, replication: int) -> str:
    """Filesystem-safe, order-stable key for one planned run."""
    return f"c{cell_index:03d}_r{replication:04d}"


class MetricMissing(KeyError):
    """A declared dependent variable is absent from the run report."""


def execute_run(version: ExperimentVersion, batch_id: str,
                item: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one planned run and build its result row.

    The single source of truth for run execution — used verbatim by the
    serial loop, the process-pool workers, and (via re-execution) by
    ``tezcat reproduce``.
    """
    cell_name = item["cell"]
    config = version.cell_config(cell_name)
    seed = version.seed_for(cell_name, item["replication"])
    run_id = f"{batch_id}_{item['key']}"

    engine = EcologyEngine(run_id, config, seed)
    while not engine.done:
        engine.step()
    engine.check_invariants()
    report = engine.build_report()

    metrics: Dict[str, Any] = {}
    for dv in version.design.dependent_variables:
        if dv not in report:
            raise MetricMissing(
                f"dependent variable {dv!r} not found in run report "
                f"(available: {sorted(k for k in report if not isinstance(report[k], dict))})")
        metrics[dv] = report[dv]

    return {
        "batch_id": batch_id,
        "version_id": version.version_id,
        "key": item["key"],
        "cell": cell_name,
        "replication": item["replication"],
        "run_id": run_id,
        "seed": seed,
        "config_hash": version.cell_configs[item["cell_index"]]["config_hash"],
        "metrics": metrics,
        "steps": engine.step_num,
        "n_trades": len(engine.trades),
        "state_hash": engine.state_hash(),
        "event_hash": engine.event_hash(),
        "event_log_chain": engine.events_log.chain,
    }


def _pool_worker(version_record: Dict[str, Any], batch_id: str,
                 item: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level (picklable) worker: rebuild the version and execute.

    ``from_dict`` re-verifies the research hash inside the worker, so a
    worker on drifted code fails loudly instead of producing subtly
    different rows.
    """
    version = ExperimentVersion.from_dict(version_record)
    return execute_run(version, batch_id, item)


class BatchRunner:
    def __init__(self, registry: Registry):
        self.registry = registry

    # ------------------------------------------------------------------
    def run(self, version_id: str, max_runs: Optional[int] = None,
            workers: int = 1) -> Dict[str, Any]:
        """Execute (or resume) the canonical batch for an experiment version.

        Returns the batch record. ``max_runs`` caps executions *this call*;
        the batch resumes from persisted rows on the next call.
        ``workers > 1`` runs pending replications in worker processes with a
        single writer here; a failed worker leaves its key pending (explicit
        in ``failed_keys``) and the batch stays resumable.
        """
        version = self.registry.load(version_id)  # re-verifies research hash
        bid = batch_id_for(version)
        design = version.design

        plan: List[Dict[str, Any]] = []
        for ci, cell in enumerate(version.cell_configs):
            for r in range(design.replications):
                plan.append({"key": run_key(ci, r), "cell": cell["cell"],
                             "cell_index": ci, "replication": r})

        completed_keys = [p["key"] for p in plan
                          if self.registry.load_result_row(bid, p["key"]) is not None]
        pending = [p for p in plan if p["key"] not in set(completed_keys)]
        if max_runs is not None:
            pending = pending[:max_runs]

        executed = 0
        failed_keys: List[str] = []
        if workers <= 1:
            for item in pending:
                row = execute_run(version, bid, item)
                self.registry.save_result_row(bid, item["key"], row)
                completed_keys.append(item["key"])
                executed += 1
        elif pending:
            record = version.to_dict()
            with ProcessPoolExecutor(
                    max_workers=min(workers, len(pending))) as pool:
                futures = {pool.submit(_pool_worker, record, bid, item): item
                           for item in pending}
                for fut in as_completed(futures):
                    item = futures[fut]
                    try:
                        row = fut.result()
                    except Exception as exc:  # noqa: BLE001 — worker died
                        log.error("worker failed on %s: %s", item["key"], exc)
                        failed_keys.append(item["key"])
                        continue
                    # Single writer; idempotent by key (content-identical
                    # rows if a duplicate ever slips through).
                    self.registry.save_result_row(bid, item["key"], row)
                    completed_keys.append(item["key"])
                    executed += 1

        missing = [p["key"] for p in plan if p["key"] not in set(completed_keys)]
        if missing:
            log.warning("batch %s incomplete: %d/%d runs complete, %d pending "
                        "(%d worker failures)", bid, len(completed_keys),
                        len(plan), len(missing), len(failed_keys))
        batch = {
            "batch_id": bid,
            "version_id": version.version_id,
            "research_hash": version.research_hash,
            "status": "completed" if not missing else "partial",
            "planned": len(plan),
            "completed": len(completed_keys),
            "executed_this_call": executed,
            "workers": workers,
            "pending_keys": missing,
            "failed_keys": failed_keys,
        }
        self.registry.save_batch(batch)
        return batch
