"""Event signatures and bounded-probability stylized facts (Phase S3).

The bridge object between an observed external market and a synthetic
experiment is the **event signature**: a versioned, hashable feature
vector extracted from a declared observation window. Signatures describe
*what the data shows*; they never claim *why*.

Transform policy (documented, per Section "probability returns")
----------------------------------------------------------------
Prediction-market probabilities are **bounded** in [0, 1]; stock-return
formulas do not transfer blindly. Two increment definitions are offered:

``delta``  Δp_t = p_t − p_{t−1}
    The default. Well-defined everywhere on [0, 1]; a "+5 pp move" means
    the same thing at p = 0.5 as at p = 0.9 in payoff terms (contracts
    settle at 0 or 1), which is the economically meaningful unit for
    event contracts.

``logit``  logit(p_t) − logit(p_{t−1})
    Optional, for tail-sensitive analyses (a 0.94→0.99 move is "larger"
    in information terms than 0.50→0.55). Undefined at p ∈ {0, 1}; this
    module **refuses** (raises) rather than clipping — clipping would
    fabricate data at exactly the points where the transform is most
    sensitive. Callers wanting logit features must restrict the window to
    interior probabilities and say so.

Volatility clustering / jump features reuse the F9 primitives (ACF) on
|Δp|. Jump threshold defaults to 0.05 (5 probability points) and is part
of the recorded transform metadata, never hidden.

Everything here is computed from **observed** rows; missing inputs yield
``None`` fields, never fabricated values.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Sequence

from pydantic import Field

from tezcat.analysis.stylized_facts import acf, excess_kurtosis
from tezcat.core.config import FrozenModel, canonical_json
from tezcat.external.schema import MarketObservation

#: Version of the signature feature definitions; participates in the
#: signature hash. Bump on any change to feature semantics.
SIGNATURE_VERSION = 1

JUMP_THRESHOLD = 0.05  # |Δp| ≥ 5 probability points counts as a jump


class SignatureError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Increments
# ---------------------------------------------------------------------------
def probability_deltas(probs: Sequence[float]) -> List[float]:
    """Δp increments of a probability series (the default transform)."""
    for i, p in enumerate(probs):
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise SignatureError(f"probability at index {i} outside [0,1]: {p!r}")
    return [b - a for a, b in zip(probs, probs[1:])]


def logit_deltas(probs: Sequence[float]) -> List[float]:
    """logit-increments; refuses boundary probabilities instead of clipping."""
    out = []
    prev: Optional[float] = None
    for i, p in enumerate(probs):
        if not 0.0 < p < 1.0:
            raise SignatureError(
                f"logit transform undefined at p={p!r} (index {i}); refusing "
                "to clip — restrict the window to interior probabilities or "
                "use the delta transform")
        lo = math.log(p / (1.0 - p))
        if prev is not None:
            out.append(lo - prev)
        prev = lo
    return out


# ---------------------------------------------------------------------------
# Bounded-probability stylized facts
# ---------------------------------------------------------------------------
def probability_features(probs: Sequence[float],
                         transform: str = "delta",
                         jump_threshold: float = JUMP_THRESHOLD
                         ) -> Dict[str, Optional[float]]:
    """Stylized-fact feature vector for a probability trajectory.

    ``transform`` is ``"delta"`` (default) or ``"logit"`` — see module
    docstring. The transform name is included in the output so no report
    can show these numbers without naming the transformation.
    """
    if transform == "delta":
        d = probability_deltas(probs)
    elif transform == "logit":
        d = logit_deltas(probs)
    else:
        raise SignatureError(f"unknown transform {transform!r}; "
                             "use 'delta' or 'logit'")
    feats: Dict[str, Any] = {"transform": transform,
                             "jump_threshold": jump_threshold,
                             "n_increments": float(len(d))}
    if len(d) < 10:
        feats["warning_too_short"] = 1.0
        return feats
    n = len(d)
    mean = sum(d) / n
    var = sum((v - mean) ** 2 for v in d) / (n - 1)
    abs_d = [abs(v) for v in d]
    feats["prob_volatility"] = math.sqrt(var)
    feats["prob_drift"] = mean
    feats["prob_excess_kurtosis"] = excess_kurtosis(d)
    feats["jump_frequency"] = sum(v >= jump_threshold for v in abs_d) / n
    feats["max_jump"] = max(abs_d)
    feats["mean_reversion_acf1"] = acf(d, 1)
    clustering = [acf(abs_d, lag) for lag in (1, 2, 5)]
    clustering = [c for c in clustering if c is not None]
    feats["volatility_clustering"] = (sum(clustering) / len(clustering)
                                      if clustering else None)
    feats["prob_range"] = max(probs) - min(probs)
    feats["terminal_probability"] = probs[-1]
    return feats


# ---------------------------------------------------------------------------
# Event signature
# ---------------------------------------------------------------------------
class EventSignature(FrozenModel):
    """Versioned, hashable description of one observed market episode.

    Only fields the observed data supports are populated; a ``None`` means
    "not observed in this window". The signature is a *description* of
    observed behavior — it carries no mechanism claim.
    """

    signature_version: int = SIGNATURE_VERSION
    dataset_id: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1)
    # window declaration (indices into the dataset's observation series)
    t0_index: int = Field(..., ge=0, description="Event-anchor observation index")
    pre_window: int = Field(..., ge=1, description="Observations before t0")
    post_window: int = Field(..., ge=1, description="Observations after t0")
    window_start: str = ""
    window_end: str = ""
    # probability trajectory
    pre_event_probability: Optional[float] = None
    post_event_probability: Optional[float] = None
    delta_probability: Optional[float] = None
    peak_probability: Optional[float] = None
    time_to_peak: Optional[int] = Field(
        None, description="Observations from t0 to the post-window extreme")
    # liquidity / activity
    pre_event_spread: Optional[float] = None
    post_event_spread: Optional[float] = None
    spread_change: Optional[float] = None
    volume_change: Optional[float] = Field(
        None, description="post/pre mean volume ratio")
    depth_change: Optional[float] = Field(
        None, description="post/pre mean depth ratio − 1")
    # stylized facts over the full window (delta transform); numeric values
    # plus the recorded 'transform' name and 'jump_threshold'
    features: Dict[str, Any] = Field(default_factory=dict)
    transform_notes: str = Field(
        "increments: delta (Δp); probabilities are market-implied proxies",
        min_length=1)

    def signature_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def extract_signature(observations: List[MarketObservation], *,
                      dataset_id: str, t0_index: int,
                      pre_window: int, post_window: int) -> EventSignature:
    """Extract an :class:`EventSignature` from a declared window.

    The window is explicit — [t0−pre_window, t0+post_window] — and must lie
    inside the observation series; silently shrinking a window would change
    the science without changing the record.
    """
    n = len(observations)
    if not 0 <= t0_index < n:
        raise SignatureError(f"t0_index {t0_index} outside series (n={n})")
    lo, hi = t0_index - pre_window, t0_index + post_window
    if lo < 0 or hi >= n:
        raise SignatureError(
            f"window [{lo}, {hi}] exceeds observation series (n={n}); "
            "declare a window that the data actually covers")
    window = observations[lo:hi + 1]
    market_id = window[0].market_id

    probs = [o.implied_probability for o in window]
    pre = [o for o in observations[lo:t0_index]]
    post = [o for o in observations[t0_index:hi + 1]]

    pre_p = next((o.implied_probability for o in reversed(pre)
                  if o.implied_probability is not None), None)
    post_p = next((o.implied_probability for o in reversed(post)
                   if o.implied_probability is not None), None)

    peak_p = time_to_peak = None
    post_probs = [(i, o.implied_probability) for i, o in enumerate(post)
                  if o.implied_probability is not None]
    if post_probs and pre_p is not None:
        # peak = extreme post-t0 probability in the direction of the move
        ref = post_p if post_p is not None else pre_p
        direction = 1.0 if ref >= pre_p else -1.0
        idx, peak_p = max(post_probs, key=lambda t: direction * t[1])
        time_to_peak = idx

    pre_spread = _mean([o.spread for o in pre if o.spread is not None])
    post_spread = _mean([o.spread for o in post if o.spread is not None])
    pre_vol = _mean([o.volume for o in pre if o.volume is not None])
    post_vol = _mean([o.volume for o in post if o.volume is not None])
    pre_depth = _mean([o.depth for o in pre if o.depth is not None])
    post_depth = _mean([o.depth for o in post if o.depth is not None])

    full_probs = [p for p in probs if p is not None]
    features = (probability_features(full_probs)
                if len(full_probs) >= 11 else {"warning_too_short": 1.0})

    return EventSignature(
        dataset_id=dataset_id, market_id=market_id,
        t0_index=t0_index, pre_window=pre_window, post_window=post_window,
        window_start=window[0].timestamp, window_end=window[-1].timestamp,
        pre_event_probability=pre_p, post_event_probability=post_p,
        delta_probability=(post_p - pre_p) if pre_p is not None
                          and post_p is not None else None,
        peak_probability=peak_p, time_to_peak=time_to_peak,
        pre_event_spread=pre_spread, post_event_spread=post_spread,
        spread_change=(post_spread - pre_spread) if pre_spread is not None
                      and post_spread is not None else None,
        volume_change=(post_vol / pre_vol) if pre_vol and post_vol is not None
                      else None,
        depth_change=(post_depth / pre_depth - 1.0) if pre_depth and
                     post_depth is not None else None,
        features=features)
