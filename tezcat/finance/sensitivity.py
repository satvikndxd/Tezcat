"""Generic sensitivity analysis (S6).

A sensitivity is a declared research input, not a precomputed table:

    SensitivitySpec(
        name="dcf: WACC x terminal growth",
        axis1=SensitivityAxis(path="dcf.wacc", values=[...]),
        axis2=SensitivityAxis(path="dcf.terminal_growth", values=[...]),
        metric="dcf.implied_value_per_share",
    )

Each cell applies the two axis overrides to the base case spec (same
strict override semantics as scenarios) and re-runs the *actual model*;
nothing is interpolated. The engine is generic: the same machinery
drives WACC × terminal growth, WACC × exit multiple, and purchase
premium × synergies grids. Cells whose assumption combination is
invalid (e.g. terminal growth ≥ WACC) are reported as explicit
``"invalid: <reason>"`` entries — the constraint surface is part of the
result, never smoothed over.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from pydantic import Field

from tezcat.core.config import FrozenModel
from tezcat.finance.scenarios import apply_spec_overrides
from tezcat.finance.statements import FinanceError


class SensitivityAxis(FrozenModel):
    path: str = Field(..., min_length=1, description="Dotted spec path")
    values: List[Any] = Field(..., min_length=2)
    label: str = ""


class SensitivitySpec(FrozenModel):
    name: str = Field(..., min_length=1)
    axis1: SensitivityAxis
    axis2: SensitivityAxis
    metric: str = Field(..., min_length=1,
                        description="Dotted path into the case results, "
                                    "e.g. 'dcf.implied_value_per_share'")


def extract_metric(results: Dict[str, Any], path: str) -> Any:
    node: Any = results
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise FinanceError(
                f"sensitivity metric path {path!r}: segment {part!r} not "
                "found in case results")
    if not isinstance(node, (int, float)):
        raise FinanceError(f"sensitivity metric {path!r} is not numeric")
    return node


def run_sensitivity(base_spec: Dict[str, Any], spec: SensitivitySpec,
                    runner: Callable[[Dict[str, Any]], Dict[str, Any]]
                    ) -> Dict[str, Any]:
    """Evaluate the grid by re-running the model per cell."""
    grid: List[List[Any]] = []
    n_invalid = 0
    for v1 in spec.axis1.values:
        row: List[Any] = []
        for v2 in spec.axis2.values:
            overridden = apply_spec_overrides(
                base_spec, {spec.axis1.path: v1, spec.axis2.path: v2})
            try:
                results = runner(overridden)
                row.append(round(extract_metric(results, spec.metric), 6))
            except FinanceError as exc:
                n_invalid += 1
                row.append(f"invalid: {str(exc)[:60]}")
        grid.append(row)
    return {
        "kind": "derived",
        "name": spec.name,
        "axis1": spec.axis1.model_dump(mode="json"),
        "axis2": spec.axis2.model_dump(mode="json"),
        "metric": spec.metric,
        "grid": grid,
        "n_cells": len(spec.axis1.values) * len(spec.axis2.values),
        "n_invalid_cells": n_invalid,
        "note": "each cell is a full model re-run under the overridden "
                "assumptions; invalid assumption combinations are reported "
                "explicitly, never interpolated",
    }
