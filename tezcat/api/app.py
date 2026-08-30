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


def _csv_env(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or default


_PUBLIC_DEMO = os.environ.get("TEZCAT_PUBLIC_DEMO", "").lower() in {"1", "true", "yes"}


def _public_limit(name: str, default: Optional[int] = None) -> Optional[int]:
    """Return a deployment-only cap; no cap is applied during local development."""
    if not _PUBLIC_DEMO:
        return None
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning("invalid integer for %s=%r; using default %r", name, raw, default)
        return default


def _ensure_public_config_limit(config: ExperimentConfig) -> None:
    max_steps = _public_limit("TEZCAT_PUBLIC_MAX_STEPS", 5_000)
    if max_steps is not None and config.total_steps > max_steps:
        raise HTTPException(
            422,
            f"public demo limit: total_steps must be <= {max_steps}",
        )


def _ensure_public_design_limit(planned_runs: int) -> None:
    max_runs = _public_limit("TEZCAT_PUBLIC_MAX_RUNS", 60)
    if max_runs is not None and planned_runs > max_runs:
        raise HTTPException(
            422,
            f"public demo limit: planned runs must be <= {max_runs}",
        )


def _bounded_query_limit(value: int, env_name: str, default: int) -> int:
    cap = _public_limit(env_name, default)
    return min(value, cap) if cap is not None else value


store = get_store()
runner = RunManager(store)

app = FastAPI(title="Tezcat", version=__version__,
              description="Agent-based market ecology laboratory")
app.state.tezcat_store = store
app.add_middleware(
    CORSMiddleware,
    allow_origins=_csv_env("TEZCAT_CORS_ORIGINS", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    _ensure_public_config_limit(config)
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
    _ensure_public_config_limit(config)
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
    max_active_runs = _public_limit("TEZCAT_PUBLIC_MAX_ACTIVE_RUNS", 1)
    if max_active_runs is not None:
        active_runs = sum(
            lr.run.status in ("running", "paused") for lr in runner.live.values()
        )
        if active_runs >= max_active_runs:
            raise HTTPException(429, "public demo is at its active run limit; try again later")
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
    limit = _bounded_query_limit(limit, "TEZCAT_PUBLIC_MAX_SERIES", 2_000)
    snaps = _run_data(run_id, "snapshots")
    chunk = snaps[start:start + limit]
    return {"snapshots": chunk, "next_start": start + len(chunk)}


@app.get("/api/runs/{run_id}/trades")
def api_trades(run_id: str, limit: int = 100):
    limit = _bounded_query_limit(limit, "TEZCAT_PUBLIC_MAX_SERIES", 2_000)
    trades = _run_data(run_id, "trades")
    return {"trades": trades[-limit:]}


@app.get("/api/runs/{run_id}/metrics")
def api_metrics(run_id: str, start: int = 0, limit: int = 5000):
    limit = _bounded_query_limit(limit, "TEZCAT_PUBLIC_MAX_SERIES", 2_000)
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
# Research API (Phase F10): register → batch → analyze → report → reproduce
# ---------------------------------------------------------------------------
import threading as _threading  # noqa: E402

from tezcat.experiments.registry import Registry, RegistryError  # noqa: E402

research_registry = Registry(os.environ.get("TEZCAT_DATA_DIR", "data"), artifact_store=store)
app.state.research_registry = research_registry
_active_batches: set = set()
_batch_lock = _threading.Lock()


class ResearchSpecBody(BaseModel):
    experiment_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    root_seed: Optional[int] = None
    config: Dict[str, Any]
    design: Dict[str, Any]
    validate_only: bool = False


class BatchBody(BaseModel):
    max_runs: Optional[int] = Field(None, ge=1)


class AnalyzeBody(BaseModel):
    seed: int = 0
    allow_partial: bool = False


class ReproduceBody(BaseModel):
    sample: Optional[int] = Field(3, ge=1)
    full: bool = False


def _resolve_version(ref: str) -> str:
    from tezcat.experiments.reproduce import ReproductionError, resolve_reference
    try:
        return resolve_reference(research_registry, ref)
    except ReproductionError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/research/experiments", status_code=201)
def api_research_register(body: ResearchSpecBody):
    from pydantic import ValidationError as _VE
    from tezcat.experiments.schema import DesignSpec, ExperimentVersion
    try:
        config = ExperimentConfig.model_validate(body.config)
        _ensure_public_config_limit(config)
        version = ExperimentVersion(
            body.experiment_id, body.name,
            config,
            DesignSpec.model_validate(body.design),
            root_seed=body.root_seed)
        _ensure_public_design_limit(version.design.planned_runs())
    except (_VE, KeyError, ValueError) as exc:
        raise HTTPException(422, f"invalid experiment spec: {exc}") from exc
    if body.validate_only:
        return {"validated": True, "version_id": version.version_id,
                "research_hash": version.research_hash,
                "planned_runs": version.design.planned_runs()}
    vid = research_registry.register(version)
    return research_registry.get(vid)


@app.get("/api/research/experiments")
def api_research_list():
    return research_registry.list()


@app.get("/api/research/experiments/{ref}")
def api_research_get(ref: str):
    return research_registry.get(_resolve_version(ref))


@app.post("/api/research/experiments/{ref}/batch", status_code=202)
def api_research_batch(ref: str, body: Optional[BatchBody] = None):
    from tezcat.experiments.batch import BatchRunner, batch_id_for
    body = body or BatchBody()
    vid = _resolve_version(ref)
    version = research_registry.load(vid)
    planned = version.design.planned_runs()
    requested_runs = body.max_runs if body.max_runs is not None else planned
    max_runs = _public_limit("TEZCAT_PUBLIC_MAX_RUNS", 60)
    if max_runs is not None and requested_runs > max_runs:
        raise HTTPException(
            422,
            f"public demo limit: batch max_runs must be <= {max_runs}",
        )
    bid = batch_id_for(version)
    with _batch_lock:
        if bid in _active_batches:
            raise HTTPException(409, f"batch {bid} is already executing")
        max_active_batches = _public_limit("TEZCAT_PUBLIC_MAX_ACTIVE_BATCHES", 1)
        if max_active_batches is not None and len(_active_batches) >= max_active_batches:
            raise HTTPException(429, "public demo is at its active batch limit; try again later")
        _active_batches.add(bid)

    def _execute():
        try:
            BatchRunner(research_registry).run(vid, max_runs=body.max_runs)
        finally:
            with _batch_lock:
                _active_batches.discard(bid)

    _threading.Thread(target=_execute, daemon=True,
                      name=f"batch-{bid}").start()
    return {"batch_id": bid, "version_id": vid, "status": "started",
            "planned": version.design.planned_runs()}


@app.get("/api/research/batches/{batch_id}")
def api_research_batch_status(batch_id: str):
    batch = research_registry.get_batch(batch_id)
    if batch is None:
        completed = len(research_registry.list_result_rows(batch_id))
        with _batch_lock:
            running = batch_id in _active_batches
        if not completed and not running:
            raise HTTPException(404, "batch not found")
        return {"batch_id": batch_id, "status": "running" if running else "unknown",
                "completed": completed}
    with _batch_lock:
        batch["executing_now"] = batch["batch_id"] in _active_batches
    return batch


@app.post("/api/research/experiments/{ref}/analyze")
def api_research_analyze(ref: str, body: Optional[AnalyzeBody] = None):
    from tezcat.analysis import AnalysisError, analyze
    body = body or AnalyzeBody()
    vid = _resolve_version(ref)
    try:
        return analyze(research_registry, vid, seed=body.seed,
                       allow_partial=body.allow_partial)
    except AnalysisError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/research/experiments/{ref}/analysis")
def api_research_analysis(ref: str):
    a = research_registry.get_analysis(_resolve_version(ref))
    if a is None:
        raise HTTPException(404, "no analysis yet; POST …/analyze first")
    return a


@app.get("/api/research/experiments/{ref}/summary")
def api_research_summary(ref: str):
    from tezcat.experiments.aggregation import aggregate
    return aggregate(research_registry, _resolve_version(ref))


@app.get("/api/research/experiments/{ref}/report")
def api_research_report(ref: str):
    from tezcat.analysis import build_report
    vid = _resolve_version(ref)
    try:
        return {"version_id": vid, "markdown": build_report(research_registry, vid)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/research/experiments/{ref}/reproduce")
def api_research_reproduce(ref: str, body: Optional[ReproduceBody] = None):
    from tezcat.experiments.reproduce import reproduce
    body = body or ReproduceBody()
    return reproduce(research_registry, _resolve_version(ref),
                     sample=None if body.full else body.sample)


# ---------------------------------------------------------------------------
# Scenarios (Phase S2): mutable drafts → the canonical experiment path
# ---------------------------------------------------------------------------
from tezcat.experiments.scenarios import (  # noqa: E402
    ScenarioError, ScenarioStore, scenario_from_preset, scenario_templates,
    validate_spec, version_from_spec,
)

scenario_store = ScenarioStore(os.environ.get("TEZCAT_DATA_DIR", "data"))


class ScenarioBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    spec: Dict[str, Any]


class SpecBody(BaseModel):
    spec: Dict[str, Any]


@app.get("/api/scenarios/templates")
def api_scenario_templates():
    return scenario_templates()


@app.get("/api/scenarios/from-preset/{preset_id}")
def api_scenario_from_preset(preset_id: str):
    try:
        return {"spec": scenario_from_preset(preset_id)}
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/validate")
def api_scenario_validate(body: SpecBody):
    """Dry-run through the real ExperimentVersion machinery: returns the
    exact research identity the spec would mint, or the exact failure."""
    try:
        return validate_spec(body.spec)
    except ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios")
def api_scenarios_list():
    return scenario_store.list()


@app.post("/api/scenarios", status_code=201)
def api_scenario_save(body: ScenarioBody):
    try:
        return scenario_store.save(body.name, body.spec)
    except ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/{scenario_id}")
def api_scenario_get(scenario_id: str):
    try:
        return scenario_store.get(scenario_id)
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/scenarios/{scenario_id}")
def api_scenario_update(scenario_id: str, body: ScenarioBody):
    try:
        scenario_store.get(scenario_id)
        return scenario_store.save(body.name, body.spec, scenario_id=scenario_id)
    except ScenarioError as exc:
        code = 404 if "unknown scenario" in str(exc) else 422
        raise HTTPException(code, str(exc)) from exc


@app.delete("/api/scenarios/{scenario_id}")
def api_scenario_delete(scenario_id: str):
    try:
        scenario_store.delete(scenario_id)
        return {"deleted": scenario_id}
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/{scenario_id}/duplicate", status_code=201)
def api_scenario_duplicate(scenario_id: str):
    try:
        return scenario_store.duplicate(scenario_id)
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/register", status_code=201)
def api_scenario_register(body: SpecBody):
    """Mint the immutable experiment from a spec (the canonical path —
    identical to CLI registration; content-addressed and idempotent)."""
    try:
        version = version_from_spec(body.spec)
    except Exception as exc:  # noqa: BLE001 — surface exact validation error
        raise HTTPException(422, f"invalid experiment spec: {exc}") from exc
    vid = research_registry.register(version)
    return research_registry.get(vid)


# ---------------------------------------------------------------------------
# TradeOps (Phase S2): queue, workers, failures, retries, capacity
# ---------------------------------------------------------------------------
from tezcat.ops import OpsError, OpsLimits, OpsManager  # noqa: E402

ops_manager = OpsManager(
    research_registry,
    limits=OpsLimits.from_env(),
    workers=int(os.environ.get("TEZCAT_OPS_WORKERS", "2")),
    chunk=int(os.environ.get("TEZCAT_OPS_CHUNK", "5")),
    data_dir=os.environ.get("TEZCAT_DATA_DIR", "data"),
)


class OpsSubmitBody(BaseModel):
    version_ref: str = Field(..., min_length=4)


def _ops_error(exc: OpsError) -> HTTPException:
    status = 409 if exc.code in ("duplicate_execution", "not_cancellable",
                                 "not_retryable") else \
             429 if exc.code.startswith("limit_") else 404
    return HTTPException(status, {"error": str(exc), "code": exc.code,
                                  "detail": exc.detail})


@app.get("/api/ops/status")
def api_ops_status():
    return {**ops_manager.status(),
            "data_dir": os.environ.get("TEZCAT_DATA_DIR", "data")}


@app.get("/api/ops/workers")
def api_ops_workers():
    return ops_manager.workers()


@app.get("/api/ops/jobs")
def api_ops_jobs(state: Optional[str] = None):
    return ops_manager.jobs(state=state.upper() if state else None)


@app.get("/api/ops/jobs/{job_id}")
def api_ops_job(job_id: str):
    try:
        return ops_manager.job(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs", status_code=202)
def api_ops_submit(body: OpsSubmitBody):
    vid = _resolve_version(body.version_ref)
    try:
        return ops_manager.submit(vid)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs/{job_id}/cancel")
def api_ops_cancel(job_id: str):
    try:
        return ops_manager.cancel(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs/{job_id}/retry", status_code=202)
def api_ops_retry(job_id: str):
    try:
        return ops_manager.retry(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


# ---------------------------------------------------------------------------
# Scenarios (Phase S2): mutable drafts → the canonical experiment path
# ---------------------------------------------------------------------------
from tezcat.experiments.scenarios import (  # noqa: E402
    ScenarioError, ScenarioStore, scenario_from_preset, scenario_templates,
    validate_spec, version_from_spec,
)

scenario_store = ScenarioStore(os.environ.get("TEZCAT_DATA_DIR", "data"))


class ScenarioBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    spec: Dict[str, Any]


class SpecBody(BaseModel):
    spec: Dict[str, Any]


@app.get("/api/scenarios/templates")
def api_scenario_templates():
    return scenario_templates()


@app.get("/api/scenarios/from-preset/{preset_id}")
def api_scenario_from_preset(preset_id: str):
    try:
        return {"spec": scenario_from_preset(preset_id)}
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/validate")
def api_scenario_validate(body: SpecBody):
    """Dry-run through the real ExperimentVersion machinery: returns the
    exact research identity the spec would mint, or the exact failure."""
    try:
        return validate_spec(body.spec)
    except ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios")
def api_scenarios_list():
    return scenario_store.list()


@app.post("/api/scenarios", status_code=201)
def api_scenario_save(body: ScenarioBody):
    try:
        return scenario_store.save(body.name, body.spec)
    except ScenarioError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/{scenario_id}")
def api_scenario_get(scenario_id: str):
    try:
        return scenario_store.get(scenario_id)
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/scenarios/{scenario_id}")
def api_scenario_update(scenario_id: str, body: ScenarioBody):
    try:
        scenario_store.get(scenario_id)
        return scenario_store.save(body.name, body.spec, scenario_id=scenario_id)
    except ScenarioError as exc:
        code = 404 if "unknown scenario" in str(exc) else 422
        raise HTTPException(code, str(exc)) from exc


@app.delete("/api/scenarios/{scenario_id}")
def api_scenario_delete(scenario_id: str):
    try:
        scenario_store.delete(scenario_id)
        return {"deleted": scenario_id}
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/{scenario_id}/duplicate", status_code=201)
def api_scenario_duplicate(scenario_id: str):
    try:
        return scenario_store.duplicate(scenario_id)
    except ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/scenarios/register", status_code=201)
def api_scenario_register(body: SpecBody):
    """Mint the immutable experiment from a spec (the canonical path —
    identical to CLI registration; content-addressed and idempotent)."""
    try:
        version = version_from_spec(body.spec)
    except Exception as exc:  # noqa: BLE001 — surface exact validation error
        raise HTTPException(422, f"invalid experiment spec: {exc}") from exc
    vid = research_registry.register(version)
    return research_registry.get(vid)


# ---------------------------------------------------------------------------
# TradeOps (Phase S2): queue, workers, failures, retries, capacity
# ---------------------------------------------------------------------------
from tezcat.ops import OpsError, OpsLimits, OpsManager  # noqa: E402

ops_manager = OpsManager(
    research_registry,
    limits=OpsLimits.from_env(),
    workers=int(os.environ.get("TEZCAT_OPS_WORKERS", "2")),
    chunk=int(os.environ.get("TEZCAT_OPS_CHUNK", "5")),
    data_dir=os.environ.get("TEZCAT_DATA_DIR", "data"),
)


class OpsSubmitBody(BaseModel):
    version_ref: str = Field(..., min_length=4)


def _ops_error(exc: OpsError) -> HTTPException:
    status = 409 if exc.code in ("duplicate_execution", "not_cancellable",
                                 "not_retryable") else \
             429 if exc.code.startswith("limit_") else 404
    return HTTPException(status, {"error": str(exc), "code": exc.code,
                                  "detail": exc.detail})


@app.get("/api/ops/status")
def api_ops_status():
    return {**ops_manager.status(),
            "data_dir": os.environ.get("TEZCAT_DATA_DIR", "data")}


@app.get("/api/ops/workers")
def api_ops_workers():
    return ops_manager.workers()


@app.get("/api/ops/jobs")
def api_ops_jobs(state: Optional[str] = None):
    return ops_manager.jobs(state=state.upper() if state else None)


@app.get("/api/ops/jobs/{job_id}")
def api_ops_job(job_id: str):
    try:
        return ops_manager.job(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs", status_code=202)
def api_ops_submit(body: OpsSubmitBody):
    vid = _resolve_version(body.version_ref)
    try:
        return ops_manager.submit(vid)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs/{job_id}/cancel")
def api_ops_cancel(job_id: str):
    try:
        return ops_manager.cancel(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


@app.post("/api/ops/jobs/{job_id}/retry", status_code=202)
def api_ops_retry(job_id: str):
    try:
        return ops_manager.retry(job_id)
    except OpsError as exc:
        raise _ops_error(exc) from exc


# ---------------------------------------------------------------------------
# MARKETS (Phase S3): external event-market intelligence — read-only
# ---------------------------------------------------------------------------
from tezcat.api.markets import router as markets_router  # noqa: E402

app.include_router(markets_router)


# ---------------------------------------------------------------------------
# STRATEGY LAB (Phase S4): market worlds × Nautilus backtests — research only
# ---------------------------------------------------------------------------
from tezcat.api.lab import router as lab_router  # noqa: E402

app.include_router(lab_router)


# ---------------------------------------------------------------------------
# RESEARCH PLANE (Phase S5): artifact graph + full-stack slice — research only
# ---------------------------------------------------------------------------
from tezcat.api.plane import router as plane_router  # noqa: E402

app.include_router(plane_router)


# ---------------------------------------------------------------------------
# FINANCE (Phase S6): valuation & transaction analysis — research only
# ---------------------------------------------------------------------------
from tezcat.api.finance import router as finance_router  # noqa: E402

app.include_router(finance_router)


# ---------------------------------------------------------------------------
# Static dashboard (built React app) — mounted last so /api wins.
# ---------------------------------------------------------------------------
_dist = Path(os.environ.get("TEZCAT_FRONTEND_DIST",
                            Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="dashboard")
