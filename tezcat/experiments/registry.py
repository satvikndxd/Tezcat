"""Experiment-version registry (Phase F4).

File-backed registry of immutable research objects with parent/child lineage
and content-addressed dedupe. Layout::

    <root>/registry/
        index.json                 # version_id -> summary row
        versions/<version_id>.json # full immutable record
        batches/<batch_id>.json    # batch execution records (Phase F5)
        results/<batch_id>/...     # seed-level result rows (Phase F5)

Registered versions are write-once: re-registering the same research hash
returns the existing id; attempting to overwrite a record with different
content raises.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tezcat.experiments.schema import ExperimentVersion


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, root: str = "data"):
        self.root = Path(root) / "registry"
        (self.root / "versions").mkdir(parents=True, exist_ok=True)
        (self.root / "batches").mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- io ------------------------------------------------------------
    def _write(self, path: Path, obj: Any) -> None:
        """Atomic write via a *writer-unique* tmp name + rename.

        A shared tmp name (e.g. ``key.tmp``) races when two writers target
        the same key concurrently — one rename wins and the other's tmp
        vanishes (found by the F11 fault-injection tests). Unique tmp names
        make concurrent same-key writes safe across threads and processes;
        content-identical rows mean last-write-wins is benign.
        """
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

    # -- versions ------------------------------------------------------
    def register(self, version: ExperimentVersion) -> str:
        """Register an immutable version; content-addressed and idempotent."""
        record = version.to_dict()
        with self._lock:
            path = self.root / "versions" / f"{version.version_id}.json"
            existing = self._read(path)
            if existing is not None:
                if existing["research_hash"] != record["research_hash"]:
                    raise RegistryError(
                        f"version id collision: {version.version_id} exists "
                        "with different content")
                return version.version_id  # idempotent re-register
            if version.parent_version_id is not None:
                parent = self.root / "versions" / f"{version.parent_version_id}.json"
                if not parent.exists():
                    raise RegistryError(
                        f"parent version {version.parent_version_id} not registered")
            self._write(path, record)
            index = self._index()
            index[version.version_id] = {
                "version_id": version.version_id,
                "experiment_id": version.experiment_id,
                "name": version.name,
                "research_hash": version.research_hash,
                "design_type": version.design.design_type,
                "primary_metric": version.design.primary_metric,
                "planned_runs": version.design.planned_runs(),
                "parent_version_id": version.parent_version_id,
            }
            self._write(self.root / "index.json", index)
        return version.version_id

    def get(self, version_id: str) -> Dict[str, Any]:
        record = self._read(self.root / "versions" / f"{version_id}.json")
        if record is None:
            raise RegistryError(f"unknown experiment version {version_id!r}")
        return record

    def load(self, version_id: str) -> ExperimentVersion:
        """Load and re-verify the research hash (fails loudly on drift)."""
        return ExperimentVersion.from_dict(self.get(version_id))

    def by_hash(self, research_hash: str) -> Optional[Dict[str, Any]]:
        for row in self._index().values():
            if row["research_hash"] == research_hash:
                return self.get(row["version_id"])
        return None

    def list(self, experiment_id: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = list(self._index().values())
        if experiment_id is not None:
            rows = [r for r in rows if r["experiment_id"] == experiment_id]
        return sorted(rows, key=lambda r: r["version_id"])

    def children(self, version_id: str) -> List[Dict[str, Any]]:
        return [r for r in self._index().values()
                if r["parent_version_id"] == version_id]

    def lineage(self, version_id: str) -> List[str]:
        """Ancestor chain from root to this version (inclusive)."""
        chain = [version_id]
        seen = {version_id}
        current = self.get(version_id)
        while current.get("parent_version_id"):
            pid = current["parent_version_id"]
            if pid in seen:
                raise RegistryError(f"lineage cycle at {pid}")
            chain.append(pid)
            seen.add(pid)
            current = self.get(pid)
        return list(reversed(chain))

    # -- batches (written by the batch runner, Phase F5) ---------------
    def save_batch(self, batch: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "batches" / f"{batch['batch_id']}.json", batch)

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "batches" / f"{batch_id}.json")

    def save_result_row(self, batch_id: str, key: str, row: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "results" / batch_id / f"{key}.json", row)

    def load_result_row(self, batch_id: str, key: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "results" / batch_id / f"{key}.json")

    def list_result_rows(self, batch_id: str) -> List[Dict[str, Any]]:
        d = self.root / "results" / batch_id
        if not d.exists():
            return []
        return [r for p in sorted(d.glob("*.json")) if (r := self._read(p))]

    # -- analyses and reports (written in Phase F6) --------------------
    def save_analysis(self, version_id: str, analysis: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "analysis" / f"{version_id}.json", analysis)

    def get_analysis(self, version_id: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "analysis" / f"{version_id}.json")

    def save_report(self, version_id: str, markdown: str,
                    meta: Dict[str, Any]) -> None:
        with self._lock:
            path = self.root / "reports" / f"{version_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(markdown)
            os.replace(tmp, path)
            self._write(self.root / "reports" / f"{version_id}.json", meta)

    def get_report(self, version_id: str) -> Optional[str]:
        path = self.root / "reports" / f"{version_id}.md"
        return path.read_text() if path.exists() else None

    def save_batch_summary(self, batch_id: str, summary: Dict[str, Any]) -> None:
        with self._lock:
            self._write(self.root / "results" / batch_id / "summary" / "summary.json",
                        summary)

    def get_batch_summary(self, batch_id: str) -> Optional[Dict[str, Any]]:
        return self._read(self.root / "results" / batch_id / "summary" / "summary.json")
