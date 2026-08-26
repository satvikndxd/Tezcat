"""Portfolio desk (Phase S5-C): typed forecast→portfolio seam.

The optimizer never consumes raw model objects: it consumes a normalized
:class:`PortfolioView` built from a forecast *artifact*, plus explicit
constraints and cost assumptions. Costs enter **before** performance —
gross vs net expectations are separate fields, and every decision input
appears in a deterministic **decision trace** (§14: a researcher can
reconstruct why the optimizer produced the weights; no LLM narration).

Two implementations of the provider-neutral ``PortfolioOptimizer``
protocol ship:

* ``reference_cvar`` — dependency-free deterministic grid search over the
  risky weight, maximizing ``E[r]·w − λ·CVaR₉₅(w) − costs(|Δw|)``.
* ``skfolio_cvar`` — optional adapter over skfolio's MeanRisk/CVaR
  estimator (partial-budget long-only). skfolio's refusal to allocate
  when all expected returns underperform the risk-free rate is caught
  and recorded as an explicit zero-allocation decision, never hidden.

The portfolio here sizes exposure to one synthetic risky instrument vs
cash — deliberately minimal for the first vertical slice, with the
contract shaped for multi-asset views later.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import Field

from tezcat.core.config import FrozenModel, canonical_json
from tezcat.plane.artifacts import ArtifactGraph, PlaneError, ResearchArtifact

PORTFOLIO_ADAPTER_VERSION = 1


class PortfolioView(FrozenModel):
    """Normalized research view the optimizer consumes (never raw models)."""

    view_type: str = "distributional"
    instrument: str
    horizon: int = Field(..., ge=1)
    expected_return: float
    dispersion: float = Field(..., ge=0)
    q05: float
    q95: float
    sample_returns: List[float] = Field(..., min_length=20,
                                        description="Terminal simple returns "
                                                    "from the forecast paths")
    forecast_hash: str = Field(..., min_length=8)

    @classmethod
    def from_forecast(cls, payload: Dict[str, Any],
                      forecast_hash: str) -> "PortfolioView":
        last = payload["last_price"]
        samples = [p[-1] / last - 1.0 for p in payload["forecast_paths"]]
        t = payload["terminal"]
        return cls(instrument=payload["instrument"],
                   horizon=payload["horizon"],
                   expected_return=t["mean_return"],
                   dispersion=t["dispersion"],
                   q05=t["q05"], q95=t["q95"],
                   sample_returns=samples, forecast_hash=forecast_hash)


class PortfolioConstraints(FrozenModel):
    min_weight: float = Field(0.0, ge=0, le=1)
    max_weight: float = Field(1.0, ge=0, le=1)
    max_turnover: float = Field(1.0, ge=0, le=2,
                                description="Max |Δw| vs the previous weight")


class CostModel(FrozenModel):
    transaction_cost_bps: float = Field(10.0, ge=0,
                                        description="One-way cost per unit "
                                                    "of turnover, in bps")


@runtime_checkable
class PortfolioOptimizer(Protocol):
    optimizer_id: str
    optimizer_version: str

    def optimize(self, view: PortfolioView, constraints: PortfolioConstraints,
                 costs: CostModel, previous_weight: float) -> Dict[str, Any]: ...


def _cvar(sample_returns: List[float], alpha: float = 0.95) -> float:
    """Positive tail-loss magnitude at level alpha (0 when tail is profitable)."""
    xs = sorted(sample_returns)
    k = max(1, int(len(xs) * (1 - alpha)))
    tail = xs[:k]
    return max(0.0, -(sum(tail) / len(tail)))


# ---------------------------------------------------------------------------
# Reference implementation (dependency-free, deterministic)
# ---------------------------------------------------------------------------
class ReferenceCvarOptimizer:
    """Grid search over w maximizing E[r]·w − λ·CVaR₉₅·w − cost·|Δw|."""

    optimizer_id = "reference_cvar"
    optimizer_version = "1.0.0"

    def __init__(self, risk_aversion: float = 2.0, grid_step: float = 0.01):
        if risk_aversion < 0 or not 0 < grid_step <= 0.25:
            raise PlaneError("invalid reference optimizer parameters")
        self.risk_aversion = risk_aversion
        self.grid_step = grid_step

    def optimize(self, view: PortfolioView, constraints: PortfolioConstraints,
                 costs: CostModel, previous_weight: float = 0.0
                 ) -> Dict[str, Any]:
        cvar = _cvar(view.sample_returns)
        cost_rate = costs.transaction_cost_bps / 1e4
        lo = max(constraints.min_weight,
                 previous_weight - constraints.max_turnover)
        hi = min(constraints.max_weight,
                 previous_weight + constraints.max_turnover)
        best_w, best_score = 0.0, float("-inf")
        steps = int(round((1.0 / self.grid_step))) + 1
        for i in range(steps):
            w = round(i * self.grid_step, 6)
            if not lo - 1e-12 <= w <= hi + 1e-12:
                continue
            score = (view.expected_return * w
                     - self.risk_aversion * cvar * w
                     - cost_rate * abs(w - previous_weight))
            if score > best_score + 1e-15:  # deterministic: ties → lower w
                best_w, best_score = w, score
        turnover = abs(best_w - previous_weight)
        gross = view.expected_return * best_w
        net = gross - cost_rate * turnover
        trace = [
            {"step": "view", "detail": f"{view.instrument}: E[r]="
             f"{view.expected_return:+.5f}, dispersion={view.dispersion:.5f}, "
             f"q05={view.q05:+.5f}, q95={view.q95:+.5f} "
             f"({len(view.sample_returns)} samples)"},
            {"step": "risk_measure", "detail": f"CVaR95={cvar:.5f}, "
             f"risk_aversion={self.risk_aversion}"},
            {"step": "constraints", "detail": f"weight in [{lo:.2f}, {hi:.2f}] "
             f"(min={constraints.min_weight}, max={constraints.max_weight}, "
             f"max_turnover={constraints.max_turnover}, "
             f"previous={previous_weight})"},
            {"step": "costs", "detail": f"{costs.transaction_cost_bps} bps "
             f"one-way on turnover"},
            {"step": "decision", "detail": f"w={best_w:.4f} "
             f"(objective={best_score:+.6f}); gross E[r]·w={gross:+.5f}, "
             f"net of declared costs={net:+.5f}"},
        ]
        return {
            "optimizer_id": self.optimizer_id,
            "optimizer_version": self.optimizer_version,
            "adapter_version": PORTFOLIO_ADAPTER_VERSION,
            "optimizer_config": {"risk_aversion": self.risk_aversion,
                                 "grid_step": self.grid_step},
            "weights": {view.instrument: best_w, "CASH": round(1 - best_w, 6)},
            "risky_weight": best_w,
            "previous_weight": previous_weight,
            "turnover": round(turnover, 6),
            "risk_measure": "CVaR95",
            "cvar95": round(cvar, 8),
            "objective": "maximize E[r]*w - lambda*CVaR95*w - cost*|dw|",
            "gross_expected_return": round(gross, 8),
            "net_expected_return": round(net, 8),
            "constraints": constraints.model_dump(mode="json"),
            "cost_model": costs.model_dump(mode="json"),
            "decision_trace": trace,
        }


# ---------------------------------------------------------------------------
# skfolio adapter (optional dependency)
# ---------------------------------------------------------------------------
class SkfolioCvarOptimizer:
    """skfolio MeanRisk/CVaR behind the same PortfolioOptimizer contract.

    Fits partial-budget long-only weights on the forecast *sample step
    returns* (each path step return is one observation). A skfolio
    refusal to allocate (all returns underperform the risk-free rate) is
    an explicit, recorded zero-allocation decision.
    """

    optimizer_id = "skfolio_cvar"
    optimizer_version = "1.0.0"

    def optimize(self, view: PortfolioView, constraints: PortfolioConstraints,
                 costs: CostModel, previous_weight: float = 0.0
                 ) -> Dict[str, Any]:
        try:
            import numpy as np
            import pandas as pd
            import skfolio
            from skfolio import RiskMeasure
            from skfolio.optimization import MeanRisk, ObjectiveFunction
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise PlaneError(
                "skfolio is not installed; use the reference_cvar optimizer "
                "or install skfolio") from exc

        samples = np.asarray(view.sample_returns, dtype=float)
        # per-horizon-step return scale for the observation matrix
        step_returns = np.sign(1 + samples) * (
            np.abs(1 + samples) ** (1.0 / view.horizon)) - 1.0
        frame = pd.DataFrame({view.instrument: step_returns})
        refused = None
        try:
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_RATIO,
                risk_measure=RiskMeasure.CVAR,
                budget=None, min_budget=0.0, max_budget=1.0,
                min_weights=constraints.min_weight,
                max_weights=constraints.max_weight,
                transaction_costs=costs.transaction_cost_bps / 1e4,
                previous_weights=previous_weight)
            model.fit(frame)
            weight = float(model.weights_[0])
        except ValueError as exc:
            refused = str(exc)
            weight = 0.0
        # enforce the turnover constraint explicitly (recorded, not silent)
        lo = max(constraints.min_weight,
                 previous_weight - constraints.max_turnover)
        hi = min(constraints.max_weight,
                 previous_weight + constraints.max_turnover)
        clamped = min(max(weight, lo), hi)
        weight_final = round(clamped, 6)

        cvar = _cvar(view.sample_returns)
        turnover = abs(weight_final - previous_weight)
        cost_rate = costs.transaction_cost_bps / 1e4
        gross = view.expected_return * weight_final
        net = gross - cost_rate * turnover
        trace = [
            {"step": "view", "detail": f"{view.instrument}: E[r]="
             f"{view.expected_return:+.5f} over horizon {view.horizon}"},
            {"step": "optimizer", "detail": f"skfolio {skfolio.__version__} "
             "MeanRisk(MAXIMIZE_RATIO, CVaR, partial budget 0..1)"},
            {"step": "risk_measure", "detail": f"CVaR95={cvar:.5f}"},
            {"step": "costs", "detail": f"{costs.transaction_cost_bps} bps "
             "passed to skfolio transaction_costs"},
        ]
        if refused is not None:
            trace.append({"step": "decision",
                          "detail": f"skfolio declined to allocate "
                                    f"({refused[:120]}…) → w=0 recorded "
                                    "as an explicit zero-allocation decision"})
        if abs(clamped - weight) > 1e-12:
            trace.append({"step": "constraints",
                          "detail": f"turnover clamp applied: {weight:.4f} → "
                                    f"{weight_final:.4f} (max_turnover="
                                    f"{constraints.max_turnover})"})
        trace.append({"step": "decision",
                      "detail": f"w={weight_final:.4f}; gross={gross:+.5f}, "
                                f"net of declared costs={net:+.5f}"})
        return {
            "optimizer_id": self.optimizer_id,
            "optimizer_version": self.optimizer_version,
            "adapter_version": PORTFOLIO_ADAPTER_VERSION,
            "optimizer_config": {"skfolio_version": skfolio.__version__,
                                 "objective": "MAXIMIZE_RATIO",
                                 "risk_measure": "CVAR"},
            "weights": {view.instrument: weight_final,
                        "CASH": round(1 - weight_final, 6)},
            "risky_weight": weight_final,
            "previous_weight": previous_weight,
            "turnover": round(turnover, 6),
            "risk_measure": "CVaR95",
            "cvar95": round(cvar, 8),
            "objective": "skfolio MeanRisk maximize ratio (CVaR)",
            "gross_expected_return": round(gross, 8),
            "net_expected_return": round(net, 8),
            "constraints": constraints.model_dump(mode="json"),
            "cost_model": costs.model_dump(mode="json"),
            "refused": refused,
            "decision_trace": trace,
        }


PORTFOLIO_OPTIMIZERS: Dict[str, Any] = {
    "reference_cvar": ReferenceCvarOptimizer,
    "skfolio_cvar": SkfolioCvarOptimizer,
}


def get_optimizer(optimizer_id: str,
                  params: Optional[Dict[str, Any]] = None) -> PortfolioOptimizer:
    if optimizer_id not in PORTFOLIO_OPTIMIZERS:
        raise PlaneError(f"unknown optimizer {optimizer_id!r}; available: "
                         f"{sorted(PORTFOLIO_OPTIMIZERS)}")
    return PORTFOLIO_OPTIMIZERS[optimizer_id](**(params or {}))


def make_portfolio_artifact(graph: ArtifactGraph, *,
                            forecast_artifact: ResearchArtifact,
                            view: PortfolioView,
                            optimizer: PortfolioOptimizer,
                            constraints: PortfolioConstraints,
                            costs: CostModel,
                            previous_weight: float = 0.0) -> ResearchArtifact:
    """Optimize and register the portfolio node (parent: forecast).

    The portfolio's identity covers its inputs — a researcher can answer
    "why did this weight exist?" from the artifact alone.
    """
    result = optimizer.optimize(view, constraints, costs, previous_weight)
    result["forecast_artifact_hash"] = forecast_artifact.artifact_hash
    result["view"] = view.model_dump(mode="json")
    constraints_hash = hashlib.sha256(
        canonical_json(constraints.model_dump(mode="json")).encode()).hexdigest()
    return graph.register(
        "portfolio", result,
        config={"optimizer_id": result["optimizer_id"],
                "optimizer_version": result["optimizer_version"],
                "optimizer_config": result["optimizer_config"],
                "constraints_hash": constraints_hash,
                "cost_model": costs.model_dump(mode="json"),
                "previous_weight": previous_weight},
        parents=[forecast_artifact])
