"""Immutable raw-to-normalized external dataset artifacts."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from tezcat.external.schemas import (
    DatasetManifest, checksum, dataset_identity, utc_now,
)


class DatasetStoreError(ValueError):
    pass


class ExternalDatasetStore:
    """Persist external data using the configured LocalStore/AWS-compatible API."""

    INDEX_PREFIX = "external"
    INDEX_NAME = "datasets_index.json"

    def __init__(self, store: Any):
        self.store = store

    def _index(self) -> List[Dict[str, Any]]:
        value = self.store.load_artifact(self.INDEX_PREFIX, self.INDEX_NAME)
        return value if isinstance(value, list) else []

    def _save_index(self, rows: List[Dict[str, Any]]) -> None:
        self.store.save_artifact(self.INDEX_PREFIX, self.INDEX_NAME, rows)

    def list(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._index()
        if provider:
            rows = [row for row in rows if row.get("provider") == provider]
        return sorted(rows, key=lambda row: (row.get("provider", ""), row.get("dataset_id", "")))

    def get(self, dataset_id: str) -> Dict[str, Any]:
        for row in self._index():
            if row.get("dataset_id") == dataset_id:
                manifest = self.store.load_artifact(row["artifact_prefix"], "manifest.json")
                if not isinstance(manifest, dict):
                    raise DatasetStoreError(f"dataset {dataset_id} has no manifest")
                return manifest
        raise DatasetStoreError(f"unknown external dataset {dataset_id!r}")

    def load_artifact(self, dataset_id: str, name: str) -> Any:
        for row in self._index():
            if row.get("dataset_id") == dataset_id:
                return self.store.load_artifact(row["artifact_prefix"], name)
        raise DatasetStoreError(f"unknown external dataset {dataset_id!r}")

    def register(self, *, provider: str, source_id: str,
                 raw: Any, normalized: Any,
                 event_ids: Iterable[str], market_ids: Iterable[str],
                 source_url: str, endpoint_id: str,
                 adapter_version: str,
                 license_terms: str = "unknown — verify provider terms before redistribution",
                 permitted_use: str = "local analysis only unless provider terms state otherwise",
                 sampling: Optional[str] = None,
                 time_window: Optional[Dict[str, str]] = None,
                 completeness: Optional[Dict[str, Any]] = None,
                 parent_dataset_id: Optional[str] = None) -> DatasetManifest:
        raw_hash = checksum(raw)
        normalized_hash = checksum(normalized)
        rows = self._index()
        for row in rows:
            if row.get("provider") == provider and row.get("source_id") == source_id and row.get("raw_checksum") == raw_hash:
                manifest = self.store.load_artifact(row["artifact_prefix"], "manifest.json")
                if isinstance(manifest, dict):
                    return DatasetManifest.model_validate(manifest)

        versions = [int(row.get("dataset_version", 0)) for row in rows
                    if row.get("provider") == provider and row.get("source_id") == source_id]
        version = max(versions, default=0) + 1
        dataset_id = dataset_identity(provider, source_id, raw_hash, version)
        prefix = f"external/{provider}/{source_id}/{dataset_id}"
        manifest = DatasetManifest(
            dataset_id=dataset_id, dataset_version=version, provider=provider,
            source_id=source_id, event_ids=sorted({str(v) for v in event_ids}),
            market_ids=sorted({str(v) for v in market_ids}),
            retrieval_timestamp=utc_now(), source_url=source_url,
            endpoint_id=endpoint_id, license_terms=license_terms,
            permitted_use=permitted_use, sampling=sampling,
            time_window=time_window, completeness=completeness or {},
            adapter_version=adapter_version, raw_checksum=raw_hash,
            normalized_checksum=normalized_hash, parent_dataset_id=parent_dataset_id,
            lineage={"raw_artifact": f"{prefix}/raw.json", "normalized_artifact": f"{prefix}/normalized.json"},
        )
        self.store.save_artifact(prefix, "raw.json", raw)
        self.store.save_artifact(prefix, "normalized.json", normalized)
        self.store.save_artifact(prefix, "manifest.json", manifest.model_dump(mode="json"))
        rows.append({**manifest.model_dump(mode="json"), "artifact_prefix": prefix})
        self._save_index(rows)
        return manifest
