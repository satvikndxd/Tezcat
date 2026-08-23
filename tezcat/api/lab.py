"""STRATEGY LAB API (Phase S4): /api/lab/* — the Nautilus bridge.

Backtest/research only: worlds are deterministic Tezcat realizations,
strategies come from the hashed reference library, and results carry the
full provenance chain. There is no live-trading surface and no order ever
leaves the process. ``nautilus_trader`` is optional — endpoints that need
it return 503 with an explicit message when it is absent.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tezcat.experiments.registry import Registry
from tezcat.lab.results import LabResultStore, reproduce_result
from tezcat.lab.strategies import LabError, list_strategies
from tezcat.worlds import WorldError, WorldStore, build_world

router = APIRouter(prefix="/api/lab", tags=["strategy-lab"])

_PUBLIC_DEMO = os.environ.get("TEZCAT_PUBLIC_DEMO", "").lower() in {"1", "true", "yes"}
_run_lock = threading.Lock()  # one lab computation at a time (they are quick)


def _data_dir() -> str:
    return os.environ.get("TEZCAT_DATA_DIR", "data")


def _http(exc: Exception) -> HTTPException:
    message = str(exc)
    if isinstance(exc, (WorldError, LabError)) and (
            "unknown world" in message or "unknown lab result" in message):
        return HTTPException(404, message)
    if isinstance(exc, ImportError) or "not installed" in str(exc):
        return HTTPException(503, str(exc))
    if isinstance(exc, (WorldError, LabError, ValueError)):
        return HTTPException(422, str(exc))
    raise exc


class BuildWorldBody(BaseModel):
    ref: str = Field(..., min_length=4,
                     description="experiment version id or research hash prefix")
    cell: Optional[str] = None
    replication: int = Field(0, ge=0)


class BacktestBody(BaseModel):
    world_id: str = Field(..., min_length=4)
    strategy_id: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)
    starting_cash: float = Field(1_000_000.0, gt=0)


class CompareBody(BaseModel):
    result_ids: List[str] = Field(..., min_length=2)


@router.get("/strategies")
def api_strategies():
    return list_strategies()


@router.get("/worlds")
def api_worlds():
    return WorldStore(_data_dir()).list()


@router.post("/worlds", status_code=201)
def api_build_world(body: BuildWorldBody):
    try:
        with _run_lock:
            world = build_world(Registry(_data_dir()), body.ref,
                                cell=body.cell, replication=body.replication)
            WorldStore(_data_dir()).save(world)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {"world_id": world.world_id, "manifest": world.manifest,
            "data_label": "SYNTHETIC TEZCAT MARKET WORLD"}


@router.get("/worlds/{world_id}")
def api_world(world_id: str):
    try:
        world = WorldStore(_data_dir()).load(world_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {"manifest": world.manifest,
            "annotations": world.annotations,
            "data_label": "SYNTHETIC TEZCAT MARKET WORLD"}


@router.get("/worlds/{world_id}/quotes")
def api_world_quotes(world_id: str, start: int = 0, limit: int = 2000):
    try:
        world = WorldStore(_data_dir()).load(world_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    chunk = world.quotes[start:start + min(limit, 5000)]
    return {"quotes": chunk, "n_total": len(world.quotes),
            "next_start": start + len(chunk),
            "data_label": "SYNTHETIC TEZCAT MARKET WORLD"}


@router.post("/backtests", status_code=201)
def api_run_backtest(body: BacktestBody):
    from tezcat.lab.backtest import run_backtest
    if _PUBLIC_DEMO and body.starting_cash > 10_000_000:
        raise HTTPException(422, "public demo limit: starting_cash too large")
    try:
        with _run_lock:
            world = WorldStore(_data_dir()).load(body.world_id)
            result = run_backtest(world, body.strategy_id, body.params,
                                  starting_cash=body.starting_cash)
            rid = LabResultStore(_data_dir()).save(result)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {"result_id": rid, **result}


@router.get("/results")
def api_results():
    return LabResultStore(_data_dir()).list()


@router.get("/results/{result_id}")
def api_result(result_id: str):
    try:
        return LabResultStore(_data_dir()).get(result_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/results/compare")
def api_compare(body: CompareBody):
    try:
        return LabResultStore(_data_dir()).compare(body.result_ids)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/results/{result_id}/reproduce")
def api_reproduce(result_id: str):
    try:
        with _run_lock:
            return reproduce_result(Registry(_data_dir()),
                                    WorldStore(_data_dir()),
                                    LabResultStore(_data_dir()), result_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
