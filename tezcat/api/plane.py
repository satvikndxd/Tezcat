"""RESEARCH PLANE API (Phase S5): /api/plane/* — the quant operating layer.

Research infrastructure only: artifacts, lineage, the vertical slice, and
reproduction. No trading surface; no forecast→order path; optional
dependencies answer 503 with an explicit message when absent.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tezcat.plane import ArtifactGraph, PlaneError
from tezcat.plane.forecasting import FORECAST_MODELS
from tezcat.plane.portfolio import (
    PORTFOLIO_OPTIMIZERS, CostModel, PortfolioConstraints,
)

router = APIRouter(prefix="/api/plane", tags=["research-plane"])

_PUBLIC_DEMO = os.environ.get("TEZCAT_PUBLIC_DEMO", "").lower() in {"1", "true", "yes"}
_run_lock = threading.Lock()


def _data_dir() -> str:
    return os.environ.get("TEZCAT_DATA_DIR", "data")


def _http(exc: Exception) -> HTTPException:
    message = str(exc)
    if isinstance(exc, PlaneError) and "unknown artifact" in message:
        return HTTPException(404, message)
    if isinstance(exc, ImportError) or "not installed" in message:
        return HTTPException(503, message)
    if isinstance(exc, (PlaneError, ValueError)):
        return HTTPException(422, message)
    raise exc


class SliceBody(BaseModel):
    history_ref: str = Field(..., min_length=4)
    history_cell: Optional[str] = None
    stress_cells: Optional[List[str]] = None
    model_id: str = "bootstrap_reference"
    model_params: Dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(60, ge=1, le=500)
    n_paths: int = Field(200, ge=20, le=2000)
    seed: int = 7
    optimizer_id: str = "reference_cvar"
    optimizer_params: Dict[str, Any] = Field(default_factory=dict)
    max_weight: float = Field(0.8, ge=0, le=1)
    transaction_cost_bps: float = Field(10.0, ge=0)
    strategy_id: str = "buy_hold"
    capital: float = Field(1_000_000.0, gt=0)


@router.get("/capabilities")
def api_capabilities():
    return {
        "forecast_models": sorted(FORECAST_MODELS),
        "portfolio_optimizers": sorted(PORTFOLIO_OPTIMIZERS),
        "position": "research infrastructure — reproducible experimental "
                    "workflows; not a trading system, not financial advice",
    }


@router.get("/artifacts")
def api_artifacts(artifact_type: Optional[str] = None):
    return ArtifactGraph(_data_dir()).list(artifact_type)


@router.get("/artifacts/{artifact_id}")
def api_artifact(artifact_id: str, include_payload: bool = False):
    graph = ArtifactGraph(_data_dir())
    try:
        artifact = graph.get(artifact_id)
        out: Dict[str, Any] = {"artifact": artifact.model_dump(mode="json")}
        if include_payload:
            out["payload"] = graph.payload(artifact_id)
        return out
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.get("/artifacts/{artifact_id}/lineage")
def api_lineage(artifact_id: str):
    graph = ArtifactGraph(_data_dir())
    try:
        chain = graph.lineage(artifact_id)
        children = graph.children(artifact_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {"lineage": [a.model_dump(mode="json") for a in chain],
            "children": children}


@router.post("/artifacts/{artifact_id}/verify")
def api_verify(artifact_id: str):
    try:
        return ArtifactGraph(_data_dir()).verify(artifact_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/slice", status_code=201)
def api_slice(body: SliceBody):
    from tezcat.plane.slice import run_full_slice
    if _PUBLIC_DEMO and (body.n_paths > 500 or body.horizon > 200):
        raise HTTPException(422, "public demo limit: reduce n_paths/horizon")
    try:
        with _run_lock:
            out = run_full_slice(
                _data_dir(), history_ref=body.history_ref,
                history_cell=body.history_cell,
                stress_cells=body.stress_cells,
                model_id=body.model_id, model_params=body.model_params,
                horizon=body.horizon, n_paths=body.n_paths, seed=body.seed,
                optimizer_id=body.optimizer_id,
                optimizer_params=body.optimizer_params,
                constraints=PortfolioConstraints(max_weight=body.max_weight),
                costs=CostModel(transaction_cost_bps=body.transaction_cost_bps),
                strategy_id=body.strategy_id, capital=body.capital)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return out


@router.post("/reproduce/{report_id}")
def api_reproduce(report_id: str):
    from tezcat.plane.slice import reproduce_slice
    try:
        with _run_lock:
            return reproduce_slice(_data_dir(), report_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
