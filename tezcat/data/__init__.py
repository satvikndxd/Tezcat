"""Offline, provenance-aware calibration data providers."""

from __future__ import annotations

from .providers import SeriesData, load_series_file, run_price_series


class DataError(Exception):
    """Raised when a series file cannot be loaded or validated."""


__all__ = ["DataError", "SeriesData", "load_series_file", "run_price_series"]
