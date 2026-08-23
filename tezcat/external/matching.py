"""Cross-provider event matching and probability divergence (Phase S3).

Superficially similar prediction markets can differ in resolution rules,
expiration, wording, underlying event, source of truth, and settlement
timing. Equivalence is therefore **never claimed automatically**:

* Algorithmic matching can propose at most ``LIKELY_EQUIVALENT``.
* ``EXACT`` requires a human confirmation with a recorded rationale.
* Every match stores rationale, confidence, origin, timestamp, and
  version, and can be confirmed or rejected by a researcher.

For matched markets the module computes **cross-provider probability
divergence** — deliberately *not* called "arbitrage": until contract
equivalence is verified, a price difference may reflect genuine
contractual differences rather than mispricing. Lead/lag measurements are
labeled observational; moving first is not "price discovery proof".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import Field

from tezcat.core.config import FrozenModel
from tezcat.external.schema import ExternalMarket

MATCHER_VERSION = 1


class MatchError(ValueError):
    pass


class MatchLevel(str, Enum):
    EXACT = "exact"                          # human-confirmed only
    LIKELY_EQUIVALENT = "likely_equivalent"  # strongest algorithmic level
    POSSIBLY_RELATED = "possibly_related"
    UNMATCHED = "unmatched"


class EventMatch(FrozenModel):
    """A recorded (never assumed) relation between two provider markets."""

    match_id: str
    matcher_version: int = MATCHER_VERSION
    provider_a: str
    market_a: str
    provider_b: str
    market_b: str
    level: MatchLevel
    confidence: float = Field(..., ge=0, le=1)
    rationale: str = Field(..., min_length=1)
    origin: str = Field(..., description="'algorithmic' or 'human'")
    created_at: str = Field(..., min_length=1)
    status: str = Field("proposed", description="proposed|confirmed|rejected")
    review_note: str = ""

    def key(self) -> str:
        return f"{self.provider_a}:{self.market_a}↔{self.provider_b}:{self.market_b}"


# ---------------------------------------------------------------------------
# Algorithmic proposal (research aid, not a verdict)
# ---------------------------------------------------------------------------
_STOPWORDS = {"will", "the", "a", "an", "of", "in", "on", "at", "by", "to",
              "be", "is", "before", "after", "than", "or", "and", "what",
              "probability", "happen", "occur"}


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower())
            if t not in _STOPWORDS and len(t) > 1}


def propose_match(a: ExternalMarket, b: ExternalMarket, *,
                  now: str) -> EventMatch:
    """Token-overlap + close-time heuristic proposal.

    Caps at LIKELY_EQUIVALENT by construction: only a human review can
    promote a match to EXACT, because wording similarity says nothing
    about resolution rules or settlement sources.
    """
    if a.provider == b.provider:
        raise MatchError("cross-provider matching requires distinct providers")
    ta, tb = _tokens(a.question), _tokens(b.question)
    if not ta or not tb:
        overlap = 0.0
    else:
        overlap = len(ta & tb) / len(ta | tb)  # Jaccard

    same_close = bool(a.close_time and b.close_time
                      and a.close_time[:10] == b.close_time[:10])
    score = overlap + (0.15 if same_close else 0.0)

    if score >= 0.6:
        level, conf = MatchLevel.LIKELY_EQUIVALENT, min(0.9, score)
    elif score >= 0.3:
        level, conf = MatchLevel.POSSIBLY_RELATED, score
    else:
        level, conf = MatchLevel.UNMATCHED, 1.0 - score

    rationale = (f"algorithmic: question token Jaccard={overlap:.2f}"
                 + ("; close dates agree" if same_close else
                    "; close dates differ/unknown")
                 + " — resolution rules NOT compared; equivalence unverified")
    digest = hashlib.sha256(
        f"{a.provider}:{a.market_id}|{b.provider}:{b.market_id}".encode()
    ).hexdigest()
    return EventMatch(
        match_id=f"exm_{digest[:12]}",
        provider_a=a.provider, market_a=a.market_id,
        provider_b=b.provider, market_b=b.market_id,
        level=level, confidence=round(conf, 4), rationale=rationale,
        origin="algorithmic", created_at=now, status="proposed")


# ---------------------------------------------------------------------------
# Match store (researcher confirm/reject)
# ---------------------------------------------------------------------------
class MatchStore:
    def __init__(self, root: str = "data"):
        self.root = Path(root) / "external" / "matches"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, match_id: str) -> Path:
        return self.root / f"{match_id}.json"

    def save(self, match: EventMatch) -> EventMatch:
        with self._lock:
            path = self._path(match.match_id)
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            with open(tmp, "w") as f:
                json.dump(match.model_dump(mode="json"), f, indent=1)
            os.replace(tmp, path)
        return match

    def get(self, match_id: str) -> EventMatch:
        path = self._path(match_id)
        if not path.exists():
            raise MatchError(f"unknown match {match_id!r}")
        with open(path) as f:
            return EventMatch.model_validate(json.load(f))

    def list(self) -> List[EventMatch]:
        out = []
        for p in sorted(self.root.glob("exm_*.json")):
            with open(p) as f:
                out.append(EventMatch.model_validate(json.load(f)))
        return out

    def review(self, match_id: str, verdict: str, note: str,
               now: str, promote_to_exact: bool = False) -> EventMatch:
        """Human confirmation/rejection; only this path can mint EXACT."""
        if verdict not in ("confirmed", "rejected"):
            raise MatchError("verdict must be 'confirmed' or 'rejected'")
        if not note.strip():
            raise MatchError("a review requires a written rationale note")
        match = self.get(match_id)
        level = match.level
        if promote_to_exact:
            if verdict != "confirmed":
                raise MatchError("cannot promote a rejected match to EXACT")
            level = MatchLevel.EXACT
        updated = match.model_copy(update={
            "status": verdict, "level": level, "origin": "human",
            "review_note": f"[{now}] {note}"})
        return self.save(updated)


# ---------------------------------------------------------------------------
# Cross-provider probability divergence
# ---------------------------------------------------------------------------
def divergence_series(series_a: List[Tuple[str, float]],
                      series_b: List[Tuple[str, float]],
                      threshold: float = 0.02) -> Dict[str, Any]:
    """Divergence between two aligned (timestamp, probability) series.

    Alignment is by exact timestamp key intersection — no interpolation
    (interpolating one provider onto the other's clock would fabricate
    observations). Output is labeled **cross-provider probability
    divergence**; whether it is exploitable or even meaningful depends on
    contract equivalence, which this function does not judge.
    """
    a = dict(series_a)
    b = dict(series_b)
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        raise MatchError(
            f"only {len(common)} aligned timestamps between the series; "
            "divergence requires >= 2 (no interpolation is performed)")
    signed = [a[t] - b[t] for t in common]
    absd = [abs(v) for v in signed]
    n = len(common)

    # persistence: longest run with |divergence| >= threshold
    longest = run = 0
    converged_at: Optional[str] = None
    for t, v in zip(common, absd):
        run = run + 1 if v >= threshold else 0
        longest = max(longest, run)
    # convergence time: first timestamp after peak where |d| < threshold
    peak_idx = max(range(n), key=lambda i: absd[i])
    for i in range(peak_idx, n):
        if absd[i] < threshold:
            converged_at = common[i]
            break

    return {
        "label": "cross-provider probability divergence "
                 "(not arbitrage: contract equivalence unverified unless the "
                 "match is human-confirmed EXACT)",
        "n_aligned": n,
        "threshold": threshold,
        "mean_abs_divergence": sum(absd) / n,
        "mean_signed_divergence": sum(signed) / n,
        "peak_abs_divergence": max(absd),
        "peak_at": common[peak_idx],
        "longest_divergent_run": longest,
        "converged_at": converged_at,
        "final_abs_divergence": absd[-1],
        "series": [{"timestamp": t, "a": a[t], "b": b[t], "signed": a[t] - b[t]}
                   for t in common],
    }


def lead_lag(series_a: List[Tuple[str, float]],
             series_b: List[Tuple[str, float]],
             max_lag: int = 10) -> Dict[str, Any]:
    """Lagged cross-correlation of probability increments.

    Purely observational: a positive best lag means changes in A tend to
    precede similar changes in B *in this sample*. This is a **lead/lag
    observation**, not price-discovery proof — no causal identification
    strategy is applied here.
    """
    a = dict(series_a)
    b = dict(series_b)
    common = sorted(set(a) & set(b))
    if len(common) < max_lag + 12:
        raise MatchError(
            f"{len(common)} aligned timestamps is too few for lead/lag with "
            f"max_lag={max_lag}")
    da = [a[t2] - a[t1] for t1, t2 in zip(common, common[1:])]
    db = [b[t2] - b[t1] for t1, t2 in zip(common, common[1:])]

    def corr(x: List[float], y: List[float]) -> Optional[float]:
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        vx = sum((v - mx) ** 2 for v in x)
        vy = sum((v - my) ** 2 for v in y)
        if vx == 0 or vy == 0:
            return None
        cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        return cov / (vx * vy) ** 0.5

    rows = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:  # a leads b by `lag`
            x, y = da[:len(da) - lag or None], db[lag:]
        else:
            x, y = da[-lag:], db[:len(db) + lag]
        c = corr(x, y) if len(x) >= 10 else None
        rows.append({"lag": lag, "correlation": c, "n": len(x)})
    usable = [r for r in rows if r["correlation"] is not None]
    best = max(usable, key=lambda r: r["correlation"]) if usable else None
    return {
        "label": "lead/lag observation (no causal claim)",
        "increments": "delta (Δp)",
        "max_lag": max_lag,
        "correlations": rows,
        "best_lag": best["lag"] if best else None,
        "best_correlation": best["correlation"] if best else None,
        "interpretation": ("positive best_lag: series A's changes tended to "
                           "precede series B's in this sample — an "
                           "observational association only"),
    }
