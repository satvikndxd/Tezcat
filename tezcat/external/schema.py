"""Canonical schema for external event-market observations (Phase S3).

Prediction markets are **not** equities, and this schema refuses to pretend
otherwise. The canonical concepts are:

    Provider → Event → Market → Outcome → Observation

* An **Event** is a real-world proposition ("Will X occur before date Y?").
* A **Market** is the tradable contract attached to that event.
* An **Outcome** is one settlement branch (YES/NO, or one of N outcomes).
* An **Observation** is a timestamped view of the market (quote, book,
  trade, or snapshot row).

Probability semantics
---------------------
The canonical probability is ``p ∈ [0, 1]`` and is always called an
**implied-probability proxy**: it is what the market charges for the YES
contract, not the true probability of the event. Provider-native units
(dollar strings, integer cents) are preserved verbatim next to every
canonical value, and each conversion names its rule in
``price_transform`` so the normalization is reproducible.

Missing data policy: fields a provider does not expose stay ``None``.
Nothing is fabricated; nothing is silently defaulted.

``EXTERNAL_SCHEMA_VERSION`` is part of every dataset checksum — a change to
these models is a new schema version, never a silent reinterpretation.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from tezcat.core.config import FrozenModel

#: Version of the canonical external-market schema. Bump on any change that
#: alters serialized semantics; it participates in dataset checksums.
EXTERNAL_SCHEMA_VERSION = 1


class MarketStatus(str, Enum):
    """Canonical market lifecycle state.

    Providers use different vocabularies; the provider-native string is
    always preserved in ``provider_status`` next to this canonical value.
    ``UNKNOWN`` means the provider reported a state this schema version
    does not recognize — that is surfaced, never silently coerced.
    """

    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class Provenance(FrozenModel):
    """Retrieval provenance attached to every normalized object.

    External observations must never appear as anonymous rows: every one
    names its provider, adapter version, endpoint, and retrieval time so a
    future provider API change is detectable and the normalization is
    reproducible.
    """

    provider: str = Field(..., min_length=1)
    adapter_version: str = Field(..., min_length=1)
    endpoint: str = Field(..., min_length=1,
                          description="Endpoint identifier (path, not full URL "
                                      "with secrets — adapters never put "
                                      "credentials in provenance)")
    retrieved_at: str = Field(..., min_length=1,
                              description="UTC ISO-8601 retrieval timestamp")
    source_kind: Literal["live", "recorded", "synthetic_fixture"] = Field(
        ..., description="live = network response; recorded = replayed "
                         "provider response; synthetic_fixture = labeled "
                         "synthetic test data, NOT provider data")


class Resolution(FrozenModel):
    """External determination of the event outcome, where available."""

    resolved: bool = False
    outcome: Optional[str] = Field(None, description="Winning outcome name "
                                                     "(e.g. 'yes', 'no')")
    resolved_at: Optional[str] = Field(None, description="UTC ISO-8601")
    settlement_value: Optional[float] = Field(
        None, description="Settlement value per contract in provider units")
    source: str = Field("", description="Provider resolution source note")


class OutcomeSpec(FrozenModel):
    """One settlement branch of a market.

    ``token_id`` is the provider-native tradable identity for this outcome
    (e.g. a Polymarket CLOB token id). Kalshi binary markets have implicit
    YES/NO outcomes on one ticker; both are represented explicitly here.
    """

    name: str = Field(..., min_length=1)
    token_id: Optional[str] = None


class ExternalEvent(FrozenModel):
    """A real-world proposition as defined by one provider.

    Identity is provider-scoped: two providers describing the same
    real-world event are two ``ExternalEvent`` rows, related only through
    the explicit event-matching layer (tezcat/external/matching.py) —
    equivalence is never assumed.
    """

    provider: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1, description="Provider-native id")
    title: str = ""
    category: str = ""
    close_time: Optional[str] = Field(None, description="UTC ISO-8601")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Selected provider-native fields preserved verbatim")
    provenance: Optional[Provenance] = None


class ExternalMarket(FrozenModel):
    """The tradable contract associated with an event."""

    provider: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1, description="Provider-native id "
                                                          "(ticker, condition id, …)")
    event_id: str = Field("", description="Provider-native parent event id")
    question: str = ""
    outcomes: List[OutcomeSpec] = Field(default_factory=list)
    status: MarketStatus = MarketStatus.UNKNOWN
    provider_status: str = Field("", description="Provider-native status string")
    resolution: Optional[Resolution] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    tick_size: Optional[float] = Field(
        None, description="Provider-reported tick in canonical probability "
                          "units; None when the provider does not report one "
                          "— never assumed")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None


def _check_probability(value: Optional[float], name: str) -> None:
    if value is None:
        return
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a probability in [0, 1]; got {value!r}")


class Quote(FrozenModel):
    """Best bid/ask for the YES-equivalent outcome, in canonical units.

    ``bid``/``ask``/``mid`` are implied-probability proxies in [0, 1].
    Provider-native price strings are preserved verbatim, and
    ``price_transform`` names the exact conversion rule applied.
    """

    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    provider_bid: Optional[str] = Field(None, description="Verbatim provider value")
    provider_ask: Optional[str] = Field(None, description="Verbatim provider value")
    price_transform: str = Field(..., min_length=1,
                                 description="Documented conversion rule, e.g. "
                                             "'dollars_string/1.0' or 'cents_int/100'")

    @model_validator(mode="after")
    def _validate(self) -> "Quote":
        for name in ("bid", "ask", "mid"):
            _check_probability(getattr(self, name), name)
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError(f"crossed quote: bid {self.bid} > ask {self.ask}")
        return self

    @property
    def spread(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


class BookLevel(FrozenModel):
    """One canonical order-book level: probability price × contract size."""

    price: float = Field(..., description="Implied-probability proxy in [0, 1]")
    size: float = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate(self) -> "BookLevel":
        _check_probability(self.price, "price")
        return self


class OrderbookSnapshot(FrozenModel):
    """Timestamped depth, in both provider-native and canonical form.

    Kalshi and Polymarket do not expose identical microstructure — Kalshi
    reports YES bids and NO bids (a YES bid at p is a NO ask at 1−p);
    Polymarket reports per-token bids and asks. The provider-native book is
    preserved verbatim in ``native``; ``bids``/``asks`` are the canonical
    YES-side comparative representation, computed only where the conversion
    is mathematically justified and named in ``book_transform``.
    """

    provider: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1)
    timestamp: Optional[str] = Field(None, description="UTC ISO-8601 if the "
                                                       "provider reports one")
    bids: List[BookLevel] = Field(default_factory=list,
                                  description="YES-side bids, best first")
    asks: List[BookLevel] = Field(default_factory=list,
                                  description="YES-side asks, best first")
    native: Dict[str, Any] = Field(default_factory=dict,
                                   description="Provider-native book, verbatim")
    book_transform: str = Field(..., min_length=1,
                                description="Documented native→canonical rule")
    provenance: Optional[Provenance] = None

    @model_validator(mode="after")
    def _validate(self) -> "OrderbookSnapshot":
        for side, levels, best_first_desc in (("bids", self.bids, True),
                                              ("asks", self.asks, False)):
            prices = [lv.price for lv in levels]
            ordered = sorted(prices, reverse=best_first_desc)
            if prices != ordered:
                raise ValueError(f"{side} not sorted best-first: {prices}")
        if self.bids and self.asks and self.bids[0].price > self.asks[0].price:
            raise ValueError("crossed canonical book")
        return self

    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    def midpoint(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        return (b + a) / 2 if b is not None and a is not None else None

    def depth(self, band: float = 0.10) -> Optional[float]:
        """Total contracts resting within ±band of the midpoint."""
        mid = self.midpoint()
        if mid is None:
            return None
        total = 0.0
        for lv in list(self.bids) + list(self.asks):
            if abs(lv.price - mid) <= band:
                total += lv.size
        return total

    def imbalance(self) -> Optional[float]:
        """Top-of-book imbalance (bid−ask)/(bid+ask) sizes; None if empty."""
        if not self.bids or not self.asks:
            return None
        b, a = self.bids[0].size, self.asks[0].size
        return (b - a) / (b + a) if (b + a) > 0 else None


class NormalizedTrade(FrozenModel):
    """One executed transaction, in canonical units + provider verbatim."""

    provider: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1)
    trade_id: str = Field(..., min_length=1)
    timestamp: Optional[str] = Field(None, description="UTC ISO-8601")
    price: float = Field(..., description="YES implied-probability proxy")
    size: float = Field(..., ge=0)
    taker_side: Optional[str] = Field(None, description="Provider-native taker "
                                                        "side, verbatim")
    provider_price: Optional[str] = None
    price_transform: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "NormalizedTrade":
        _check_probability(self.price, "price")
        return self


class MarketObservation(FrozenModel):
    """One timestamped snapshot row of a market — the dataset unit.

    Fields a provider does not expose stay ``None``; a ``None`` here means
    "not observed", never zero.
    """

    market_id: str = Field(..., min_length=1)
    timestamp: str = Field(..., min_length=1, description="UTC ISO-8601")
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    implied_probability: Optional[float] = Field(
        None, description="Market-implied probability proxy for the YES "
                          "outcome — NOT the true event probability")
    spread: Optional[float] = None
    depth: Optional[float] = Field(None, ge=0)
    volume: Optional[float] = Field(None, ge=0)
    trade_count: Optional[int] = Field(None, ge=0)
    status: MarketStatus = MarketStatus.UNKNOWN

    @model_validator(mode="after")
    def _validate(self) -> "MarketObservation":
        for name in ("bid", "ask", "mid", "implied_probability"):
            _check_probability(getattr(self, name), name)
        if self.spread is not None and (not math.isfinite(self.spread)
                                        or self.spread < 0):
            raise ValueError(f"spread must be >= 0; got {self.spread!r}")
        return self
