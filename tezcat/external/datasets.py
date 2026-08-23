"""Immutable external research datasets with full lineage (Phase S3).

The F9 data-lineage philosophy, strengthened for external providers.
Every ingestion follows one path::

    RAW PROVIDER RESPONSE  →  immutable raw artifact
                           →  canonical normalization (MarketObservation rows)
                           →  registered ExternalDataset (manifest + checksums)

Rules enforced here:

* **Datasets are immutable after registration.** Registering identical
  content is idempotent; attempting to overwrite a dataset id with
  different content raises. Changed source data = a **new dataset
  version** (``supersedes`` links the chain).
* **Quality failures reject the dataset.** Non-monotonic timestamps,
  duplicate observations, impossible probabilities, and negative
  quantities are grounds for rejection — never for silent cleaning.
* **Nothing is anonymous.** The manifest records provider, adapter
  version, schema version, endpoints, retrieval timestamp, license/terms,
  permitted-use category, sampling, time window, completeness diagnostics,
  and SHA-256 checksums of both the raw and normalized artifacts.
* **Terms are data.** ``license`` is mandatory and ``permitted_use``
  distinguishes "available locally for analysis" from "permitted for
  public redistribution" — endpoint accessibility never implies
  redistribution rights.

Identity: ``dataset_id = exd_<sha256[:12]>`` over (external schema version,
provider, adapter version, raw checksum, normalized observations). The
retrieval timestamp is provenance, not identity, so re-registering the
byte-identical retrieval dedupes to the same id.

Storage layout mirrors the artifact-store philosophy (works on LocalStore;
nothing AWS-specific)::

    <root>/external/
        index.json
        <provider>/<market>/<dataset_id>/raw.json
        <provider>/<market>/<dataset_id>/normalized.json
        <provider>/<market>/<dataset_id>/manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from tezcat.core.config import FrozenModel, canonical_json
from tezcat.external.schema import EXTERNAL_SCHEMA_VERSION, MarketObservation


class DatasetError(ValueError):
    pass


class DataQualityError(DatasetError):
    """The observations violate basic validity; the dataset is rejected."""


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
def validate_observations(observations: List[MarketObservation],
                          expect_monotonic: bool = True) -> Dict[str, Any]:
    """Validate a normalized observation series; reject rather than clean.

    Returns completeness diagnostics on success. Raises
    :class:`DataQualityError` listing every violation on failure —
    impossible probabilities and negative quantities are already blocked
    by the schema; this layer checks the *series*: emptiness, timestamp
    order, and duplicates.
    """
    problems: List[str] = []
    if not observations:
        raise DataQualityError("empty observation series — nothing to register")

    seen: set = set()
    prev_ts: Optional[str] = None
    for i, obs in enumerate(observations):
        key = (obs.market_id, obs.timestamp)
        if key in seen:
            problems.append(f"duplicate observation at index {i}: "
                            f"market {obs.market_id} @ {obs.timestamp}")
        seen.add(key)
        if expect_monotonic and prev_ts is not None and obs.timestamp < prev_ts:
            problems.append(f"timestamp regression at index {i}: "
                            f"{obs.timestamp} < {prev_ts}")
        prev_ts = obs.timestamp

    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise DataQualityError(
            f"observation series failed quality validation — dataset "
            f"rejected, not cleaned: {shown}{more}")

    fields = ("bid", "ask", "mid", "implied_probability", "spread", "depth",
              "volume", "trade_count")
    n = len(observations)
    completeness = {f: sum(getattr(o, f) is not None for o in observations) / n
                    for f in fields}
    return {"n_observations": n, "field_completeness": completeness,
            "first_timestamp": observations[0].timestamp,
            "last_timestamp": observations[-1].timestamp}


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
class DatasetManifest(FrozenModel):
    """The registered identity + lineage of one external dataset."""

    dataset_id: str
    dataset_hash: str
    version: int = Field(1, ge=1)
    supersedes: Optional[str] = Field(
        None, description="Previous dataset version replaced by this one")
    provider: str = Field(..., min_length=1)
    adapter_version: str = Field(..., min_length=1)
    external_schema_version: int
    source_kind: Literal["live", "recorded", "synthetic_fixture"]
    event_id: str = ""
    market_ids: List[str] = Field(..., min_length=1)
    endpoints: List[str] = Field(default_factory=list)
    retrieved_at: str = Field(..., min_length=1)
    time_window: Dict[str, str] = Field(default_factory=dict,
                                        description="{'start','end'} UTC ISO")
    sampling: str = Field(..., min_length=1,
                          description="Sampling rule, e.g. '60-minute candles'")
    license: str = Field(..., min_length=1,
                         description="License/terms statement; mandatory")
    permitted_use: Literal["local_analysis", "public_redistribution_permitted",
                           "unknown"] = "local_analysis"
    raw_checksum: Optional[str] = Field(
        None, description="SHA-256 of the raw artifact; None when raw "
                          "retention is not permitted")
    normalized_checksum: str = Field(..., min_length=1)
    n_observations: int = Field(..., ge=1)
    completeness: Dict[str, float] = Field(default_factory=dict)
    notes: str = ""

    def lineage(self) -> Dict[str, Any]:
        return {"dataset_id": self.dataset_id, "dataset_hash": self.dataset_hash,
                "version": self.version, "provider": self.provider,
                "adapter_version": self.adapter_version,
                "external_schema_version": self.external_schema_version,
                "source_kind": self.source_kind, "event_id": self.event_id,
                "market_ids": self.market_ids, "retrieved_at": self.retrieved_at,
                "time_window": self.time_window, "sampling": self.sampling,
                "license": self.license, "permitted_use": self.permitted_use,
                "raw_checksum": self.raw_checksum,
                "normalized_checksum": self.normalized_checksum,
                "n_observations": self.n_observations}


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _safe(component: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", component)[:120] or "_"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class ExternalDatasetStore:
    def __init__(self, root: str = "data"):
        self.root = Path(root) / "external"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- io (atomic, writer-unique tmp names — F11 discipline) ---------
    def _write(self, path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, path)

    def _read(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def _index(self) -> Dict[str, Dict[str, Any]]:
        return self._read(self.root / "index.json") or {}

    def _dir_for(self, manifest_row: Dict[str, Any]) -> Path:
        return (self.root / _safe(manifest_row["provider"])
                / _safe(manifest_row["market_ids"][0])
                / manifest_row["dataset_id"])

    # -- registration --------------------------------------------------
    def register(self, *, provider: str, adapter_version: str,
                 source_kind: str, market_ids: List[str],
                 observations: List[MarketObservation],
                 retrieved_at: str, sampling: str, license: str,
                 event_id: str = "", endpoints: Optional[List[str]] = None,
                 raw: Optional[Any] = None,
                 permitted_use: str = "local_analysis",
                 supersedes: Optional[str] = None,
                 notes: str = "") -> DatasetManifest:
        """Validate, checksum, and register an immutable dataset.

        ``raw`` is the verbatim provider response(s); pass ``None`` only
        when retention of raw data is not permitted (the manifest records
        the absence). Quality failures raise before anything is written.
        """
        diagnostics = validate_observations(observations)
        normalized_payload = [o.model_dump(mode="json") for o in observations]
        normalized_checksum = _sha256(normalized_payload)
        raw_checksum = _sha256(raw) if raw is not None else None

        version = 1
        if supersedes is not None:
            prev = self.get(supersedes)  # raises if unknown
            version = prev.version + 1

        dataset_hash = _sha256({
            "external_schema_version": EXTERNAL_SCHEMA_VERSION,
            "provider": provider,
            "adapter_version": adapter_version,
            "raw_checksum": raw_checksum,
            "normalized_checksum": normalized_checksum,
        })
        dataset_id = f"exd_{dataset_hash[:12]}"

        manifest = DatasetManifest(
            dataset_id=dataset_id, dataset_hash=dataset_hash,
            version=version, supersedes=supersedes,
            provider=provider, adapter_version=adapter_version,
            external_schema_version=EXTERNAL_SCHEMA_VERSION,
            source_kind=source_kind, event_id=event_id,
            market_ids=list(market_ids), endpoints=list(endpoints or []),
            retrieved_at=retrieved_at,
            time_window={"start": diagnostics["first_timestamp"],
                         "end": diagnostics["last_timestamp"]},
            sampling=sampling, license=license, permitted_use=permitted_use,
            raw_checksum=raw_checksum, normalized_checksum=normalized_checksum,
            n_observations=diagnostics["n_observations"],
            completeness=diagnostics["field_completeness"], notes=notes)

        record = manifest.model_dump(mode="json")
        with self._lock:
            index = self._index()
            if dataset_id in index:
                existing = self._read(self._dir_for(index[dataset_id])
                                      / "manifest.json")
                if existing and existing["normalized_checksum"] != normalized_checksum:
                    raise DatasetError(
                        f"dataset id collision: {dataset_id} exists with "
                        "different content")
                return DatasetManifest.model_validate(existing)
            d = self._dir_for(record)
            if raw is not None:
                self._write(d / "raw.json", raw)
            self._write(d / "normalized.json", normalized_payload)
            self._write(d / "manifest.json", record)
            index[dataset_id] = {"dataset_id": dataset_id,
                                 "provider": provider,
                                 "market_ids": list(market_ids),
                                 "event_id": event_id,
                                 "version": version,
                                 "supersedes": supersedes,
                                 "retrieved_at": retrieved_at,
                                 "n_observations": manifest.n_observations,
                                 "source_kind": source_kind}
            self._write(self.root / "index.json", index)
        return manifest

    # -- retrieval -----------------------------------------------------
    def list(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = list(self._index().values())
        if provider is not None:
            rows = [r for r in rows if r["provider"] == provider]
        return sorted(rows, key=lambda r: (r["provider"], r["dataset_id"]))

    def get(self, dataset_id: str) -> DatasetManifest:
        row = self._index().get(dataset_id)
        if row is None:
            raise DatasetError(f"unknown dataset {dataset_id!r}")
        manifest = self._read(self._dir_for(row) / "manifest.json")
        if manifest is None:
            raise DatasetError(f"dataset {dataset_id} indexed but manifest "
                               "missing — store is corrupt")
        return DatasetManifest.model_validate(manifest)

    def observations(self, dataset_id: str) -> List[MarketObservation]:
        """Load and re-verify the normalized artifact against its checksum."""
        manifest = self.get(dataset_id)
        row = self._index()[dataset_id]
        payload = self._read(self._dir_for(row) / "normalized.json")
        if payload is None:
            raise DatasetError(f"dataset {dataset_id} normalized artifact missing")
        if _sha256(payload) != manifest.normalized_checksum:
            raise DatasetError(
                f"dataset {dataset_id} normalized artifact does not match "
                "its registered checksum — refusing to load tampered data")
        return [MarketObservation.model_validate(o) for o in payload]

    def raw(self, dataset_id: str) -> Optional[Any]:
        manifest = self.get(dataset_id)
        row = self._index()[dataset_id]
        payload = self._read(self._dir_for(row) / "raw.json")
        if payload is None:
            return None
        if manifest.raw_checksum and _sha256(payload) != manifest.raw_checksum:
            raise DatasetError(
                f"dataset {dataset_id} raw artifact does not match its "
                "registered checksum")
        return payload

    def versions(self, dataset_id: str) -> List[str]:
        """Chain of dataset versions ending at ``dataset_id`` (oldest first)."""
        chain = [dataset_id]
        seen = {dataset_id}
        current = self.get(dataset_id)
        while current.supersedes:
            if current.supersedes in seen:
                raise DatasetError(f"version cycle at {current.supersedes}")
            chain.append(current.supersedes)
            seen.add(current.supersedes)
            current = self.get(current.supersedes)
        return list(reversed(chain))
