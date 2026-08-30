"""FINANCE API (Phase S6): /api/finance/* — valuation & transaction analysis.

Analytical research only: cases execute deterministically from their
persisted specs, outputs are immutable artifacts in the research graph,
and reports render from persisted payloads. Nothing here is investment
advice; the bundled example uses synthetic fixture data.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tezcat.finance.case import (
    ValuationCase, register_case, reproduce_case, run_case,
)
from tezcat.finance.statements import FinanceError
from tezcat.plane.artifacts import ArtifactGraph, PlaneError

router = APIRouter(prefix="/api/finance", tags=["finance"])
_run_lock = threading.Lock()


def _data_dir() -> str:
    return os.environ.get("TEZCAT_DATA_DIR", "data")


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, PlaneError) and "unknown artifact" in str(exc):
        return HTTPException(404, str(exc))
    if isinstance(exc, (FinanceError, PlaneError, ValueError)):
        return HTTPException(422, str(exc))
    raise exc


class CaseBody(BaseModel):
    spec: Dict[str, Any] = Field(..., description="Valuation case spec")
    validate_only: bool = False


@router.post("/cases", status_code=201)
def api_run_case(body: CaseBody):
    try:
        if body.validate_only:
            case = ValuationCase.from_spec(body.spec)
            return {"validated": True, "version_id": case.version_id,
                    "case_hash": case.case_hash()}
        with _run_lock:
            reg = register_case(_data_dir(), body.spec)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
    return {k: reg[k] for k in ("case_artifact", "output_artifact",
                                "report_artifact", "version_id",
                                "case_hash", "results")}


@router.get("/cases")
def api_list_cases():
    graph = ArtifactGraph(_data_dir())
    rows = graph.list("valuation_case")
    out = []
    for r in rows:
        payload = graph.payload(r["artifact_id"])
        out.append({"case_artifact": r["artifact_id"],
                    "case_hash": r["external_identity"],
                    "case_id": payload.get("case_id"),
                    "name": payload.get("name"),
                    "created_at": r["created_at"]})
    return out


def _case_outputs(graph: ArtifactGraph, case_artifact: str) -> Dict[str, Any]:
    outputs = [c for c in graph.children(case_artifact)
               if c["artifact_type"] == "valuation_output"]
    if not outputs:
        raise PlaneError(f"no valuation_output linked to {case_artifact}")
    output_id = outputs[-1]["artifact_id"]
    reports = [g for g in graph.children(output_id)
               if g["artifact_type"] == "finance_report"]
    return {"output_artifact": output_id,
            "report_artifact": reports[-1]["artifact_id"] if reports else None}


@router.get("/cases/{case_artifact}")
def api_get_case(case_artifact: str, include_results: bool = True):
    graph = ArtifactGraph(_data_dir())
    try:
        spec = graph.payload(case_artifact)
        linked = _case_outputs(graph, case_artifact)
        out: Dict[str, Any] = {"case_artifact": case_artifact,
                               "spec": spec, **linked,
                               "data_label": "ANALYTICAL RESEARCH — synthetic "
                                             "fixture inputs unless the spec's "
                                             "provenance says otherwise"}
        if include_results:
            out["results"] = graph.payload(linked["output_artifact"])
        return out
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.get("/cases/{case_artifact}/report")
def api_case_report(case_artifact: str):
    graph = ArtifactGraph(_data_dir())
    try:
        linked = _case_outputs(graph, case_artifact)
        if linked["report_artifact"] is None:
            raise PlaneError("no finance_report artifact for this case")
        return {"case_artifact": case_artifact,
                "markdown": graph.payload(linked["report_artifact"])["markdown"]}
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/cases/{case_artifact}/reproduce")
def api_reproduce(case_artifact: str):
    try:
        with _run_lock:
            return reproduce_case(_data_dir(), case_artifact)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc


@router.post("/preview")
def api_preview(body: CaseBody):
    """Execute a spec without persisting anything (dashboard dry-run)."""
    try:
        return run_case(body.spec)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from exc
