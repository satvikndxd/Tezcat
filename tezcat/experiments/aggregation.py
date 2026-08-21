"""Seed-level aggregation of batch results (Phase F5).

Descriptive statistics only — per-cell n, mean, sample std, min, median,
max, and the full seed-level value list for every dependent variable.
Inference (confidence intervals, effect sizes, comparisons) is Phase F6;
this module deliberately does not compute p-values or draw conclusions.

Missing runs are surfaced explicitly (``missing_keys``) — a partial batch
must never aggregate as if it were complete.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from tezcat.experiments.batch import batch_id_for, run_key
from tezcat.experiments.registry import Registry


def _describe(values: List[float]) -> Dict[str, Any]:
    n = len(values)
    s = sorted(float(v) for v in values)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / (n - 1) if n > 1 else 0.0
    mid = n // 2
    median = s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
    return {"n": n, "mean": mean, "std": math.sqrt(var),
            "min": s[0], "median": median, "max": s[-1]}


def aggregate(registry: Registry, version_id: str) -> Dict[str, Any]:
    """Aggregate the canonical batch of an experiment version by cell."""
    version = registry.load(version_id)
    bid = batch_id_for(version)
    design = version.design

    rows = registry.list_result_rows(bid)
    by_key = {r["key"]: r for r in rows}

    planned, missing = [], []
    for ci, cell in enumerate(version.cell_configs):
        for r in range(design.replications):
            k = run_key(ci, r)
            planned.append(k)
            if k not in by_key:
                missing.append(k)

    cells: Dict[str, Any] = {}
    for ci, cell in enumerate(version.cell_configs):
        cell_rows = [by_key[run_key(ci, r)] for r in range(design.replications)
                     if run_key(ci, r) in by_key]
        if not cell_rows:
            cells[cell["cell"]] = {"n": 0, "metrics": {}, "seeds": [], "runs": []}
            continue
        metrics = {}
        for dv in design.dependent_variables:
            values = [row["metrics"][dv] for row in cell_rows]
            metrics[dv] = {**_describe(values), "values": values}
        cells[cell["cell"]] = {
            "n": len(cell_rows),
            "metrics": metrics,
            "seeds": [row["seed"] for row in cell_rows],
            "runs": [row["run_id"] for row in cell_rows],
        }

    summary = {
        "batch_id": bid,
        "version_id": version.version_id,
        "research_hash": version.research_hash,
        "design_type": design.design_type,
        "primary_metric": design.primary_metric,
        "replications": design.replications,
        "planned_runs": len(planned),
        "completed_runs": len(planned) - len(missing),
        "missing_keys": missing,
        "complete": not missing,
        "cells": cells,
    }
    registry.save_batch_summary(bid, summary)
    return summary
