"""Forecasting desk (Phase S5-B): provider-neutral forecast contract.

The plane defines an abstract :class:`ForecastModel` and ships one
deterministic **reference implementation** (a seeded block bootstrap of
historical log returns). Kronos — or any other model — plugs in by
implementing the same protocol and registering under its own model id;
nothing in Tezcat semantics is tied to a specific vendor model, and no
forecasting dependency touches the kernel.

Contract rules:

* **A forecast is a probabilistic research observation, not an order.**
  Nothing in this module (or anywhere downstream) converts a forecast
  into an order; forecasts feed *portfolio research*.
* **Uncertainty is first-class.** A forecast payload must carry sampled
  paths and derived quantiles/dispersion — a bare
  ``expected_return = 0.034`` is not a valid forecast artifact.
* **Quality ≠ trading value.** :func:`evaluate_forecast` scores
  calibration/accuracy against realized data and stores the result as a
  separate ``forecast_quality`` artifact; portfolio performance lives
  elsewhere and the two are never merged.

The reference model resamples the supplied history; it makes **no
predictive claim** about real markets and says so in its own payload.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from tezcat.plane.artifacts import ArtifactGraph, PlaneError, ResearchArtifact

FORECAST_ADAPTER_VERSION = 1


@runtime_checkable
class ForecastModel(Protocol):
    """Provider-neutral forecasting interface (Kronos-shaped, optional)."""

    model_id: str
    model_version: str

    def forecast(self, prices: Sequence[float], horizon: int,
                 n_paths: int, seed: int) -> Dict[str, Any]:
        """Return a distributional forecast payload (see reference impl)."""
        ...


def _quantile(sorted_xs: List[float], q: float) -> float:
    idx = q * (len(sorted_xs) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_xs) - 1)
    frac = idx - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


class SeededBlockBootstrapModel:
    """Deterministic reference model: block bootstrap of log returns.

    Resamples contiguous blocks of the historical log-return series to
    build ``n_paths`` forward paths. Same inputs + same seed ⇒ identical
    forecast. This is a *research baseline* — resampled history with no
    predictive claim — existing so the forecast→portfolio→execution seam
    can be exercised end to end without a heavyweight model dependency.
    """

    model_id = "bootstrap_reference"
    model_version = "1.0.0"

    def __init__(self, block_size: int = 10):
        if block_size < 1:
            raise PlaneError("block_size must be >= 1")
        self.block_size = block_size

    def forecast(self, prices: Sequence[float], horizon: int,
                 n_paths: int, seed: int) -> Dict[str, Any]:
        if len(prices) < max(30, self.block_size + 1):
            raise PlaneError(
                f"history too short for forecasting: {len(prices)} prices")
        if horizon < 1 or n_paths < 20:
            raise PlaneError("horizon must be >= 1 and n_paths >= 20 — a "
                             "distribution needs samples")
        returns = [math.log(b / a) for a, b in zip(prices, prices[1:])
                   if a > 0 and b > 0]
        rng = random.Random(seed)
        last = float(prices[-1])

        paths: List[List[float]] = []
        for _ in range(n_paths):
            path, price = [], last
            while len(path) < horizon:
                start = rng.randrange(0, len(returns) - self.block_size + 1) \
                    if len(returns) > self.block_size else 0
                for r in returns[start:start + self.block_size]:
                    price *= math.exp(r)
                    path.append(round(price, 6))
                    if len(path) == horizon:
                        break
            paths.append(path)

        terminal_returns = sorted(p[-1] / last - 1.0 for p in paths)
        n = len(terminal_returns)
        mean_r = sum(terminal_returns) / n
        dispersion = (sum((r - mean_r) ** 2 for r in terminal_returns)
                      / (n - 1)) ** 0.5
        step_quantiles = []
        for step in range(horizon):
            vals = sorted(p[step] for p in paths)
            step_quantiles.append({
                "q05": round(_quantile(vals, 0.05), 6),
                "q50": round(_quantile(vals, 0.50), 6),
                "q95": round(_quantile(vals, 0.95), 6),
            })
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "adapter_version": FORECAST_ADAPTER_VERSION,
            "model_config": {"block_size": self.block_size},
            "horizon": horizon,
            "n_paths": n_paths,
            "seed": seed,
            "last_price": last,
            "forecast_paths": paths,
            "terminal": {
                "mean_return": round(mean_r, 8),
                "median_return": round(_quantile(terminal_returns, 0.5), 8),
                "dispersion": round(dispersion, 8),
                "q05": round(_quantile(terminal_returns, 0.05), 8),
                "q25": round(_quantile(terminal_returns, 0.25), 8),
                "q75": round(_quantile(terminal_returns, 0.75), 8),
                "q95": round(_quantile(terminal_returns, 0.95), 8),
                "prob_up": round(sum(r > 0 for r in terminal_returns) / n, 6),
            },
            "step_quantiles": step_quantiles,
            "disclaimer": "reference research model: block-bootstrap of the "
                          "supplied history — a probabilistic research "
                          "observation, NOT a prediction of real markets "
                          "and NOT an order",
        }


#: Model registry. Additional models (e.g. a Kronos adapter) register
#: here by implementing ForecastModel; the plane never hard-codes one.
FORECAST_MODELS: Dict[str, Any] = {
    "bootstrap_reference": SeededBlockBootstrapModel,
}


def get_model(model_id: str, params: Optional[Dict[str, Any]] = None
              ) -> ForecastModel:
    if model_id not in FORECAST_MODELS:
        raise PlaneError(f"unknown forecast model {model_id!r}; available: "
                         f"{sorted(FORECAST_MODELS)} (external models plug "
                         "in via the ForecastModel protocol)")
    return FORECAST_MODELS[model_id](**(params or {}))


def make_forecast_artifact(graph: ArtifactGraph, *,
                           dataset_artifact: ResearchArtifact,
                           prices: Sequence[float], instrument: str,
                           model: ForecastModel, horizon: int,
                           n_paths: int, seed: int) -> ResearchArtifact:
    """Run the model and register the forecast node (parent: dataset)."""
    payload = model.forecast(prices, horizon, n_paths, seed)
    payload["instrument"] = instrument
    payload["dataset_artifact_hash"] = dataset_artifact.artifact_hash
    return graph.register(
        "forecast", payload,
        config={"model_id": model.model_id,
                "model_version": model.model_version,
                "model_config": payload["model_config"],
                "horizon": horizon, "n_paths": n_paths, "seed": seed,
                "instrument": instrument},
        parents=[dataset_artifact])


# ---------------------------------------------------------------------------
# Forecast quality (stored separately from portfolio performance)
# ---------------------------------------------------------------------------
def evaluate_forecast(forecast_payload: Dict[str, Any],
                      realized_prices: Sequence[float]) -> Dict[str, Any]:
    """Score a forecast against realized data. Quality ≠ trading value.

    Metrics: MAE/RMSE of the median path, directional accuracy of the
    terminal move, q05–q95 interval coverage per step, and a sample-based
    CRPS at the terminal step (CRPS ≈ E|X−y| − ½E|X−X′|).
    """
    horizon = forecast_payload["horizon"]
    if len(realized_prices) < horizon:
        raise PlaneError(f"realized series has {len(realized_prices)} points; "
                         f"forecast horizon is {horizon}")
    realized = [float(p) for p in realized_prices[:horizon]]
    medians = [sq["q50"] for sq in forecast_payload["step_quantiles"]]
    errors = [m - r for m, r in zip(medians, realized)]
    mae = sum(abs(e) for e in errors) / horizon
    rmse = (sum(e * e for e in errors) / horizon) ** 0.5
    covered = sum(sq["q05"] <= r <= sq["q95"] for sq, r in
                  zip(forecast_payload["step_quantiles"], realized))
    last = forecast_payload["last_price"]
    realized_dir = realized[-1] > last
    forecast_dir = forecast_payload["terminal"]["median_return"] > 0

    samples = sorted(p[-1] for p in forecast_payload["forecast_paths"])
    y = realized[-1]
    n = len(samples)
    e_xy = sum(abs(x - y) for x in samples) / n
    e_xx = sum(abs(samples[i] - samples[j])
               for i in range(n) for j in range(i + 1, n)) * 2 / (n * n)
    crps = e_xy - 0.5 * e_xx

    return {
        "horizon": horizon,
        "mae_median_path": round(mae, 8),
        "rmse_median_path": round(rmse, 8),
        "interval_coverage_q05_q95": round(covered / horizon, 6),
        "nominal_interval": 0.90,
        "directional_hit": bool(realized_dir == forecast_dir),
        "crps_terminal": round(crps, 8),
        "note": "forecast quality is stored separately from portfolio "
                "performance; directional accuracy does not imply "
                "financial usefulness",
    }
