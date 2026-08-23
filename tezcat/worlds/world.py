"""Market World construction and storage (Phase S4).

``build_world`` turns one cell/replication of a registered experiment
version into a deterministic **Market World**:

    ExperimentVersion ── cell, replication ──► seeded EcologyEngine run
            │                                        │
            ▼                                        ▼
      research hash                       canonical market stream
            │                                        │
            └───────────────► world hash ◄───────────┘

The canonical stream is the Nautilus-facing export contract, kept
deliberately framework-neutral:

* ``quotes``       one L1 row per step with both sides present:
                   {step, ts_ns, bid, ask, bid_size, ask_size}.
                   Sizes are the aggregate resting depth of each side —
                   the closest honest L1 mapping Tezcat's book exposes.
                   Steps with an empty side are *skipped and counted*
                   (``n_quote_gaps``), never fabricated.
* ``trades``       {step, ts_ns, trade_id, price, quantity, aggressor}.
                   Tezcat's matching engine does not record taker side,
                   so ``aggressor`` is ``null`` — exported as
                   NO_AGGRESSOR, not guessed.
* ``annotations``  regime transition events, shock events, and derived
                   regime intervals — optional research metadata layered
                   *next to* standard market data, not mixed into it.

Time mapping (part of the world hash): step ``s`` maps to
``WORLD_EPOCH_NS + s * STEP_NS`` (1 simulated second per step). Within a
step, trade *i* gets ``+i`` ns (preserving intra-step order) and the
end-of-step quote gets ``+STEP_NS − 1`` ns, so the stream is strictly
ordered and chronology is deterministic: trades happen during the step,
the quote snapshot closes it.

Worlds are immutable and content-addressed (``mw_<sha256[:12]>``); the
hash covers the schema version, the experiment's research identity, the
seed/config/state/event hashes, the time mapping, the stream checksums,
and the fingerprint. Same registry + same reference ⇒ byte-identical
world.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json, config_hash
from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.registry import Registry
from tezcat.worlds.fingerprint import EcologyFingerprint, extract_fingerprint

WORLD_SCHEMA_VERSION = 1
WORLD_EPOCH_NS = 1_577_836_800_000_000_000  # 2020-01-01T00:00:00Z
STEP_NS = 1_000_000_000                     # 1 simulated second per step


class WorldError(ValueError):
    pass


def _sha(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


class MarketWorld:
    """In-memory world: manifest + canonical stream artifacts."""

    def __init__(self, manifest: Dict[str, Any], quotes: List[Dict[str, Any]],
                 trades: List[Dict[str, Any]], annotations: Dict[str, Any]):
        self.manifest = manifest
        self.quotes = quotes
        self.trades = trades
        self.annotations = annotations

    @property
    def world_id(self) -> str:
        return self.manifest["world_id"]

    @property
    def world_hash(self) -> str:
        return self.manifest["world_hash"]

    def fingerprint(self) -> EcologyFingerprint:
        return EcologyFingerprint.model_validate(self.manifest["fingerprint"])

    def regime_at(self, ts_ns: int) -> str:
        """Regime active at a stream timestamp (from derived intervals)."""
        for interval in self.annotations["regime_intervals"]:
            if interval["start_ts_ns"] <= ts_ns < interval["end_ts_ns"]:
                return interval["regime"]
        return self.annotations["regime_intervals"][-1]["regime"] \
            if self.annotations["regime_intervals"] else "stable"


def _regime_intervals(regime_events: List[Dict[str, Any]],
                      total_steps: int) -> List[Dict[str, Any]]:
    """Half-open [start, end) regime intervals in steps and stream ns."""
    intervals: List[Dict[str, Any]] = []
    current, start = "stable", 0
    for ev in sorted(regime_events, key=lambda e: e["step"]):
        if ev["step"] > start:
            intervals.append({"start_step": start, "end_step": ev["step"],
                              "regime": current})
        current, start = ev["new_regime"], ev["step"]
    intervals.append({"start_step": start, "end_step": total_steps,
                      "regime": current})
    for iv in intervals:
        iv["start_ts_ns"] = WORLD_EPOCH_NS + iv["start_step"] * STEP_NS
        iv["end_ts_ns"] = WORLD_EPOCH_NS + iv["end_step"] * STEP_NS
    return intervals


def build_world(registry: Registry, ref: str, cell: Optional[str] = None,
                replication: int = 0) -> MarketWorld:
    """Deterministically realize one cell/replication as a Market World."""
    from tezcat.experiments.reproduce import resolve_reference
    version_id = resolve_reference(registry, ref)
    version = registry.load(version_id)  # re-verifies the research hash

    cell_names = [c["cell"] for c in version.cell_configs]
    if cell is None:
        cell = cell_names[0]
    if cell not in cell_names:
        raise WorldError(f"unknown cell {cell!r}; available: {cell_names}")
    config = version.cell_config(cell)
    seed = version.seed_for(cell, replication)

    engine = EcologyEngine(f"world_{version_id}_{cell}_{replication}",
                           config, seed)
    while not engine.done:
        engine.step()
    engine.check_invariants()

    # -- canonical stream ----------------------------------------------
    quotes: List[Dict[str, Any]] = []
    n_quote_gaps = 0
    for s in engine.snapshots:
        bid, ask = s.get("best_bid"), s.get("best_ask")
        if bid is None or ask is None:
            n_quote_gaps += 1
            continue
        step = s["step"]
        quotes.append({
            "step": step,
            "ts_ns": WORLD_EPOCH_NS + step * STEP_NS + STEP_NS - 1,
            "bid": round(bid, 4), "ask": round(ask, 4),
            "bid_size": int(s["bid_depth"]), "ask_size": int(s["ask_depth"]),
        })

    trades: List[Dict[str, Any]] = []
    intra: Dict[int, int] = {}
    for t in engine.trades:
        step = t["step"]
        i = intra.get(step, 0)
        intra[step] = i + 1
        trades.append({
            "step": step,
            "ts_ns": WORLD_EPOCH_NS + step * STEP_NS + i,
            "trade_id": t["trade_id"],
            "price": t["price"],
            "quantity": t["quantity"],
            "aggressor": None,  # not recorded by the matching engine
        })

    regime_events = [ev.to_dict() for ev in engine.regimes.events]
    shock_events = [ev.to_dict() for ev in engine.shocks.events]
    annotations = {
        "regimes": regime_events,
        "shocks": shock_events,
        "regime_intervals": _regime_intervals(regime_events,
                                              config.total_steps),
    }

    fingerprint = extract_fingerprint(config, engine.snapshots, engine.trades,
                                      regime_events, shock_events)

    identity_payload = {
        "world_schema_version": WORLD_SCHEMA_VERSION,
        "research_hash": version.research_hash,
        "version_id": version_id,
        "cell": cell,
        "replication": replication,
        "seed": seed,
        "config_hash": config_hash(config),
        "state_hash": engine.state_hash(),
        "event_hash": engine.event_hash(),
        "time_mapping": {"epoch_ns": WORLD_EPOCH_NS, "step_ns": STEP_NS},
        "quotes_checksum": _sha(quotes),
        "trades_checksum": _sha(trades),
        "annotations_checksum": _sha(annotations),
        "fingerprint": fingerprint.model_dump(mode="json"),
    }
    world_hash = _sha(identity_payload)
    manifest = {
        **identity_payload,
        "world_hash": world_hash,
        "world_id": f"mw_{world_hash[:12]}",
        "n_quotes": len(quotes),
        "n_quote_gaps": n_quote_gaps,
        "n_trades": len(trades),
        "external_context": version.external_context,
        "instrument_symbol": config.market.symbol,
        "tick_size": config.market.tick_size,
    }
    return MarketWorld(manifest, quotes, trades, annotations)


# ---------------------------------------------------------------------------
# Store (immutable, content-addressed, idempotent)
# ---------------------------------------------------------------------------
class WorldStore:
    def __init__(self, root: str = "data"):
        self.root = Path(root) / "worlds"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

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

    def save(self, world: MarketWorld) -> str:
        """Persist a world; idempotent for identical content, collision-safe."""
        wid = world.world_id
        with self._lock:
            d = self.root / wid
            existing = self._read(d / "manifest.json")
            if existing is not None:
                if existing["world_hash"] != world.world_hash:
                    raise WorldError(f"world id collision at {wid}")
                return wid
            self._write(d / "manifest.json", world.manifest)
            self._write(d / "quotes.json", world.quotes)
            self._write(d / "trades.json", world.trades)
            self._write(d / "annotations.json", world.annotations)
            index = self._index()
            index[wid] = {
                "world_id": wid,
                "version_id": world.manifest["version_id"],
                "cell": world.manifest["cell"],
                "replication": world.manifest["replication"],
                "seed": world.manifest["seed"],
                "n_quotes": world.manifest["n_quotes"],
                "n_trades": world.manifest["n_trades"],
                "crash_detected": world.manifest["fingerprint"]["crash_detected"],
                "regime_occupancy": world.manifest["fingerprint"]["regime_occupancy"],
            }
            self._write(self.root / "index.json", index)
        return wid

    def list(self) -> List[Dict[str, Any]]:
        return sorted(self._index().values(), key=lambda r: r["world_id"])

    def load(self, world_id: str) -> MarketWorld:
        """Load a world and re-verify its stream checksums and hash."""
        d = self.root / world_id
        manifest = self._read(d / "manifest.json")
        if manifest is None:
            raise WorldError(f"unknown world {world_id!r}")
        quotes = self._read(d / "quotes.json")
        trades = self._read(d / "trades.json")
        annotations = self._read(d / "annotations.json")
        if quotes is None or trades is None or annotations is None:
            raise WorldError(f"world {world_id} artifacts missing — corrupt store")
        for name, payload, key in (("quotes", quotes, "quotes_checksum"),
                                   ("trades", trades, "trades_checksum"),
                                   ("annotations", annotations,
                                    "annotations_checksum")):
            if _sha(payload) != manifest[key]:
                raise WorldError(
                    f"world {world_id} {name} artifact does not match its "
                    "registered checksum — refusing to load tampered data")
        recomputed = _sha({k: manifest[k] for k in (
            "world_schema_version", "research_hash", "version_id", "cell",
            "replication", "seed", "config_hash", "state_hash", "event_hash",
            "time_mapping", "quotes_checksum", "trades_checksum",
            "annotations_checksum", "fingerprint")})
        if recomputed != manifest["world_hash"]:
            raise WorldError(f"world {world_id} manifest does not re-hash to "
                             "its stored world_hash")
        return MarketWorld(manifest, quotes, trades, annotations)
