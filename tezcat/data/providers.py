"""Validated, file-backed price-series data for stylized-facts research."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from pydantic import Field, field_validator, model_validator

from tezcat.core.config import FrozenModel


class SeriesData(FrozenModel):
    """A validated price series with explicit provenance metadata."""

    source_id: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    instrument: str = Field(..., min_length=1)
    date_range: str = Field(..., min_length=1)
    license: str = Field(..., min_length=1)
    sampling: str = Field(..., min_length=1)
    prices: List[float] = Field(..., min_length=30)
    volumes: Optional[List[float]] = None

    @field_validator("prices")
    @classmethod
    def _positive_finite_prices(cls, values: List[float]) -> List[float]:
        import math
        if len(values) < 30:
            raise ValueError("prices must contain at least 30 observations")
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("prices must be positive finite values")
        return values

    @field_validator("volumes")
    @classmethod
    def _finite_nonnegative_volumes(cls, values: Optional[List[float]]) -> Optional[List[float]]:
        if values is None:
            return values
        import math
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("volumes must be nonnegative finite values")
        return values

    @model_validator(mode="after")
    def _validate_provider_and_lengths(self) -> "SeriesData":
        if self.provider != "file":
            raise ValueError(
                f"provider {self.provider!r} is not available; only the file provider is implemented"
            )
        if self.volumes is not None and len(self.volumes) != len(self.prices):
            raise ValueError("volumes length must match prices length")
        return self

    def _checksum_payload(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def checksum(self) -> str:
        """SHA-256 of the canonical, metadata-preserving series payload."""
        encoded = json.dumps(
            self._checksum_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def with_checksum(self) -> "SeriesData":
        """Return this immutable, checksummed value for fluent call sites."""
        _ = self.checksum
        return self

    def lineage(self) -> Dict[str, Any]:
        """Return the provenance record used by calibration artifacts."""
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "instrument": self.instrument,
            "date_range": self.date_range,
            "license": self.license,
            "sampling": self.sampling,
            "n_observations": len(self.prices),
            "checksum": self.checksum,
        }


def load_series_file(path: Any) -> SeriesData:
    """Load and validate a JSON :class:`SeriesData` payload from ``path``."""
    from pathlib import Path
    from tezcat.data import DataError

    file_path = Path(path)
    if not file_path.exists():
        raise DataError(f"series file not found: {file_path}")
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"series file is not valid JSON: {file_path}") from exc
    if not isinstance(payload, dict):
        raise DataError("series file must contain a JSON object")
    try:
        return SeriesData.model_validate(payload)
    except Exception as exc:
        raise DataError(f"invalid series data: {exc}") from exc


def run_price_series(snapshots: Sequence[Any]) -> List[float]:
    """Extract one last-trade price per ecology snapshot without imputation."""
    prices: List[float] = []
    for snapshot in snapshots:
        value = snapshot.get("last_price") if isinstance(snapshot, dict) else getattr(snapshot, "last_price", None)
        if value is None:
            raise ValueError("snapshot is missing last_price")
        prices.append(float(value))
    return prices
