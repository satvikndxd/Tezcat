"""Research artifact graph (Phase S5-A).

Every stage of a quantitative workflow becomes a **typed, hashed,
immutable node** in one directed provenance graph:

    dataset → forecast → portfolio → market_world → backtest → risk
        → analysis → research_report   (+ signatures, counterfactuals)

Design decisions (explicit, not incidental):

* **One provenance system.** Existing Tezcat identities (experiment
  research hashes, S3 dataset hashes, S4 world hashes and lab result
  ids) are carried verbatim as ``external_identity`` and inside payloads
  — graph nodes *reference* them, never re-mint them. The graph is the
  connective tissue, not a rival registry.
* **Identity = content.** ``artifact_hash`` covers the plane schema
  version, artifact type, config, payload checksum, parent hashes, the
  wrapped external identity, and the code version. Registration is
  idempotent for identical content and collision-safe otherwise.
* **Environment is provenance, not identity.** The execution environment
  (python/platform/optional-package versions) is recorded on every node
  and *checked loudly* during reproduction, but excluded from the hash —
  so "same content, same identity" holds across machines while
  environment drift is still detected and reported, never absorbed.
* **Immutability.** Artifacts are write-once; payloads are checksummed
  and re-verified on load; tampered artifacts refuse to load.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import Field

from tezcat import __version__
from tezcat.core.config import FrozenModel, canonical_json

PLANE_SCHEMA_VERSION = 1

#: artifact type → id prefix. Prefixes are distinct from every existing
#: Tezcat identity prefix (expv_, exd_, mw_, lab_) so a graph id is never
#: mistaken for a native identity it wraps.
ARTIFACT_TYPES: Dict[str, str] = {
    "external_dataset": "dat",
    "event_signature": "sig",
    "forecast": "fct",
    "forecast_quality": "fqe",
    "portfolio": "pft",
    "market_world": "wld",
    "strategy": "stg",
    "backtest": "btr",
    "risk_report": "rsk",
    "analysis": "any",
    "counterfactual": "cfx",
    "research_report": "rpt",
    # Finance domain (Phase S6) — same graph, same provenance discipline
    "company_financials": "cfn",
    "valuation_case": "vca",
    "valuation_output": "vlo",
    "finance_report": "frp",
}


class PlaneError(ValueError):
    pass


def _sha(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def environment_fingerprint() -> Dict[str, Any]:
    """Versioned execution-environment record (provenance, not identity)."""
    env: Dict[str, Any] = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "tezcat_version": __version__,
        "plane_schema_version": PLANE_SCHEMA_VERSION,
    }
    for pkg in ("nautilus_trader", "skfolio", "numpy"):
        try:
            module = __import__(pkg)
            env[f"{pkg}_version"] = getattr(module, "__version__", "unknown")
        except ImportError:
            env[f"{pkg}_version"] = None
    return env


class ResearchArtifact(FrozenModel):
    """One immutable node of the research run graph."""

    artifact_id: str
    artifact_type: str
    artifact_hash: str
    parent_hashes: List[str] = Field(default_factory=list)
    external_identity: Optional[str] = Field(
        None, description="Native Tezcat identity wrapped by this node "
                          "(research hash, dataset hash, world hash, lab "
                          "result id, …) — carried verbatim, never re-minted")
    schema_version: int = PLANE_SCHEMA_VERSION
    code_version: str = __version__
    created_at: str = ""
    environment: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    payload_checksum: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ArtifactGraph:
    """File-backed, write-once research run graph."""

    def __init__(self, root: str = "data"):
        self.root = Path(root) / "plane"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- io ------------------------------------------------------------
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

    # -- registration --------------------------------------------------
    def register(self, artifact_type: str, payload: Any, *,
                 config: Optional[Dict[str, Any]] = None,
                 parents: Sequence[Union[str, ResearchArtifact]] = (),
                 external_identity: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None
                 ) -> ResearchArtifact:
        """Register an immutable node; content-addressed and idempotent."""
        if artifact_type not in ARTIFACT_TYPES:
            raise PlaneError(f"unknown artifact type {artifact_type!r}; "
                             f"known: {sorted(ARTIFACT_TYPES)}")
        parent_hashes = []
        for p in parents:
            parent_hashes.append(p.artifact_hash
                                 if isinstance(p, ResearchArtifact) else str(p))
        config = json.loads(canonical_json(config or {}))
        payload = json.loads(canonical_json(payload))
        payload_checksum = _sha(payload)
        artifact_hash = _sha({
            "schema_version": PLANE_SCHEMA_VERSION,
            "artifact_type": artifact_type,
            "config": config,
            "payload_checksum": payload_checksum,
            "parent_hashes": parent_hashes,
            "external_identity": external_identity,
            "code_version": __version__,
        })
        artifact_id = f"{ARTIFACT_TYPES[artifact_type]}_{artifact_hash[:12]}"
        artifact = ResearchArtifact(
            artifact_id=artifact_id, artifact_type=artifact_type,
            artifact_hash=artifact_hash, parent_hashes=parent_hashes,
            external_identity=external_identity,
            created_at=datetime.now(timezone.utc).isoformat(),
            environment=environment_fingerprint(),
            config=config, payload_checksum=payload_checksum,
            metadata=metadata or {})

        with self._lock:
            d = self.root / artifact_id
            existing = self._read(d / "artifact.json")
            if existing is not None:
                if existing["artifact_hash"] != artifact_hash:
                    raise PlaneError(f"artifact id collision at {artifact_id}")
                return ResearchArtifact.model_validate(existing)
            # every parent must already exist in the graph — dangling
            # provenance is a schema violation, not a warning
            index = self._index()
            known_hashes = {row["artifact_hash"] for row in index.values()}
            for ph in parent_hashes:
                if ph not in known_hashes:
                    raise PlaneError(
                        f"parent hash {ph[:16]}… is not registered in the "
                        "graph — register parents before children")
            self._write(d / "artifact.json", artifact.model_dump(mode="json"))
            self._write(d / "payload.json", payload)
            index[artifact_id] = {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "artifact_hash": artifact_hash,
                "parent_hashes": parent_hashes,
                "external_identity": external_identity,
                "created_at": artifact.created_at,
            }
            self._write(self.root / "index.json", index)
        return artifact

    # -- retrieval -----------------------------------------------------
    def list(self, artifact_type: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = list(self._index().values())
        if artifact_type is not None:
            rows = [r for r in rows if r["artifact_type"] == artifact_type]
        return sorted(rows, key=lambda r: r["created_at"])

    def get(self, artifact_id: str) -> ResearchArtifact:
        record = self._read(self.root / artifact_id / "artifact.json")
        if record is None:
            raise PlaneError(f"unknown artifact {artifact_id!r}")
        return ResearchArtifact.model_validate(record)

    def by_hash(self, artifact_hash: str) -> ResearchArtifact:
        for row in self._index().values():
            if row["artifact_hash"] == artifact_hash:
                return self.get(row["artifact_id"])
        raise PlaneError(f"no artifact with hash {artifact_hash[:16]}…")

    def payload(self, artifact_id: str) -> Any:
        """Checksum-verified payload load; refuses tampered artifacts."""
        artifact = self.get(artifact_id)
        payload = self._read(self.root / artifact_id / "payload.json")
        if payload is None:
            raise PlaneError(f"artifact {artifact_id} payload missing")
        if _sha(payload) != artifact.payload_checksum:
            raise PlaneError(
                f"artifact {artifact_id} payload does not match its "
                "registered checksum — refusing to load tampered data")
        return payload

    # -- graph traversal -----------------------------------------------
    def lineage(self, artifact_id: str) -> List[ResearchArtifact]:
        """All ancestors (root-first) plus the artifact itself, deduped."""
        target = self.get(artifact_id)
        by_hash = {row["artifact_hash"]: row["artifact_id"]
                   for row in self._index().values()}
        ordered: List[ResearchArtifact] = []
        seen: set = set()

        def visit(node: ResearchArtifact) -> None:
            if node.artifact_hash in seen:
                return
            seen.add(node.artifact_hash)
            for ph in node.parent_hashes:
                if ph in by_hash:
                    visit(self.get(by_hash[ph]))
            ordered.append(node)

        visit(target)
        return ordered

    def children(self, artifact_id: str) -> List[Dict[str, Any]]:
        target_hash = self.get(artifact_id).artifact_hash
        return [row for row in self._index().values()
                if target_hash in row["parent_hashes"]]

    def verify(self, artifact_id: str) -> Dict[str, Any]:
        """Re-verify payload checksum and artifact hash from stored content."""
        artifact = self.get(artifact_id)
        payload = self._read(self.root / artifact_id / "payload.json")
        checks = []
        payload_ok = payload is not None and _sha(payload) == artifact.payload_checksum
        checks.append({"check": "payload checksum", "ok": payload_ok})
        recomputed = _sha({
            "schema_version": artifact.schema_version,
            "artifact_type": artifact.artifact_type,
            "config": artifact.config,
            "payload_checksum": artifact.payload_checksum,
            "parent_hashes": artifact.parent_hashes,
            "external_identity": artifact.external_identity,
            "code_version": artifact.code_version,
        })
        checks.append({"check": "artifact hash",
                       "ok": recomputed == artifact.artifact_hash})
        return {"artifact_id": artifact_id,
                "success": all(c["ok"] for c in checks), "checks": checks}
