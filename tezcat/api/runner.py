"""Run manager: executes runs on background threads with pause/resume/cancel."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tezcat import __version__
from tezcat.core.config import SCHEMA_VERSION, canonical_json
from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.model import Experiment, Run

log = logging.getLogger("tezcat.runner")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LiveRun:
    def __init__(self, run: Run, experiment: Experiment):
        self.run = run
        self.engine = EcologyEngine(run.run_id, experiment.config, run.seed)
        self.pause_event = threading.Event()  # set => paused
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.report: Optional[Dict[str, Any]] = None


class RunManager:
    def __init__(self, store):
        self.store = store
        self.live: Dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self, run: Run, experiment: Experiment) -> None:
        # Refuse to execute an experiment whose stored identity is stale.
        experiment.verify_hash()
        lr = LiveRun(run, experiment)
        with self._lock:
            self.live[run.run_id] = lr
        run.status = "running"
        run.started_at = _now()
        self.store.save_run(run.to_dict())
        lr.thread = threading.Thread(target=self._execute, args=(lr,), daemon=True,
                                     name=f"run-{run.run_id}")
        lr.thread.start()

    # ------------------------------------------------------------------
    def _execute(self, lr: LiveRun) -> None:
        run, engine = lr.run, lr.engine
        delay = max(0.0, run.step_delay_ms / 1000.0)
        last_persist = 0.0
        try:
            while not engine.done:
                if lr.cancel_event.is_set():
                    run.status = "cancelled"
                    break
                if lr.pause_event.is_set():
                    time.sleep(0.1)
                    continue
                with lr.lock:
                    engine.step()
                run.steps_completed = engine.step_num
                run.current_regime = engine.regimes.current.value
                now = time.time()
                if now - last_persist > 2.0:
                    self.store.save_run(run.to_dict())
                    last_persist = now
                if delay:
                    time.sleep(delay)
            if engine.done:
                engine.check_invariants()
                lr.report = engine.build_report()
                # Provenance (Phase F2): every published number must be
                # traceable to config, code, seed, and reproducible hashes.
                lr.report["provenance"] = {
                    "schema_version": SCHEMA_VERSION,
                    "code_version": __version__,
                    "config_hash": run.config_hash,
                    "seed": run.seed,
                    "state_hash": engine.state_hash(),
                    "event_hash": engine.event_hash(),
                    "event_log_chain": engine.events_log.chain,
                }
                run.status = "completed"
                run.completed_at = _now()
                self._persist_artifacts(lr)
                log.info("run %s completed: %s steps, %s trades",
                         run.run_id, engine.step_num, len(engine.trades))
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s failed", run.run_id)
            run.status = "failed"
            run.error = str(exc)
            run.completed_at = _now()
        finally:
            self.store.save_run(run.to_dict())

    # ------------------------------------------------------------------
    def _persist_artifacts(self, lr: LiveRun) -> None:
        run, engine = lr.run, lr.engine
        prefix = run.s3_prefix or f"runs/{run.run_id}"
        artifacts = {
            "report/report.json": lr.report,
            "events/events.json": {
                "event_schema_version": 1,
                "chain_hash": engine.events_log.chain,
                "count": len(engine.events_log),
                "events": engine.events_log.events,
            },
            "trades/trades.json": engine.trades,
            "snapshots/snapshots.json": engine.snapshots,
            "metrics/step_metrics.json": engine.metrics.step_metrics,
            "shocks/shock_events.json": [e.to_dict() for e in engine.shocks.events],
            "regimes/regime_events.json": [e.to_dict() for e in engine.regimes.events],
        }
        checksums = {}
        for name, payload in artifacts.items():
            self.store.save_artifact(prefix, name, payload)
            checksums[name] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        # Manifest last, so its presence implies the artifacts it names exist.
        self.store.save_artifact(prefix, "manifest.json", {
            "run_id": run.run_id,
            "experiment_id": run.experiment_id,
            "config_hash": run.config_hash,
            "seed": run.seed,
            "schema_version": SCHEMA_VERSION,
            "code_version": __version__,
            "state_hash": engine.state_hash(),
            "event_hash": engine.event_hash(),
            "event_log_chain": engine.events_log.chain,
            "artifact_checksums": checksums,
        })

    # ------------------------------------------------------------------
    def get(self, run_id: str) -> Optional[LiveRun]:
        return self.live.get(run_id)

    def pause(self, run_id: str) -> Optional[Run]:
        lr = self.live.get(run_id)
        if lr is None or lr.run.status not in ("running",):
            return None
        lr.pause_event.set()
        lr.run.status = "paused"
        self.store.save_run(lr.run.to_dict())
        return lr.run

    def resume(self, run_id: str) -> Optional[Run]:
        lr = self.live.get(run_id)
        if lr is None or lr.run.status != "paused":
            return None
        lr.pause_event.clear()
        lr.run.status = "running"
        self.store.save_run(lr.run.to_dict())
        return lr.run

    def cancel(self, run_id: str) -> Optional[Run]:
        lr = self.live.get(run_id)
        if lr is None or lr.run.status not in ("running", "paused"):
            return None
        lr.pause_event.clear()
        lr.cancel_event.set()
        return lr.run

    def inject_shock(self, run_id: str, shock_type: str, side: Optional[str],
                     magnitude: float, duration: Optional[int]) -> bool:
        lr = self.live.get(run_id)
        if lr is None or lr.run.status not in ("running", "paused"):
            return False
        with lr.lock:
            lr.engine.shocks.inject_manual(shock_type, side, magnitude, duration)
        return True
