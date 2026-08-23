"""Offline fixture transport for external providers (Phase S3).

The F9 offline rule, applied to provider adapters: **no network access in
tests, ever**, and no fabricated data pretending to be provider data.
Every fixture file declares what it is::

    {
      "fixture_label": "SYNTHETIC FIXTURE",          // or
      "fixture_label": "RECORDED PROVIDER RESPONSE", // + provenance
      "provenance":    "who made this / where it was recorded",
      "provider":      "kalshi",
      "routes": { "<path>": <payload>, "<path>?<query>": <payload> }
    }

A missing or unknown label is an error — synthetic and recorded data are
never blurred. Recorded responses stored in the repository must document
their provenance and terms; otherwise fixtures stay synthetic and
minimal, sufficient to test schemas.

Failure injection payloads (for the provider-failure test matrix)::

    {"__status__": 500, "__body__": "..."}     HTTP error
    {"__timeout__": true}                      transport timeout
    {"__malformed__": "not json {"}            malformed body
    {"__sequence__": [p1, p2, ...]}            consumed one per request

Route matching: the transport strips any base URL, then tries
``path?canonical_query`` (params sorted) before falling back to ``path``.
Unrouted requests raise — a fixture-driven test can never silently hit
the network or an empty default.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tezcat.external.provider import ProviderError, ProviderTimeout

FIXTURE_LABELS = ("SYNTHETIC FIXTURE", "RECORDED PROVIDER RESPONSE")


class FixtureError(ValueError):
    pass


def load_fixture(path: str | Path) -> Dict[str, Any]:
    """Load and validate a labeled fixture file."""
    p = Path(path)
    if not p.exists():
        raise FixtureError(f"fixture file not found: {p}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise FixtureError(f"fixture {p} is not valid JSON: {exc}") from exc
    label = data.get("fixture_label")
    if label not in FIXTURE_LABELS:
        raise FixtureError(
            f"fixture {p} must declare fixture_label as one of "
            f"{FIXTURE_LABELS}; got {label!r} — synthetic and recorded "
            "provider data are never blurred")
    if label == "RECORDED PROVIDER RESPONSE" and not data.get("provenance"):
        raise FixtureError(
            f"fixture {p} is labeled RECORDED but documents no provenance")
    if "routes" not in data or not isinstance(data["routes"], dict):
        raise FixtureError(f"fixture {p} has no 'routes' mapping")
    return data


class FixtureTransport:
    """Transport that serves canned responses; never opens a socket."""

    def __init__(self, routes: Dict[str, Any],
                 label: str = "SYNTHETIC FIXTURE"):
        if label not in FIXTURE_LABELS:
            raise FixtureError(f"unknown fixture label {label!r}")
        self.routes = dict(routes)
        self.label = label
        self.requests: List[str] = []  # observed request log (for tests)

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureTransport":
        data = load_fixture(path)
        return cls(data["routes"], label=data["fixture_label"])

    # -- request handling ---------------------------------------------
    def _lookup(self, url: str, params: Optional[Dict[str, Any]]) -> Any:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        # strip API base prefixes so routes are written as bare paths
        for prefix in ("/trade-api/v2",):
            if path.startswith(prefix):
                path = path[len(prefix):]
        merged: Dict[str, Any] = dict(urllib.parse.parse_qsl(parsed.query))
        merged.update({k: v for k, v in (params or {}).items() if v is not None})
        query = urllib.parse.urlencode(sorted(
            (k, str(v)) for k, v in merged.items()))
        for key in (f"{path}?{query}" if query else path, path):
            if key in self.routes:
                self.requests.append(key)
                return self.routes[key]
        raise ProviderError(
            f"fixture transport has no route for {path}"
            + (f"?{query}" if query else "")
            + f" (available: {sorted(self.routes)[:8]}…) — offline tests "
              "never fall through to the network")

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            timeout: float = 10.0) -> Tuple[int, str]:
        payload = self._lookup(url, params)
        if isinstance(payload, dict) and "__sequence__" in payload:
            seq = payload["__sequence__"]
            if not seq:
                raise ProviderError("fixture sequence exhausted")
            payload = seq.pop(0)
        if isinstance(payload, dict):
            if payload.get("__timeout__"):
                raise ProviderTimeout("fixture-injected timeout")
            if "__status__" in payload:
                return int(payload["__status__"]), str(payload.get("__body__", ""))
            if "__malformed__" in payload:
                return 200, str(payload["__malformed__"])
        return 200, json.dumps(payload)
