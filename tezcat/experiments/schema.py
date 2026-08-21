"""Research-object schema (Phase F4).

An *experiment version* is the immutable unit of scientific intent: a base
config, a validated design (question, hypothesis, variables, control,
treatments, replications, budget), a deterministic seed plan, and a content
hash (the research identity). Runs execute cells of its design matrix; they
never define the science themselves.

Design types
------------
baseline   one cell, no comparison (demonstrations, calibration anchors)
ab         control arm vs >=1 treatment arms
ablation   control (full model) vs arms with mechanisms removed/neutralized
sweep      one factor over >=2 levels
factorial  >=2 factors, full cartesian product of levels

Every non-baseline design requires >=2 replications: one seed is a
demonstration, not evidence.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from tezcat import __version__
from tezcat.core.config import (
    SCHEMA_VERSION, ExperimentConfig, FrozenModel, canonical_json, config_hash,
)
from tezcat.core.seeds import ALLOCATOR_VERSION, derive_seed


# ---------------------------------------------------------------------------
# Config override application (dotted paths)
# ---------------------------------------------------------------------------
def apply_overrides(config: ExperimentConfig, overrides: Dict[str, Any]) -> ExperimentConfig:
    """Return a new validated config with dotted-path overrides applied.

    Paths address the JSON dump: ``"total_steps"``, ``"market.tick_size"``,
    ``"agents.1.params.herding"``. Unknown paths raise ``KeyError``; invalid
    resulting configs raise pydantic ``ValidationError``. The input config is
    never mutated (it is frozen).

    Exception: strategy ``params`` dicts are open-ended, so a *final* segment
    may be created inside a ``params`` dict (treatments must be able to set
    e.g. ``agents.1.params.herding`` even when the base config leaves it at
    the strategy default). Everywhere else, unknown segments are fatal —
    pydantic would silently ignore a typo'd extra key otherwise.
    """
    payload = config.model_dump(mode="json")
    for path, value in overrides.items():
        node: Any = payload
        parts = path.split(".")
        prev_segment = ""
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            key: Any = int(part) if isinstance(node, list) else part
            if isinstance(node, list):
                if not (0 <= key < len(node)):
                    raise KeyError(f"override path {path!r}: index {part} out of range")
            elif not isinstance(node, dict) or key not in node:
                creatable = (last and isinstance(node, dict)
                             and prev_segment == "params")
                if not creatable:
                    raise KeyError(f"override path {path!r}: unknown segment {part!r}")
            if last:
                node[key] = value
            else:
                node = node[key]
                prev_segment = part
    return ExperimentConfig.model_validate(payload)


# ---------------------------------------------------------------------------
# Design schema
# ---------------------------------------------------------------------------
class Arm(FrozenModel):
    """A named configuration variant (control or treatment)."""

    name: str = Field(..., min_length=1)
    overrides: Dict[str, Any] = Field(default_factory=dict)


class Factor(FrozenModel):
    """One swept dimension of the parameter space."""

    name: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1, description="Dotted config path")
    levels: List[Any] = Field(..., min_length=2)


class DesignSpec(FrozenModel):
    design_type: Literal["baseline", "ab", "ablation", "sweep", "factorial"]
    question: str = Field(..., min_length=8)
    hypothesis: str = Field(..., min_length=8)
    independent_variables: List[str] = Field(default_factory=list)
    dependent_variables: List[str] = Field(..., min_length=1,
                                           description="Report metric names")
    primary_metric: str
    control: Optional[Arm] = None
    treatments: List[Arm] = Field(default_factory=list)
    factors: List[Factor] = Field(default_factory=list)
    replications: int = Field(..., ge=1)
    max_runs: Optional[int] = Field(None, ge=1, description="Hard budget")
    robustness_checks: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_design(self) -> "DesignSpec":
        if self.primary_metric not in self.dependent_variables:
            raise ValueError("primary_metric must be one of dependent_variables")

        dt = self.design_type
        if dt != "baseline" and self.replications < 2:
            raise ValueError(
                f"{dt} designs require >=2 replications; one seed is not evidence")

        if dt in ("ab", "ablation"):
            if self.control is None:
                raise ValueError(f"{dt} design requires a control arm")
            if not self.treatments:
                raise ValueError(f"{dt} design requires at least one treatment arm")
            names = [self.control.name] + [t.name for t in self.treatments]
            if len(set(names)) != len(names):
                raise ValueError("arm names must be unique")
            for t in self.treatments:
                if not t.overrides:
                    raise ValueError(f"treatment {t.name!r} has no overrides — "
                                     "it would be indistinguishable from control")
                if t.overrides == self.control.overrides:
                    raise ValueError(f"treatment {t.name!r} equals the control")
            if not self.independent_variables:
                raise ValueError(f"{dt} design requires independent_variables")
        elif dt == "sweep":
            if len(self.factors) != 1:
                raise ValueError("sweep design requires exactly one factor")
        elif dt == "factorial":
            if len(self.factors) < 2:
                raise ValueError("factorial design requires >=2 factors")
        if self.factors:
            fnames = [f.name for f in self.factors]
            if len(set(fnames)) != len(fnames):
                raise ValueError("factor names must be unique")
        return self

    # -- design matrix --------------------------------------------------
    def cells(self) -> List[Dict[str, Any]]:
        """Ordered list of {"cell", "overrides"} for this design."""
        dt = self.design_type
        if dt == "baseline":
            base = self.control or Arm(name="baseline")
            return [{"cell": base.name, "overrides": dict(base.overrides)}]
        if dt in ("ab", "ablation"):
            out = [{"cell": self.control.name, "overrides": dict(self.control.overrides)}]
            out += [{"cell": t.name, "overrides": dict(t.overrides)}
                    for t in self.treatments]
            return out
        if dt == "sweep":
            f = self.factors[0]
            return [{"cell": f"{f.name}={lvl}", "overrides": {f.path: lvl}}
                    for lvl in f.levels]
        # factorial: cartesian product in declared factor order
        cells: List[Dict[str, Any]] = [{"cell": "", "overrides": {}}]
        for f in self.factors:
            cells = [{"cell": (c["cell"] + "|" if c["cell"] else "") + f"{f.name}={lvl}",
                      "overrides": {**c["overrides"], f.path: lvl}}
                     for c in cells for lvl in f.levels]
        return cells

    def planned_runs(self) -> int:
        return len(self.cells()) * self.replications


# ---------------------------------------------------------------------------
# Supporting research entities
# ---------------------------------------------------------------------------
class ModelCard(FrozenModel):
    purpose: str
    assumptions: List[str] = Field(default_factory=list)
    mechanisms: List[str] = Field(default_factory=list)
    calibration_status: str = "uncalibrated: parameters are hand-tuned, not fit to data"
    validation_status: str = "mechanism-level tests only; no real-market validation"
    failure_modes: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    claims_not_supported: List[str] = Field(default_factory=list)


def default_model_card() -> ModelCard:
    """The honest card for the current synthetic market model."""
    return ModelCard(
        purpose=("Mechanism laboratory for studying how heterogeneous trading "
                 "behavior, liquidity, and exogenous shocks combine to generate "
                 "market phenomena in a synthetic limit order book."),
        assumptions=[
            "single asset, single venue, no fees or financing",
            "integer quantities; prices snapped to a fixed tick",
            "no leverage, margin, or short selling by default",
            "agents act once per discrete step in seeded random order",
            "whale shocks are exogenous scripted interventions",
        ],
        mechanisms=[
            "price-time priority matching with partial fills",
            "reservation-based cash/inventory accounting",
            "five hand-tuned strategy classes with EMA memory",
            "rule-based regime detection with hysteresis",
        ],
        failure_modes=[
            "scenario outcomes can be dominated by scripted shocks rather than "
            "endogenous dynamics",
            "crash/bubble labels are threshold heuristics, not validated events",
        ],
        limitations=[
            "not calibrated to any real market; no out-of-sample validation",
            "single-seed preset outputs are demonstrations, not evidence",
            "latency, queue position, and microstructure metrics not yet modeled",
        ],
        claims_not_supported=[
            "prediction of real market prices",
            "statistically validated stylized facts",
            "causal claims about real-world markets",
        ],
    )


class InterventionSpec(FrozenModel):
    """Registry record of a fork-time controlled intervention (F3 lineage)."""

    name: str
    parent_run_id: str
    checkpoint_step: int
    checkpoint_hash: str
    operations: List[Dict[str, Any]]
    rationale: str = ""
    hypothesis: str = ""


class DataSource(FrozenModel):
    """Provenance of any external data input (unused until Phase F9)."""

    source_id: str
    provider: str
    instrument: str = ""
    date_range: str = ""
    license: str = ""
    sampling: str = ""
    checksum: str = ""


# ---------------------------------------------------------------------------
# Experiment version: the immutable research object
# ---------------------------------------------------------------------------
class ExperimentVersion:
    """Immutable, content-addressed executable research specification.

    Construction resolves and validates every cell config up front — an
    invalid design (bad override path, invalid resulting config, budget
    overrun) is rejected before anything can run.
    """

    def __init__(self, experiment_id: str, name: str, config: ExperimentConfig,
                 design: DesignSpec, root_seed: Optional[int] = None,
                 model_card: Optional[ModelCard] = None,
                 parent_version_id: Optional[str] = None):
        self.experiment_id = experiment_id
        self.name = name
        self.config = config
        self.design = design
        self.root_seed = config.default_seed if root_seed is None else root_seed
        self.model_card = model_card or default_model_card()
        self.parent_version_id = parent_version_id

        # Resolve every cell now; fail loudly before registration.
        self.cell_configs: List[Dict[str, Any]] = []
        for cell in design.cells():
            resolved = apply_overrides(config, cell["overrides"])
            self.cell_configs.append({
                "cell": cell["cell"],
                "overrides": cell["overrides"],
                "config_hash": config_hash(resolved),
            })
        names = [c["cell"] for c in self.cell_configs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate cell names in design matrix: {names}")

        if design.max_runs is not None and design.planned_runs() > design.max_runs:
            raise ValueError(
                f"design plans {design.planned_runs()} runs but max_runs is "
                f"{design.max_runs}; shrink the design or raise the budget")

        self.research_hash = self._research_hash()
        self.version_id = f"expv_{self.research_hash[:12]}"

    # ------------------------------------------------------------------
    def _research_hash(self) -> str:
        """Research identity: names semantics, not just parameter values."""
        payload = {
            "schema_version": SCHEMA_VERSION,
            "seed_allocator_version": ALLOCATOR_VERSION,
            "code_version": __version__,
            "base_config": self.config.model_dump(mode="json"),
            "design": self.design.model_dump(mode="json"),
            "root_seed": self.root_seed,
            "parent_version_id": self.parent_version_id,
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    # ------------------------------------------------------------------
    def cell_config(self, cell_name: str) -> ExperimentConfig:
        for c in self.cell_configs:
            if c["cell"] == cell_name:
                return apply_overrides(self.config, c["overrides"])
        raise KeyError(f"unknown cell {cell_name!r}")

    def seed_for(self, cell_name: str, replication: int) -> int:
        """Deterministic, worker/order-independent seed for one planned run."""
        if not 0 <= replication < self.design.replications:
            raise ValueError(f"replication {replication} outside design "
                             f"(0..{self.design.replications - 1})")
        return derive_seed(self.root_seed, "cell", cell_name,
                           "replication", replication)

    def design_matrix(self) -> List[Dict[str, Any]]:
        """Every planned run: (cell, replication, seed, config_hash)."""
        out = []
        for c in self.cell_configs:
            for r in range(self.design.replications):
                out.append({
                    "cell": c["cell"],
                    "replication": r,
                    "seed": self.seed_for(c["cell"], r),
                    "config_hash": c["config_hash"],
                })
        return out

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "experiment_id": self.experiment_id,
            "name": self.name,
            "research_hash": self.research_hash,
            "schema_version": SCHEMA_VERSION,
            "seed_allocator_version": ALLOCATOR_VERSION,
            "code_version": __version__,
            "root_seed": self.root_seed,
            "parent_version_id": self.parent_version_id,
            "config": self.config.model_dump(mode="json"),
            "config_hash": config_hash(self.config),
            "design": self.design.model_dump(mode="json"),
            "cells": self.cell_configs,
            "planned_runs": self.design.planned_runs(),
            "model_card": self.model_card.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExperimentVersion":
        ev = cls(
            experiment_id=d["experiment_id"], name=d["name"],
            config=ExperimentConfig.model_validate(d["config"]),
            design=DesignSpec.model_validate(d["design"]),
            root_seed=d["root_seed"],
            model_card=ModelCard.model_validate(d["model_card"]),
            parent_version_id=d.get("parent_version_id"),
        )
        if ev.research_hash != d["research_hash"]:
            raise ValueError(
                f"experiment version {d['version_id']}: stored research_hash "
                f"{d['research_hash'][:12]}… does not match recomputed "
                f"{ev.research_hash[:12]}… (schema/code/seed-allocator drift "
                "— register a new version instead of reusing the old record)")
        return ev
