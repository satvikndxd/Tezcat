"""Research-only transformations over normalized external observations."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar

from tezcat.external.schemas import (
    Event, EventMatch, EventSignature, OrderbookSnapshot, Quote, Trade,
    checksum, normalize_timestamp, signature_identity, utc_now,
)


_T = TypeVar("_T")
_STOPWORDS = {"will", "the", "a", "an", "of", "to", "in", "on", "for", "before", "after", "by"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in _STOPWORDS}


def match_events(event_a: Event, event_b: Event, *, origin: str = "algorithmic") -> EventMatch:
    """Create a cautious research-only equivalence record.

    This function never claims economic equivalence from a small text overlap;
    it records the level and rationale so a researcher can confirm/reject it.
    """
    a = _tokens(event_a.title + " " + (event_a.description or ""))
    b = _tokens(event_b.title + " " + (event_b.description or ""))
    union = a | b
    overlap = len(a & b) / len(union) if union else 0.0
    same_title = event_a.title.strip().casefold() == event_b.title.strip().casefold()
    if event_a.provider == event_b.provider and event_a.event_id == event_b.event_id:
        level, confidence, rationale = "EXACT", 1.0, "same provider and event identifier"
    elif same_title and (event_a.category or "") == (event_b.category or ""):
        level, confidence, rationale = "EXACT", 0.98, "identical title and matching category"
    elif overlap >= 0.78:
        level, confidence, rationale = "LIKELY_EQUIVALENT", round(min(0.95, overlap), 4), f"token overlap={overlap:.3f}; review resolution rules and expiry"
    elif overlap >= 0.45:
        level, confidence, rationale = "POSSIBLY_RELATED", round(overlap, 4), f"partial token overlap={overlap:.3f}; wording alone cannot establish equivalence"
    else:
        level, confidence, rationale = "UNMATCHED", round(overlap, 4), f"insufficient title overlap={overlap:.3f}"
    return EventMatch(
        match_id=f"match_{checksum({'a': event_a.provider + ':' + event_a.event_id, 'b': event_b.provider + ':' + event_b.event_id})[:16]}",
        event_a={"provider": event_a.provider, "event_id": event_a.event_id},
        event_b={"provider": event_b.provider, "event_id": event_b.event_id},
        level=level, confidence=confidence, rationale=rationale,
        origin="human" if origin == "human" else "algorithmic", created_at=utc_now(),
    )


def probability_divergence(probability_a: float, probability_b: float) -> Dict[str, Any]:
    """Return descriptive divergence; never labels it arbitrage or causality."""
    signed = float(probability_b) - float(probability_a)
    return {
        "label": "cross-provider probability divergence",
        "provider_a_probability": float(probability_a),
        "provider_b_probability": float(probability_b),
        "signed_difference": signed,
        "absolute_difference": abs(signed),
        "units": "probability points",
        "interpretation": "descriptive difference; contractual equivalence and causality are not established",
    }


def _window_filter(rows: Sequence[_T], timestamp_fn, start: Optional[str], end: Optional[str]) -> List[_T]:
    if not start and not end:
        return list(rows)
    start_dt = datetime.fromisoformat(normalize_timestamp(start).replace("Z", "+00:00")) if start else None
    end_dt = datetime.fromisoformat(normalize_timestamp(end).replace("Z", "+00:00")) if end else None
    out: List[T] = []
    for row in rows:
        dt = datetime.fromisoformat(normalize_timestamp(timestamp_fn(row)).replace("Z", "+00:00"))
        if start_dt and dt < start_dt:
            continue
        if end_dt and dt > end_dt:
            continue
        out.append(row)
    return out


def extract_event_signature(*, provider: str, dataset_id: str, market_id: str,
                            quotes: Sequence[Quote] = (),
                            trades: Sequence[Trade] = (),
                            books: Sequence[OrderbookSnapshot] = (),
                            window: Optional[Dict[str, str]] = None) -> EventSignature:
    """Extract a conservative event signature from normalized observations.

    Missing provider fields remain null. Probability features use raw bounded
    probability deltas; no logit or return transform is silently applied.
    """
    window = dict(window or {})
    quotes = _window_filter(quotes, lambda row: row.timestamp, window.get("start"), window.get("end"))
    trades = _window_filter(trades, lambda row: row.timestamp, window.get("start"), window.get("end"))
    books = _window_filter(books, lambda row: row.timestamp, window.get("start"), window.get("end"))
    probs = [row.implied_probability if row.implied_probability is not None else row.mid for row in quotes]
    probs = [float(value) for value in probs if value is not None]
    pre = probs[0] if probs else None
    post = probs[-1] if probs else None
    peak = max(probs) if probs else None
    delta = (post - pre) if pre is not None and post is not None else None
    changes = [probs[i] - probs[i - 1] for i in range(1, len(probs))]
    if len(changes) > 1:
        mean = sum(changes) / len(changes)
        volatility = math.sqrt(sum((value - mean) ** 2 for value in changes) / (len(changes) - 1))
    else:
        volatility = None
    jumps = [abs(value) for value in changes if abs(value) >= 0.05]
    spreads = [row.spread for row in quotes if row.spread is not None]
    depths = [row.depth for row in books if row.depth is not None]
    volumes = [row.quantity for row in trades]
    payload: Dict[str, Any] = {
        "provider": provider, "dataset_id": dataset_id, "market_id": market_id,
        "window": window, "pre_event_probability": pre, "post_event_probability": post,
        "delta_probability": delta, "peak_probability": peak,
        "probability_volatility": volatility, "jump_frequency": (len(jumps) / len(changes) if changes else None),
        "pre_event_spread": spreads[0] if spreads else None,
        "post_event_spread": spreads[-1] if spreads else None,
        "volume_change": (volumes[-1] / volumes[0] if volumes and volumes[0] else None),
        "depth_change": (depths[-1] / depths[0] - 1.0 if depths and depths[0] else None),
        "activity_intensity": (len(trades) / max(1, len(quotes))),
        "transform": "raw_probability_delta",
    }
    sig_id = signature_identity(payload)
    return EventSignature(
        signature_id=sig_id, checksum=checksum(payload), provider=provider,
        dataset_id=dataset_id, market_id=market_id, window=window,
        pre_event_probability=pre, post_event_probability=post,
        delta_probability=delta, peak_probability=peak,
        probability_volatility=volatility,
        jump_frequency=payload["jump_frequency"],
        pre_event_spread=payload["pre_event_spread"],
        post_event_spread=payload["post_event_spread"],
        volume_change=payload["volume_change"], depth_change=payload["depth_change"],
        activity_intensity=payload["activity_intensity"],
        metadata={"data_class": "INFERRED", "source_observations": {"quotes": len(quotes), "trades": len(trades), "books": len(books)}},
    )


def compare_signatures(observed: EventSignature, synthetic: Dict[str, Any]) -> Dict[str, Any]:
    """Compare supported features descriptively, preserving nulls and labels."""
    features = [
        "probability_volatility", "jump_frequency", "activity_intensity",
        "pre_event_spread", "post_event_spread", "volume_change", "depth_change",
    ]
    rows = []
    errors = []
    for feature in features:
        left = getattr(observed, feature)
        right = synthetic.get(feature)
        error = abs(float(left) - float(right)) if left is not None and right is not None else None
        if error is not None:
            errors.append(error)
        rows.append({"feature": feature, "observed": left, "synthetic": right, "match": error})
    return {
        "label": "observed-versus-synthetic comparison",
        "data_classes": {"observed": "OBSERVED", "synthetic": "SYNTHETIC", "match": "INFERRED"},
        "features": rows,
        "mean_absolute_error": sum(errors) / len(errors) if errors else None,
        "limitations": ["comparison is descriptive", "missing provider fields remain unavailable", "no causal claim is implied"],
    }
