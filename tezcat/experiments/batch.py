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

Serial by design: a trusted serial reference must exist before any
distributed execution (spec F11).
"""

from __future__ import annotations

import logging
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


class BatchRunner:
    def __init__(self, registry: Registry):
        self.registry = registry

    # ------------------------------------------------------------------
    def run(self, version_id: str, max_runs: Optional[int] = None) -> Dict[str, Any]:
        """Execute (or resume) the canonical batch for an experiment version.

        Returns the batch record. ``max_runs`` caps executions *this call*;
        the batch resumes from persisted rows on the next call.
        """
        version = self.registry.load(version_id)  # re-verifies research hash
        bid = batch_id_for(version)
        design = version.design

        plan: List[Dict[str, Any]] = []
        for ci, cell in enumerate(version.cell_configs):
            for r in range(design.replications):
                plan.append({"key": run_key(ci, r), "cell": cell["cell"],
                             "cell_index": ci, "replication": r})

        executed = 0
        completed_keys = []
        for item in plan:
            existing = self.registry.load_result_row(bid, item["key"])
            if existing is not None:
                completed_keys.append(item["key"])
                continue
            if max_runs is not None and executed >= max_runs:
                continue
            row = self._execute(version, bid, item)
            self.registry.save_result_row(bid, item["key"], row)
            completed_keys.append(item["key"])
            executed += 1

        missing = [p["key"] for p in plan if p["key"] not in set(completed_keys)]
        if missing:
            log.warning("batch %s stopped at budget: %d/%d runs complete, "
                        "%d pending", bid, len(completed_keys), len(plan),
                        len(missing))
        batch = {
            "batch_id": bid,
            "version_id": version.version_id,
            "research_hash": version.research_hash,
            "status": "completed" if not missing else "partial",
            "planned": len(plan),
            "completed": len(completed_keys),
            "executed_this_call": executed,
            "pending_keys": missing,
        }
        self.registry.save_batch(batch)
        return batch

    # ------------------------------------------------------------------
    def _execute(self, version: ExperimentVersion, batch_id: str,
                 item: Dict[str, Any]) -> Dict[str, Any]:
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
