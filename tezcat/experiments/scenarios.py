"""Scenario drafts and templates (Phase S2).

Semantics — three distinct things, deliberately kept apart:

- **Scenario**: a *mutable draft* of an experiment specification (the same
  JSON spec format the CLI consumes). Drafts have display ids (``scn_…``),
  can be edited, duplicated, and deleted, and carry NO research identity.
- **Experiment**: the immutable, content-addressed research object derived
  from a spec via the existing ``ExperimentVersion`` machinery. Registering
  a scenario goes through that exact path — there is no second execution
  or hashing route. Editing a scenario and re-registering yields a *new*
  experiment identity; historical research objects are never mutated.
- **Run**: an execution of an experiment (existing batch semantics).

Templates ("try an idea") and preset conversions are ordinary specs built
from mechanisms that already exist; they pass through the same validation
and registration as anything a user types.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tezcat.core.config import ExperimentConfig
from tezcat.experiments.presets import PRESETS, build_config
from tezcat.experiments.schema import DesignSpec, ExperimentVersion


class ScenarioError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Spec validation → identity preview (the canonical path, dry-run)
# ---------------------------------------------------------------------------
def validate_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a spec through the real ExperimentVersion machinery.

    Returns the identity preview {version_id, research_hash, planned_runs,
    cells}; raises ``ScenarioError`` with the exact validation failure.
    Nothing is registered or executed.
    """
    try:
        config = ExperimentConfig.model_validate(spec["config"])
        design = DesignSpec.model_validate(spec["design"])
        version = ExperimentVersion(
            experiment_id=spec.get("experiment_id", "exp_custom"),
            name=spec.get("name", "custom-scenario"),
            config=config, design=design,
            root_seed=spec.get("root_seed"))
    except KeyError as exc:
        raise ScenarioError(f"spec is missing required section: {exc}") from exc
    except Exception as exc:  # pydantic ValidationError, ValueError, KeyError paths
        raise ScenarioError(str(exc)) from exc
    return {
        "valid": True,
        "version_id": version.version_id,
        "research_hash": version.research_hash,
        "planned_runs": version.design.planned_runs(),
        "cells": [c["cell"] for c in version.cell_configs],
    }


def version_from_spec(spec: Dict[str, Any]) -> ExperimentVersion:
    """Build the immutable research object from a spec (canonical path)."""
    return ExperimentVersion(
        experiment_id=spec.get("experiment_id", "exp_custom"),
        name=spec.get("name", "custom-scenario"),
        config=ExperimentConfig.model_validate(spec["config"]),
        design=DesignSpec.model_validate(spec["design"]),
        root_seed=spec.get("root_seed"))


# ---------------------------------------------------------------------------
# Draft store
# ---------------------------------------------------------------------------
class ScenarioStore:
    """File-backed store of mutable scenario drafts under <root>/scenarios."""

    def __init__(self, root: str = "data"):
        self.root = Path(root) / "scenarios"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, scenario_id: str) -> Path:
        return self.root / f"{scenario_id}.json"

    def _write(self, path: Path, obj: Any) -> None:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(obj, indent=1))
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    def save(self, name: str, spec: Dict[str, Any],
             scenario_id: Optional[str] = None) -> Dict[str, Any]:
        """Create or update a draft. Updating a draft is fine — drafts have
        no research identity; only registration mints one."""
        validate_spec(spec)  # a draft must at least be a valid spec
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            if scenario_id is None:
                scenario_id = f"scn_{uuid.uuid4().hex[:8]}"
                record = {"scenario_id": scenario_id, "name": name,
                          "spec": spec, "created_at": now, "updated_at": now}
            else:
                existing = self.get(scenario_id)
                record = {**existing, "name": name, "spec": spec,
                          "updated_at": now}
            self._write(self._path(scenario_id), record)
        return record

    def get(self, scenario_id: str) -> Dict[str, Any]:
        p = self._path(scenario_id)
        if not p.exists():
            raise ScenarioError(f"unknown scenario {scenario_id!r}")
        return json.loads(p.read_text())

    def list(self) -> List[Dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("scn_*.json")):
            d = json.loads(p.read_text())
            out.append({k: d[k] for k in
                        ("scenario_id", "name", "created_at", "updated_at")})
        return sorted(out, key=lambda d: d["updated_at"], reverse=True)

    def delete(self, scenario_id: str) -> None:
        p = self._path(scenario_id)
        if not p.exists():
            raise ScenarioError(f"unknown scenario {scenario_id!r}")
        with self._lock:
            p.unlink()

    def duplicate(self, scenario_id: str) -> Dict[str, Any]:
        src = self.get(scenario_id)
        return self.save(f"{src['name']} (copy)", src["spec"])


# ---------------------------------------------------------------------------
# Templates: worked starting points built from existing mechanisms only
# ---------------------------------------------------------------------------
def _baseline_design(question: str, hypothesis: str, replications: int,
                     risk_enabled: bool) -> Dict[str, Any]:
    dvs = ["max_drawdown", "total_return", "realized_volatility", "total_trades"]
    if risk_enabled:
        dvs += ["risk_forced_volume", "risk_liquidation_slices",
                "risk_margin_calls", "risk_max_leverage"]
    return {"design_type": "baseline", "question": question,
            "hypothesis": hypothesis, "dependent_variables": dvs,
            "primary_metric": "max_drawdown", "replications": replications}


def scenario_templates() -> List[Dict[str, Any]]:
    """The 'try an idea' starters. Each is a full valid spec."""
    from tezcat.risk.stress import stressed_base_config

    spiral = stressed_base_config().model_dump(mode="json")

    retail_panic = ExperimentConfig.model_validate({
        "agents": [
            {"agent_type": "noise_trader", "count": 10},
            {"agent_type": "retail_trader", "count": 40,
             "params": {"herding": 0.9}},
            {"agent_type": "mean_reversion_trader", "count": 5},
            {"agent_type": "market_maker", "count": 3},
        ],
        "shocks": [{"shock_id": "panic", "shock_type": "sentiment_shock",
                    "trigger": {"kind": "scheduled", "step": 300},
                    "side": "sell", "magnitude": 0.85, "duration": 60}],
        "total_steps": 800,
    }).model_dump(mode="json")

    momentum_bubble = ExperimentConfig.model_validate({
        "agents": [
            {"agent_type": "noise_trader", "count": 10},
            {"agent_type": "momentum_trader", "count": 25,
             "params": {"threshold": 0.001}},
            {"agent_type": "retail_trader", "count": 15,
             "params": {"herding": 0.7}},
            {"agent_type": "market_maker", "count": 3},
        ],
        "shocks": [
            {"shock_id": "hype1", "shock_type": "sentiment_shock",
             "trigger": {"kind": "scheduled", "step": 150},
             "side": "buy", "magnitude": 0.7, "duration": 80},
            {"shock_id": "hype2", "shock_type": "sentiment_shock",
             "trigger": {"kind": "scheduled", "step": 400},
             "side": "buy", "magnitude": 0.7, "duration": 80},
        ],
        "total_steps": 800,
    }).model_dump(mode="json")

    liquidity_vacuum = ExperimentConfig.model_validate({
        "agents": [
            {"agent_type": "noise_trader", "count": 15},
            {"agent_type": "retail_trader", "count": 15,
             "params": {"herding": 0.6}},
            {"agent_type": "market_maker", "count": 1},
        ],
        "shocks": [
            {"shock_id": "withdraw", "shock_type": "mm_withdrawal",
             "trigger": {"kind": "scheduled", "step": 380},
             "magnitude": 1.0, "duration": 60},
            {"shock_id": "whale", "shock_type": "whale_order",
             "trigger": {"kind": "scheduled", "step": 400},
             "side": "sell", "magnitude": 1500, "duration": 8},
        ],
        "total_steps": 800,
    }).model_dump(mode="json")

    templates = [
        {"template_id": "high_leverage_spiral",
         "name": "High-Leverage Spiral",
         "description": "10× leverage cap, pump-then-dump, endogenous "
                        "liquidation cascades (the F8 stress scenario).",
         "spec": {"experiment_id": "exp_spiral", "name": "high-leverage-spiral",
                  "root_seed": 42, "config": spiral,
                  "design": _baseline_design(
                      "Does the pump-and-dump end in a liquidation spiral?",
                      "Levered longs breach maintenance margin after the dump.",
                      20, risk_enabled=True)}},
        {"template_id": "retail_panic",
         "name": "Retail Panic",
         "description": "Retail-heavy ecology with strong herding hit by a "
                        "negative sentiment shock.",
         "spec": {"experiment_id": "exp_panic", "name": "retail-panic",
                  "root_seed": 42, "config": retail_panic,
                  "design": _baseline_design(
                      "Does herded retail flow amplify a sentiment shock?",
                      "Panic selling deepens drawdown beyond the shock itself.",
                      20, risk_enabled=False)}},
        {"template_id": "momentum_bubble",
         "name": "Momentum Bubble",
         "description": "Momentum-heavy ecology under repeated positive "
                        "sentiment waves.",
         "spec": {"experiment_id": "exp_bubble", "name": "momentum-bubble",
                  "root_seed": 42, "config": momentum_bubble,
                  "design": _baseline_design(
                      "Do repeated hype waves inflate a price bubble?",
                      "Momentum chasing converts sentiment into price runs.",
                      20, risk_enabled=False)}},
        {"template_id": "liquidity_vacuum",
         "name": "Liquidity Vacuum",
         "description": "A single market maker withdraws just before a "
                        "whale sell hits the thin book.",
         "spec": {"experiment_id": "exp_vacuum", "name": "liquidity-vacuum",
                  "root_seed": 42, "config": liquidity_vacuum,
                  "design": _baseline_design(
                      "What happens when the only liquidity provider leaves?",
                      "The whale sell gaps the book without MM absorption.",
                      20, risk_enabled=False)}},
    ]
    for t in templates:
        validate_spec(t["spec"])  # templates must always be registrable
    return templates


def scenario_from_preset(preset_id: str,
                         replications: int = 10) -> Dict[str, Any]:
    """Convert one of the three demo presets into a scenario spec — presets
    become templates, not a separate capability."""
    if preset_id not in PRESETS:
        raise ScenarioError(f"unknown preset {preset_id!r}")
    config = build_config(preset_id)
    spec = {
        "experiment_id": f"exp_{preset_id}",
        "name": f"{preset_id}-scenario",
        "root_seed": config.default_seed,
        "config": config.model_dump(mode="json"),
        "design": _baseline_design(
            f"What does the {preset_id} preset ecology produce?",
            "Preset demonstration promoted to a replicated experiment.",
            replications, risk_enabled=config.risk.enabled),
    }
    validate_spec(spec)
    return spec
