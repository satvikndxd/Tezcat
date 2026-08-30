"""Scenario analysis via layered assumption overrides (S6).

A scenario is an explicit, persistable object: a name plus a set of
dotted-path overrides applied to the case specification (e.g.
``"forecast.revenue_growth.0": -0.05`` or ``"dcf.wacc": 0.11``). The
whole model is never copied per case — the base spec is the single
source of truth and each scenario is a declared delta, which keeps the
provenance question ("what exactly differs in the downside case?")
answerable from the artifact alone.

Override semantics mirror the experiment layer's discipline: paths must
address existing values (a typo'd path is a loud error, and list
indices must be in range); overrides never create new drivers.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import Field

from tezcat.core.config import FrozenModel, canonical_json
from tezcat.finance.statements import FinanceError


class Scenario(FrozenModel):
    """Named layered override set over the base case spec."""

    name: str = Field(..., min_length=1)
    description: str = ""
    overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="dotted spec path -> replacement value; empty = base")


def apply_spec_overrides(spec: Dict[str, Any],
                         overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new spec dict with dotted-path overrides applied.

    Strict: every path segment must already exist (dicts) or be a valid
    index (lists). Unknown segments raise ``FinanceError`` — a scenario
    must not silently invent assumptions.
    """
    payload = json.loads(canonical_json(spec))
    for path, value in overrides.items():
        node: Any = payload
        parts = path.split(".")
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if isinstance(node, list):
                try:
                    key: Any = int(part)
                except ValueError as exc:
                    raise FinanceError(
                        f"override path {path!r}: segment {part!r} is not a "
                        "list index") from exc
                if not 0 <= key < len(node):
                    raise FinanceError(
                        f"override path {path!r}: index {key} out of range "
                        f"(list has {len(node)} entries)")
            elif isinstance(node, dict):
                key = part
                if key not in node:
                    raise FinanceError(
                        f"override path {path!r}: unknown segment {part!r} "
                        f"(available: {sorted(node)[:8]})")
            else:
                raise FinanceError(
                    f"override path {path!r}: segment {part!r} addresses a "
                    "scalar; the path is too deep")
            if last:
                node[key] = value
            else:
                node = node[key]
    return payload
