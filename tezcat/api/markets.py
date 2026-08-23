"""MARKETS API (Phase S3): /api/markets/* — external event-market layer.

Read-only research ingestion; no trading surface exists anywhere below
this router. Provider implementations stay behind the service layer, and
no provider credential is ever read here or sent to the frontend.

Offline-first: live provider fetches require ``TEZCAT_EXTERNAL_LIVE=1``
server-side. In public-demo mode (``TEZCAT_PUBLIC_DEMO``), imports are
restricted to the repository's labeled fixture bundles so the demo never
scrapes providers on visitor traffic — the frontend sees cached, labeled
snapshots only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tezcat.external.datasets import DataQualityError, DatasetError
from tezcat.external.fixtures import FixtureError
from tezcat.external.provider import ProviderError
from tezcat.external.research import BridgeError
from tezcat.external.service import MarketsService, ServiceError
from tezcat.external.signature import SignatureError

router = APIRouter(prefix="/api/markets", tags=["markets"])

_PUBLIC_DEMO = os.environ.get("TEZCAT_PUBLIC_DEMO", "").lower() in {"1", "true", "yes"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_FIXTURE_DIRS = (_REPO_ROOT / "tests" / "fixtures" / "external",
                         _REPO_ROOT / "examples" / "external")


def _service() -> MarketsService:
    return MarketsService(os.environ.get("TEZCAT_DATA_DIR", "data"))


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, (DatasetError, ServiceError)) and "unknown" in str(exc):
        return HTTPException(404, str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(502, f"provider error: {exc}")
    if isinstance(exc, (ServiceError, DatasetError, DataQualityError,
                        SignatureError, BridgeError, FixtureError, ValueError)):
        return HTTPException(422, str(exc))
    raise exc


class ImportBody(BaseModel):
    provider: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1)
    fixture_path: Optional[str] = Field(
        None, description="Labeled fixture bundle (offline import)")
    start_ts: int = 0
    end_ts: Optional[int] = None
    period_minutes: int = Field(60, ge=1, le=1440)
    supersedes: Optional[str] = None


class ResearchBody(BaseModel):
    dataset_id: str = Field(..., min_length=1)
    mechanisms: List[str] = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)
    replications: int = Field(5, ge=2, le=50)
    t0_index: Optional[int] = Field(None, ge=0)
    pre_window: Optional[int] = Field(None, ge=1)
    post_window: Optional[int] = Field(None, ge=1)
    t0_step: int = Field(400, ge=10)
    total_steps: int = Field(1200, ge=100, le=100_000)
    root_seed: Optional[int] = None


class CompareBody(BaseModel):
    dataset_a: str
    dataset_b: str
    threshold: float = Field(0.02, gt=0, lt=1)
    max_lag: int = Field(10, ge=1, le=50)


@router.get("/providers")
def api_providers():
    return _service().providers()


@router.get("/mechanisms")
def api_mechanisms():
    return _service().mechanisms()


@router.get("/datasets")
def api_datasets():
    return _service().list_datasets()


@router.get("/datasets/{dataset_id}")
def api_dataset(dataset_id: str):
    try:
        svc = _service()
        manifest = svc.dataset(dataset_id)
        return {"manifest": manifest.model_dump(mode="json"),
                "versions": svc.datasets.versions(dataset_id),
                "data_label": ("SYNTHETIC FIXTURE DATA"
                               if manifest.source_kind == "synthetic_fixture"
                               else "OBSERVED EXTERNAL MARKET DATA")}
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.get("/datasets/{dataset_id}/observations")
def api_observations(dataset_id: str, start: int = 0, limit: int = 2000):
    try:
        obs = _service().observations(dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    chunk = obs[start:start + min(limit, 5000)]
    return {"observations": [o.model_dump(mode="json") for o in chunk],
            "n_total": len(obs), "next_start": start + len(chunk),
            "note": "implied_probability is a market-implied probability "
                    "proxy, not a forecast"}


@router.post("/import", status_code=201)
def api_import(body: ImportBody):
    if body.fixture_path is not None:
        p = Path(body.fixture_path).resolve()
        if _PUBLIC_DEMO and not any(
                str(p).startswith(str(d)) for d in _ALLOWED_FIXTURE_DIRS):
            raise HTTPException(
                403, "public demo: fixture imports are restricted to the "
                     "bundled example fixtures")
    elif _PUBLIC_DEMO:
        raise HTTPException(
            403, "public demo: live provider imports are disabled; the demo "
                 "serves cached labeled snapshots only")
    try:
        manifest = _service().import_market(
            body.provider, body.market_id, fixture_path=body.fixture_path,
            start_ts=body.start_ts, end_ts=body.end_ts,
            period_minutes=body.period_minutes, supersedes=body.supersedes)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return manifest.model_dump(mode="json")


@router.get("/datasets/{dataset_id}/signature")
def api_signature(dataset_id: str, t0_index: Optional[int] = None,
                  pre_window: Optional[int] = None,
                  post_window: Optional[int] = None):
    try:
        sig = _service().signature(dataset_id, t0_index=t0_index,
                                   pre_window=pre_window,
                                   post_window=post_window)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {"signature": sig.model_dump(mode="json"),
            "signature_hash": sig.signature_hash(),
            "data_label": "INFERRED (computed from observed data)"}


@router.get("/datasets/{dataset_id}/propose")
def api_propose(dataset_id: str, t0_index: Optional[int] = None,
                pre_window: Optional[int] = None,
                post_window: Optional[int] = None):
    try:
        return _service().propose(dataset_id, t0_index=t0_index,
                                  pre_window=pre_window,
                                  post_window=post_window)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/research", status_code=201)
def api_research(body: ResearchBody):
    """Compile an observed signature into an ordinary ExperimentVersion.

    Execution then flows through the existing research/TradeOps surfaces
    (`/api/research/...`, `/api/ops/...`) — there is no second queue.
    """
    if _PUBLIC_DEMO and body.replications * (len(body.mechanisms) + 1) > 60:
        raise HTTPException(422, "public demo limit: planned runs must be <= 60")
    try:
        return _service().create_research(
            body.dataset_id, mechanisms=body.mechanisms, name=body.name,
            replications=body.replications, t0_index=body.t0_index,
            pre_window=body.pre_window, post_window=body.post_window,
            t0_step=body.t0_step, total_steps=body.total_steps,
            root_seed=body.root_seed)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/compare")
def api_compare(body: CompareBody):
    try:
        return _service().compare_datasets(
            body.dataset_a, body.dataset_b,
            threshold=body.threshold, max_lag=body.max_lag)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
