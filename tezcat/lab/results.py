"""Strategy Lab results: provenance-linked artifacts + reproduction (S4).

A lab result is **not a backtest file** — it is an artifact whose identity
is the content hash of (lab schema version, world hash, strategy hash,
backtest config). Metrics live inside the artifact; the identity does not
depend on them, so reproduction is a real check: rebuild the world from
the registry, re-run the backtest, and compare every stored metric
exactly. Environment mismatch (e.g. a different ``nautilus_trader``
version) fails loudly — never "close enough".

This is artifact storage, not a second experiment registry: research
identity stays with the Tezcat ``ExperimentVersion``; results reference
it through the provenance chain ``research_hash → world_hash →
strategy_hash → result``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tezcat.core.config import canonical_json
from tezcat.lab.backtest import LAB_SCHEMA_VERSION, run_backtest
from tezcat.lab.strategies import LabError
from tezcat.worlds.world import WorldStore, build_world


def result_id_for(world_hash: str, strategy_hash_: str,
                  backtest_config: Dict[str, Any]) -> str:
    payload = {"lab_schema_version": LAB_SCHEMA_VERSION,
               "world_hash": world_hash,
               "strategy_hash": strategy_hash_,
               "backtest_config": backtest_config}
    return f"lab_{hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:12]}"


class LabResultStore:
    def __init__(self, root: str = "data"):
        self.root = Path(root) / "lab"
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

    # ------------------------------------------------------------------
    def save(self, result: Dict[str, Any]) -> str:
        rid = result_id_for(result["world"]["world_hash"],
                            result["strategy"]["strategy_hash"],
                            result["backtest_config"])
        record = {"result_id": rid, **result}
        with self._lock:
            path = self.root / rid / "result.json"
            existing = self._read(path)
            if existing is not None:
                return rid  # identity is content-addressed; idempotent
            self._write(path, record)
            index = self._index()
            index[rid] = {
                "result_id": rid,
                "world_id": result["world"]["world_id"],
                "version_id": result["world"]["version_id"],
                "cell": result["world"]["cell"],
                "strategy_id": result["strategy"]["strategy_id"],
                "total_return": result["metrics"]["total_return"],
                "max_drawdown": result["metrics"]["max_drawdown"],
                "n_fills": result["metrics"]["n_fills"],
            }
            self._write(self.root / "index.json", index)
        return rid

    def list(self) -> List[Dict[str, Any]]:
        return sorted(self._index().values(), key=lambda r: r["result_id"])

    def get(self, result_id: str) -> Dict[str, Any]:
        record = self._read(self.root / result_id / "result.json")
        if record is None:
            raise LabError(f"unknown lab result {result_id!r}")
        return record

    # ------------------------------------------------------------------
    def compare(self, result_ids: List[str]) -> Dict[str, Any]:
        """Side-by-side comparison of one strategy across worlds (or
        several strategies on one world). Purely tabular; no ranking
        heuristics."""
        if len(result_ids) < 2:
            raise LabError("comparison needs >= 2 results")
        records = [self.get(rid) for rid in result_ids]
        strategies = {r["strategy"]["strategy_hash"] for r in records}
        worlds = {r["world"]["world_hash"] for r in records}
        rows = []
        for r in records:
            m = r["metrics"]
            rows.append({
                "result_id": r["result_id"],
                "world_id": r["world"]["world_id"],
                "cell": r["world"]["cell"],
                "strategy_id": r["strategy"]["strategy_id"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "turnover": m["turnover"],
                "fill_rate": m["fill_rate"],
                "mean_slippage_vs_mid": m["mean_slippage_vs_mid"],
                "regime_pnl": {k: v["pnl"]
                               for k, v in m["regime_breakdown"].items()},
            })
        return {
            "mode": ("same strategy, different worlds" if len(strategies) == 1
                     and len(worlds) > 1 else
                     "same world, different strategies" if len(worlds) == 1
                     and len(strategies) > 1 else "mixed"),
            "rows": rows,
            "note": "environmental sensitivity is isolated only when the "
                    "strategy hash is constant across rows",
        }


# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------
def reproduce_result(registry, world_store: WorldStore,
                     result_store: LabResultStore,
                     result_id: str) -> Dict[str, Any]:
    """Re-run the full chain and verify byte-level metric equality.

    Chain: registry → rebuild world (verify world hash) → re-run Nautilus
    backtest (verify environment) → compare metrics exactly. Any mismatch
    is a named failure; environment drift is reported as such, not
    absorbed.
    """
    import nautilus_trader

    stored = result_store.get(result_id)
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    env_ok = (stored["environment"]["nautilus_trader_version"]
              == nautilus_trader.__version__)
    check("nautilus environment matches", env_ok,
          f"stored {stored['environment']['nautilus_trader_version']} vs "
          f"current {nautilus_trader.__version__}")
    if not env_ok:
        return {"result_id": result_id, "success": False, "checks": checks,
                "reason": "environment mismatch — result declared "
                          "non-identical rather than silently accepted"}

    w = stored["world"]
    world = build_world(registry, w["version_id"], cell=w["cell"],
                        replication=w["replication"])
    check("world rebuilt from registry", True, world.world_id)
    if not check("world hash matches", world.world_hash == w["world_hash"],
                 f"{world.world_hash[:16]}… vs {w['world_hash'][:16]}…"):
        return {"result_id": result_id, "success": False, "checks": checks}

    rerun = run_backtest(world, stored["strategy"]["strategy_id"],
                         stored["strategy"]["params"],
                         starting_cash=stored["backtest_config"]["starting_cash"])
    # Byte-level comparison through the same JSON canonicalization the
    # artifact was stored with.
    stored_metrics = json.loads(canonical_json(stored["metrics"]))
    rerun_metrics = json.loads(canonical_json(rerun["metrics"]))
    ok = stored_metrics == rerun_metrics
    if not ok:
        diffs = [k for k in stored_metrics
                 if stored_metrics.get(k) != rerun_metrics.get(k)]
        check("metrics reproduce exactly", False,
              f"diverging fields: {diffs[:6]}")
    else:
        check("metrics reproduce exactly", True,
              f"{len(stored_metrics)} fields byte-identical")
    return {"result_id": result_id, "success": ok, "checks": checks}
