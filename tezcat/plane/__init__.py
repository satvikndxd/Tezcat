"""Research Control Plane (Phase S5): the quant research operating layer.

Tezcat's differentiated asset is the **seam between specialized systems**:
typed contracts, one provenance graph, and controlled counterfactuals
across the whole quantitative workflow —

    Observation → Hypothesis → Forecast → Portfolio Decision
        → Market World → Execution → Risk → Counterfactual → Reproduction

Non-negotiable architectural properties:

* **The kernel stays frozen.** Forecasting, portfolio optimization,
  Nautilus, and any AI layer live behind optional adapters. If every
  external integration disappeared, the synthetic-market laboratory
  still works: nothing in ``tezcat.core``/``tezcat.engine`` imports this
  package or its optional dependencies.
* **One provenance system.** The artifact graph *wraps* existing Tezcat
  identities (research hashes, dataset hashes, world hashes, lab result
  ids) as ``external_identity`` on graph nodes — it never replaces them.
* **A forecast is a probabilistic research observation, not an order.**
  There is no forecast→order path anywhere in this package; forecasts
  feed portfolio *research*, portfolios feed backtest *sizing*, and
  execution stays inside the S4 backtest-only bridge.
* **Uncertainty is first-class.** Forecast artifacts carry distributions
  (paths, quantiles, dispersion), and forecast *quality* is stored
  separately from portfolio *performance* — prediction accuracy is never
  conflated with trading value.
"""

from tezcat.plane.artifacts import (  # noqa: F401
    ARTIFACT_TYPES, PLANE_SCHEMA_VERSION, ArtifactGraph, PlaneError,
    ResearchArtifact, environment_fingerprint,
)
