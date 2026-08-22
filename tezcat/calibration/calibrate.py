"""Calibration with enforced out-of-sample discipline (Phase F9).

The workflow the module enforces:

1. Declare a `CalibrationSpec`: parameter grid (dotted config paths →
   candidate values), target features, a **calibration** data source and a
   *different* **validation** data source, replications per evaluation, and
   an evaluation budget. Identical calibration/validation sources are
   rejected at spec construction — "no-validation-period tuning" is a
   schema violation, not a guideline.
2. `calibrate()` runs a deterministic grid search: each candidate parameter
   set is evaluated by simulating `replications` seeded runs and scoring
   the mean |z| of the synthetic ensemble's features against the
   calibration target. Every evaluation is logged; budget exhaustion is an
   explicit `truncated` flag, never silent.
3. The result is **frozen** (parameters + objective + full evaluation log).
4. `validate_calibration()` scores the frozen parameters against the
   validation source only. It refuses a result that was tuned on the
   validation source and refuses to re-tune anything.

Objective: mean *relative error* of the synthetic ensemble mean vs the
target, per feature (``|target - mean| / max(|target|, eps)``). A pure
|z| objective was rejected: dividing by ensemble spread rewards diffuse
models. Per-feature z-distances are still reported as diagnostics.
ABC/history-matching are documented future methods.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Sequence

from pydantic import Field, model_validator

from tezcat.analysis.stats import describe
from tezcat.analysis.stylized_facts import extract_features
from tezcat.core.config import ExperimentConfig, FrozenModel
from tezcat.core.seeds import derive_seed
from tezcat.data.providers import SeriesData
from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.schema import apply_overrides


class CalibrationError(ValueError):
    pass


class CalibrationSpec(FrozenModel):
    parameter_grid: Dict[str, List[Any]] = Field(
        ..., min_length=1, description="dotted config path -> candidate values")
    target_features: List[str] = Field(..., min_length=1)
    calibration_source_id: str = Field(..., min_length=1)
    validation_source_id: str = Field(..., min_length=1)
    replications: int = Field(5, ge=3,
                              description="Synthetic runs per evaluation")
    max_evaluations: int = Field(50, ge=1)
    root_seed: int = 42

    @model_validator(mode="after")
    def _no_validation_tuning(self) -> "CalibrationSpec":
        if self.calibration_source_id == self.validation_source_id:
            raise ValueError(
                "calibration and validation sources are identical — tuning on "
                "the validation period is not permitted; use separate periods")
        for path, levels in self.parameter_grid.items():
            if len(levels) < 1:
                raise ValueError(f"parameter {path!r} has no candidate values")
        return self


def _ensemble_features(config: ExperimentConfig, replications: int,
                       root_seed: int, label: str) -> List[Dict[str, Any]]:
    out = []
    for i in range(replications):
        seed = derive_seed(root_seed, "calibration", label, "rep", i)
        eng = EcologyEngine(f"cal_{label}_{i}", config, seed)
        while not eng.done:
            eng.step()
        prices = [s["last_price"] for s in eng.snapshots]
        out.append(extract_features(prices))
    return out


def _objective(target: Dict[str, Any], ensemble: List[Dict[str, Any]],
               features: Sequence[str]) -> Dict[str, Any]:
    """Mean relative error of the ensemble mean vs the target features.

    z-distances (target vs ensemble spread) are reported as diagnostics but
    deliberately NOT optimized: a diffuse ensemble would minimize |z| for
    free.
    """
    zs: Dict[str, Optional[float]] = {}
    rel_errors = []
    for name in features:
        tval = target.get(name)
        vals = [e.get(name) for e in ensemble]
        vals = [v for v in vals if v is not None]
        if tval is None or len(vals) < 3:
            zs[name] = None
            continue
        d = describe(vals)
        zs[name] = abs(tval - d["mean"]) / max(d["std"], 1e-12)
        rel_errors.append(abs(tval - d["mean"]) / max(abs(tval), 1e-12))
    if not rel_errors:
        raise CalibrationError(
            f"no usable target features among {list(features)}")
    return {"objective": sum(rel_errors) / len(rel_errors),
            "per_feature_z": zs, "n_usable_features": len(rel_errors)}


def calibrate(spec: CalibrationSpec, base_config: ExperimentConfig,
              calibration_data: SeriesData) -> Dict[str, Any]:
    """Deterministic budgeted grid search; returns a frozen result dict."""
    if calibration_data.source_id != spec.calibration_source_id:
        raise CalibrationError(
            f"supplied data source {calibration_data.source_id!r} does not "
            f"match spec.calibration_source_id {spec.calibration_source_id!r}")
    target = extract_features(calibration_data.prices, calibration_data.volumes)

    paths = sorted(spec.parameter_grid)
    combos = list(itertools.product(*(spec.parameter_grid[p] for p in paths)))
    truncated = len(combos) > spec.max_evaluations
    evaluations: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None

    for idx, combo in enumerate(combos[: spec.max_evaluations]):
        params = dict(zip(paths, combo))
        cfg = apply_overrides(base_config, params)
        ensemble = _ensemble_features(cfg, spec.replications, spec.root_seed,
                                      label=f"eval{idx}")
        score = _objective(target, ensemble, spec.target_features)
        row = {"params": params, **score}
        evaluations.append(row)
        if best is None or score["objective"] < best["objective"]:
            best = row

    return {
        "frozen": True,
        "best_params": best["params"],
        "objective_value": best["objective"],
        "objective_name": "mean relative error of ensemble mean vs target",
        "target_features": list(spec.target_features),
        "calibration_source": calibration_data.lineage(),
        "validation_source_id": spec.validation_source_id,
        "replications_per_evaluation": spec.replications,
        "root_seed": spec.root_seed,
        "evaluations": evaluations,
        "n_evaluations": len(evaluations),
        "n_candidates": len(combos),
        "truncated": truncated,
    }


def validate_calibration(result: Dict[str, Any], base_config: ExperimentConfig,
                         validation_data: SeriesData,
                         replications: Optional[int] = None) -> Dict[str, Any]:
    """Score frozen parameters against the held-out validation source.

    Refuses validation data that matches the calibration source, and never
    re-tunes: the parameters come from the frozen result verbatim.
    """
    if not result.get("frozen"):
        raise CalibrationError("result is not frozen; run calibrate() first")
    if validation_data.source_id == result["calibration_source"]["source_id"]:
        raise CalibrationError(
            "validation data is the calibration source — out-of-sample "
            "validation requires a different period/source")
    if validation_data.source_id != result["validation_source_id"]:
        raise CalibrationError(
            f"validation data {validation_data.source_id!r} does not match "
            f"the pre-declared validation source "
            f"{result['validation_source_id']!r}")

    target = extract_features(validation_data.prices, validation_data.volumes)
    cfg = apply_overrides(base_config, result["best_params"])
    reps = replications or result["replications_per_evaluation"]
    ensemble = _ensemble_features(cfg, reps, result["root_seed"], "validate")
    score = _objective(target, ensemble, result["target_features"])
    return {
        "best_params": result["best_params"],
        "in_sample_objective": result["objective_value"],
        "out_of_sample_objective": score["objective"],
        "per_feature_z": score["per_feature_z"],
        "validation_source": validation_data.lineage(),
        "degradation": score["objective"] - result["objective_value"],
    }
