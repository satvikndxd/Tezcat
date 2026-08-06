"""Run manager: executes runs on background threads with pause/resume/cancel."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
        self.store.save_artifact(prefix, "report/report.json", lr.report)
        self.store.save_artifact(prefix, "trades/trades.json", engine.trades)
        self.store.save_artifact(prefix, "snapshots/snapshots.json", engine.snapshots)
        self.store.save_artifact(prefix, "metrics/step_metrics.json", engine.metrics.step_metrics)
        self.store.save_artifact(prefix, "shocks/shock_events.json",
                                 [e.to_dict() for e in engine.shocks.events])
        self.store.save_artifact(prefix, "regimes/regime_events.json",
                                 [e.to_dict() for e in engine.regimes.events])

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
