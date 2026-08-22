"""Canonical, provider-neutral schemas for Tezcat's external market layer.

External observations are research inputs, never synthetic market state.  The
models intentionally retain provider-native payloads and identifiers so that a
normalization decision can be audited and reproduced later.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXTERNAL_SCHEMA_VERSION = "s3.1"


class ExternalDataError(ValueError):
    """Base error for explicit external-data failures."""


class ProviderSchemaError(ExternalDataError):
    """The provider response is missing or changed required semantics."""


class DataQualityError(ExternalDataError):
    """The response contains impossible or contradictory observations."""


class ExternalModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class Event(ExternalModel):
    provider: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    close_time: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Outcome(ExternalModel):
    outcome_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    token_id: Optional[str] = None
    provider_units: Optional[str] = None


class Market(ExternalModel):
    provider: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    event_id: Optional[str] = None
    title: str = Field(min_length=1)
    description: Optional[str] = None
    outcomes: List[Outcome] = Field(min_length=1)
    status: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Quote(ExternalModel):
    provider: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    implied_probability: Optional[float] = None
    provider_price: Optional[float] = None
    provider_units: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("implied_probability")
    @classmethod
    def _probability_range(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("implied_probability must be in [0, 1]")
        return value


class BookLevel(ExternalModel):
    price: float
    quantity: float = Field(ge=0)

    @field_validator("price")
    @classmethod
    def _price_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("canonical probability book prices must be in [0, 1]")
        return value


class OrderbookSnapshot(ExternalModel):
    provider: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    event_id: Optional[str] = None
    outcome_id: Optional[str] = None
    timestamp: str = Field(min_length=1)
    bids: List[BookLevel] = Field(default_factory=list)
    asks: List[BookLevel] = Field(default_factory=list)
    mid: Optional[float] = None
    spread: Optional[float] = None
    depth: Optional[float] = None
    implied_probability: Optional[float] = None
    native_bids: List[Dict[str, Any]] = Field(default_factory=list)
    native_asks: List[Dict[str, Any]] = Field(default_factory=list)
    provider_units: Optional[str] = None
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("implied_probability", "mid")
    @classmethod
    def _bounded_probability(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("probability-valued fields must be in [0, 1]")
        return value


class Trade(ExternalModel):
    provider: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    event_id: Optional[str] = None
    trade_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    outcome_id: Optional[str] = None
    price: float
    quantity: float = Field(ge=0)
    side: Optional[Literal["buy", "sell", "unknown"]] = "unknown"
    provider_units: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("price")
    @classmethod
    def _trade_probability_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("canonical probability trade prices must be in [0, 1]")
        return value


class DatasetManifest(ExternalModel):
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    provider: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    event_ids: List[str] = Field(default_factory=list)
    market_ids: List[str] = Field(default_factory=list)
    retrieval_timestamp: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    license_terms: str = "unknown — verify provider terms before redistribution"
    permitted_use: str = "local analysis only unless provider terms state otherwise"
    sampling: Optional[str] = None
    time_window: Optional[Dict[str, str]] = None
    completeness: Dict[str, Any] = Field(default_factory=dict)
    adapter_version: str = Field(min_length=1)
    schema_version: str = EXTERNAL_SCHEMA_VERSION
    raw_checksum: str = Field(min_length=64, max_length=64)
    normalized_checksum: str = Field(min_length=64, max_length=64)
    parent_dataset_id: Optional[str] = None
    lineage: Dict[str, Any] = Field(default_factory=dict)


class EventSignature(ExternalModel):
    """Versioned, hashable observed feature object; null means unavailable."""

    signature_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    window: Dict[str, str]
    pre_event_probability: Optional[float] = None
    post_event_probability: Optional[float] = None
    delta_probability: Optional[float] = None
    peak_probability: Optional[float] = None
    time_to_peak_seconds: Optional[float] = None
    pre_event_spread: Optional[float] = None
    post_event_spread: Optional[float] = None
    volume_change: Optional[float] = None
    depth_change: Optional[float] = None
    probability_volatility: Optional[float] = None
    jump_frequency: Optional[float] = None
    activity_intensity: Optional[float] = None
    recovery_seconds: Optional[float] = None
    transform: str = "raw_probability_delta"
    signature_version: str = EXTERNAL_SCHEMA_VERSION
    checksum: str = Field(min_length=64, max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EventMatch(ExternalModel):
    match_id: str = Field(min_length=1)
    event_a: Dict[str, str]
    event_b: Dict[str, str]
    level: Literal["EXACT", "LIKELY_EQUIVALENT", "POSSIBLY_RELATED", "UNMATCHED"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    origin: Literal["algorithmic", "human"] = "algorithmic"
    created_at: str = Field(min_length=1)
    version: str = EXTERNAL_SCHEMA_VERSION
    confirmed: Optional[bool] = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_timestamp(value: Any) -> str:
    """Normalize ISO strings or epoch seconds/milliseconds to UTC ISO text."""
    if isinstance(value, (int, float, Decimal)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    if value is None or str(value).strip() == "":
        raise ProviderSchemaError("missing timestamp")
    text = str(value).strip()
    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ProviderSchemaError(f"invalid timestamp {value!r}") from exc
    if dt.tzinfo is None:
        raise ProviderSchemaError(f"timestamp must include timezone: {value!r}")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def probability(value: Any, units: str = "probability") -> float:
    """Convert provider units to p in [0, 1] without silently guessing."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProviderSchemaError(f"invalid probability value {value!r}") from exc
    unit = units.lower().strip()
    if unit in {"probability", "decimal", "dollars", "usd"}:
        out = number
    elif unit in {"cents", "cent", "basis_points"}:
        out = number / (Decimal("100") if unit != "basis_points" else Decimal("10000"))
    else:
        raise ProviderSchemaError(f"unsupported probability units {units!r}")
    if not Decimal("0") <= out <= Decimal("1"):
        raise DataQualityError(f"probability outside [0,1]: {value!r} {units}")
    return float(out)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def dataset_identity(provider: str, source_id: str, raw_checksum: str, version: int = 1) -> str:
    return f"extds_{checksum({'provider': provider, 'source_id': source_id, 'raw_checksum': raw_checksum, 'version': version})[:16]}"


def signature_identity(payload: Dict[str, Any]) -> str:
    return f"sig_{checksum(payload)[:16]}"
