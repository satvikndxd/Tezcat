"""Tezcat REST API (FastAPI).

Serves the JSON API under /api and the built React dashboard at /.
"""

from __future__ import annotations

import logging
import os
import random as _random
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from tezcat import __version__
from tezcat.core.config import ExperimentConfig
from tezcat.experiments.model import ConfigHashMismatch, Experiment, Run
from tezcat.experiments.presets import build_config, get_preset, list_presets
from tezcat.persistence.aws import get_store
from tezcat.api.runner import RunManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("tezcat.api")

store = get_store()
runner = RunManager(store)

app = FastAPI(title="Tezcat", version=__version__,
              description="Agent-based market ecology laboratory")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class CreateExperimentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    hypothesis: Optional[str] = None
    config: Dict[str, Any]


class PresetExperimentBody(BaseModel):
    name: Optional[str] = None
    overrides: Optional[Dict[str, Any]] = None


class CreateRunBody(BaseModel):
    seed: Optional[int] = Field(None, ge=0)
    step_delay_ms: Optional[int] = Field(None, ge=0, le=1000)


class InjectShockBody(BaseModel):
    shock_type: str
    side: Optional[str] = None
    magnitude: float = Field(..., gt=0)
    duration: Optional[int] = Field(None, ge=1, le=2000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_experiment(experiment_id: str) -> Experiment:
    d = store.load_experiment(experiment_id)
    if d is None:
        raise HTTPException(404, "experiment not found")
    return Experiment.from_dict(d)


def _load_run(run_id: str) -> Run:
    lr = runner.get(run_id)
    if lr is not None:
        return lr.run
    d = store.load_run(run_id)
    if d is None:
        raise HTTPException(404, "run not found")
    return Run.from_dict(d)


def _run_data(run_id: str, kind: str) -> List[Dict[str, Any]]:
    """Fetch run series data from the live engine or from persisted artifacts."""
    lr = runner.get(run_id)
    if lr is not None:
        e = lr.engine
        return {
            "snapshots": e.snapshots,
            "trades": e.trades,
            "metrics": e.metrics.step_metrics,
            "shocks": [ev.to_dict() for ev in e.shocks.events],
            "regimes": [ev.to_dict() for ev in e.regimes.events],
        }[kind]
    run = _load_run(run_id)
    names = {
        "snapshots": "snapshots/snapshots.json",
        "trades": "trades/trades.json",
        "metrics": "metrics/step_metrics.json",
        "shocks": "shocks/shock_events.json",
        "regimes": "regimes/regime_events.json",
    }
    data = store.load_artifact(run.s3_prefix or f"runs/{run_id}", names[kind])
    return data or []


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
@app.get("/api/presets")
def api_list_presets():
    return list_presets()


@app.get("/api/presets/{preset_id}")
def api_get_preset(preset_id: str):
    p = get_preset(preset_id)
    if p is None:
        raise HTTPException(404, "preset not found")
    return p


@app.post("/api/presets/{preset_id}/experiments", status_code=201)
def api_create_from_preset(preset_id: str, body: PresetExperimentBody):
    p = get_preset(preset_id)
    if p is None:
        raise HTTPException(404, "preset not found")
    try:
        config = build_config(preset_id, body.overrides)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid overrides: {exc}") from exc
    exp = Experiment.create(body.name or p["name"], config, preset_id=preset_id)
    store.save_experiment(exp.to_dict())
    log.info("experiment %s created from preset %s", exp.experiment_id, preset_id)
    return exp.to_dict()


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
@app.post("/api/experiments", status_code=201)
def api_create_experiment(body: CreateExperimentBody):
    try:
        config = ExperimentConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid config: {exc}") from exc
    exp = Experiment.create(body.name, config, hypothesis=body.hypothesis)
    store.save_experiment(exp.to_dict())
    return exp.to_dict()


@app.get("/api/experiments")
def api_list_experiments():
    return store.list_experiments()


@app.get("/api/experiments/{experiment_id}")
def api_get_experiment(experiment_id: str):
    return _load_experiment(experiment_id).to_dict()


@app.get("/api/experiments/{experiment_id}/shocks")
def api_experiment_shocks(experiment_id: str):
    exp = _load_experiment(experiment_id)
    return [s.model_dump(mode="json") for s in exp.config.shocks]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@app.post("/api/experiments/{experiment_id}/runs", status_code=201)
def api_create_run(experiment_id: str, body: Optional[CreateRunBody] = None):
    body = body or CreateRunBody()
    exp = _load_experiment(experiment_id)
    if body.seed is not None:
        seed = body.seed
    elif exp.config.seed_policy == "fixed":
        seed = exp.config.default_seed
    else:
        seed = _random.SystemRandom().randrange(2**31)
    delay = body.step_delay_ms if body.step_delay_ms is not None else exp.config.step_delay_ms
    run = Run.create(exp, seed=seed, step_delay_ms=delay)
    try:
        runner.start(run, exp)
    except ConfigHashMismatch as exc:
        raise HTTPException(409, str(exc)) from exc
    log.info("run %s started for experiment %s (seed=%s)", run.run_id, experiment_id, seed)
    return run.to_dict()


@app.get("/api/runs")
def api_list_runs():
    live = {rid: lr.run.to_dict() for rid, lr in runner.live.items()}
    stored = {r["run_id"]: r for r in store.list_runs()}
    stored.update(live)
    out = list(stored.values())
    out.sort(key=lambda d: d.get("started_at") or "", reverse=True)
    return out


@app.get("/api/runs/{run_id}")
def api_get_run(run_id: str):
    return _load_run(run_id).to_dict()


@app.post("/api/runs/{run_id}/pause")
def api_pause(run_id: str):
    run = runner.pause(run_id)
    if run is None:
        raise HTTPException(409, "run is not running")
    return run.to_dict()


@app.post("/api/runs/{run_id}/resume")
def api_resume(run_id: str):
    run = runner.resume(run_id)
    if run is None:
        raise HTTPException(409, "run is not paused")
    return run.to_dict()


@app.post("/api/runs/{run_id}/cancel")
def api_cancel(run_id: str):
    run = runner.cancel(run_id)
    if run is None:
        raise HTTPException(409, "run is not active")
    return run.to_dict()


# ---------------------------------------------------------------------------
# Shocks / regimes
# ---------------------------------------------------------------------------
@app.post("/api/runs/{run_id}/shocks", status_code=202)
def api_inject_shock(run_id: str, body: InjectShockBody):
    if body.shock_type not in ("whale_order", "mm_withdrawal", "sentiment_shock"):
        raise HTTPException(422, f"unknown shock_type {body.shock_type!r}")
    ok = runner.inject_shock(run_id, body.shock_type, body.side, body.magnitude, body.duration)
    if not ok:
        raise HTTPException(409, "run is not active")
    return {"queued": True, "run_id": run_id, "shock_type": body.shock_type}


@app.get("/api/runs/{run_id}/shocks")
def api_run_shocks(run_id: str):
    return _run_data(run_id, "shocks")


@app.get("/api/runs/{run_id}/regimes")
def api_run_regimes(run_id: str):
    return _run_data(run_id, "regimes")


# ---------------------------------------------------------------------------
# Market / history / trades / metrics / report
# ---------------------------------------------------------------------------
@app.get("/api/runs/{run_id}/market")
def api_market(run_id: str):
    lr = runner.get(run_id)
    if lr is not None:
        snaps = lr.engine.snapshots
        book = lr.engine.book.level_snapshot(10)
        return {"snapshot": snaps[-1] if snaps else None, **book}
    snaps = _run_data(run_id, "snapshots")
    return {"snapshot": snaps[-1] if snaps else None, "bids": [], "asks": []}


@app.get("/api/runs/{run_id}/history")
def api_history(run_id: str, start: int = 0, limit: int = 5000):
    snaps = _run_data(run_id, "snapshots")
    chunk = snaps[start:start + limit]
    return {"snapshots": chunk, "next_start": start + len(chunk)}


@app.get("/api/runs/{run_id}/trades")
def api_trades(run_id: str, limit: int = 100):
    trades = _run_data(run_id, "trades")
    return {"trades": trades[-limit:]}


@app.get("/api/runs/{run_id}/metrics")
def api_metrics(run_id: str, start: int = 0, limit: int = 5000):
    metrics = _run_data(run_id, "metrics")
    chunk = metrics[start:start + limit]
    return {"metrics": chunk, "next_start": start + len(chunk)}


@app.get("/api/runs/{run_id}/report")
def api_report(run_id: str):
    lr = runner.get(run_id)
    if lr is not None and lr.report is not None:
        return lr.report
    run = _load_run(run_id)
    report = store.load_artifact(run.s3_prefix or f"runs/{run_id}", "report/report.json")
    if report is None:
        raise HTTPException(404, "report not available (run not completed)")
    return report


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.post("/api/runs/{run_id}/export")
def api_export(run_id: str):
    run = _load_run(run_id)
    if run.status != "completed":
        raise HTTPException(409, "run not completed")
    prefix = run.s3_prefix or f"runs/{run_id}"
    exp = store.load_experiment(run.experiment_id)
    export_id = f"exp_{uuid.uuid4().hex[:8]}"
    manifest = {
        "export_id": export_id,
        "run_id": run_id,
        "config_hash": run.config_hash,
        "seed": run.seed,
        "version": __version__,
    }
    files = [
        store.save_artifact(prefix, "export/manifest.json", manifest),
        store.save_artifact(prefix, "export/experiment.json", exp),
        store.save_artifact(prefix, "export/run.json", run.to_dict()),
    ]
    # Series artifacts are already persisted at completion time.
    for name in ("report/report.json", "trades/trades.json", "snapshots/snapshots.json",
                 "metrics/step_metrics.json", "shocks/shock_events.json",
                 "regimes/regime_events.json"):
        if store.load_artifact(prefix, name) is not None:
            files.append(f"{prefix}/{name}")
    return {"export_id": export_id, "files": files, "s3_prefix": prefix}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "version": __version__, "live_runs": len(runner.live)}


# ---------------------------------------------------------------------------
# Static dashboard (built React app) — mounted last so /api wins.
# ---------------------------------------------------------------------------
_dist = Path(os.environ.get("TEZCAT_FRONTEND_DIST",
                            Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="dashboard")
