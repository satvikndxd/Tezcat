"""Reproduction verification (Phase F10).

``reproduce()`` takes a registered experiment version (by id or research
hash), re-executes runs from its stored design, and compares every hash the
original batch persisted: config hash, seed, state hash, event hash,
event-log chain, and the declared metrics — byte for byte.

Verification ladder (first failure names the divergence class):

1. registry record found (id, hash, or unambiguous hash prefix)
2. research hash re-verifies (schema / code / seed-allocator drift fails here)
3. stored batch rows exist
4. per run: seed matches the allocator, then state hash, event hash,
   event-log chain, and metrics match the stored row

By default a deterministic sample of rows is re-executed (first, middle,
last of the design matrix); ``sample=None`` re-executes everything.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.batch import batch_id_for
from tezcat.experiments.registry import Registry, RegistryError


class ReproductionError(ValueError):
    pass


def resolve_reference(registry: Registry, ref: str) -> str:
    """Resolve a version id, full research hash, or hash prefix (>=8 chars)."""
    rows = registry.list()
    for row in rows:
        if row["version_id"] == ref or row["research_hash"] == ref:
            return row["version_id"]
    if len(ref) >= 8:
        matches = [r for r in rows if r["research_hash"].startswith(ref)
                   or r["version_id"].startswith(ref)]
        if len(matches) == 1:
            return matches[0]["version_id"]
        if len(matches) > 1:
            raise ReproductionError(
                f"reference {ref!r} is ambiguous: matches "
                f"{[m['version_id'] for m in matches]}")
    raise ReproductionError(f"no registered experiment matches {ref!r}")


def reproduce(registry: Registry, ref: str,
              sample: Optional[int] = 3) -> Dict[str, Any]:
    """Re-execute and verify a registered experiment. Never mutates artifacts."""
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    version_id = resolve_reference(registry, ref)
    check("experiment identified", True, version_id)

    try:
        version = registry.load(version_id)  # re-verifies research hash
        check("research hash verified", True, version.research_hash[:16] + "…")
    except (RegistryError, ValueError) as exc:
        check("research hash verified", False, str(exc))
        return _result(version_id, ref, checks, verified=0, failed=1)

    external_context = version.external_context
    if external_context is not None:
        # Reproduction of an external-data experiment means re-executing it
        # *against the exact dataset version it was built from*: the stored
        # manifest must match the hashed context, and the on-disk artifacts
        # must re-hash to their registered checksums (the store re-verifies
        # them on load and raises on any tamper).
        try:
            dataset_store = registry.external_dataset_store()
            dataset_id = external_context.get("dataset_id")
            manifest = dataset_store.get(dataset_id)
            dataset_store.observations(dataset_id)   # checksum-verified load
            if manifest.raw_checksum:
                dataset_store.raw(dataset_id)        # checksum-verified load
            checksums_ok = (
                manifest.dataset_hash == external_context.get("dataset_hash")
                and manifest.normalized_checksum == external_context.get("dataset_checksum")
                and manifest.raw_checksum == external_context.get("raw_checksum")
            )
            check(
                "external dataset identity verified",
                checksums_ok,
                f"{dataset_id}: dataset hash + raw/normalized checksums"
                if checksums_ok else f"{dataset_id}: checksum or manifest mismatch",
            )
            if not checksums_ok:
                return _result(version_id, ref, checks, verified=0, failed=1)
        except Exception as exc:
            check("external dataset identity verified", False, str(exc))
            return _result(version_id, ref, checks, verified=0, failed=1)

    bid = batch_id_for(version)
    rows = {r["key"]: r for r in registry.list_result_rows(bid)}
    if not check("stored batch rows found", bool(rows),
                 f"{len(rows)} rows in {bid}"):
        return _result(version_id, ref, checks, verified=0, failed=1)

    keys = sorted(rows)
    if sample is not None and len(keys) > sample:
        if sample <= 1:
            keys = [keys[0]]
        else:
            # Deterministic spread: first, last, and evenly spaced middles.
            idx = sorted({round(i * (len(keys) - 1) / (sample - 1))
                          for i in range(sample)})
            keys = [keys[i] for i in idx]

    verified = failed = 0
    for key in keys:
        stored = rows[key]
        cell, rep = stored["cell"], stored["replication"]
        expected_seed = version.seed_for(cell, rep)
        if not check(f"{key}: seed allocation", stored["seed"] == expected_seed,
                     f"stored {stored['seed']}"):
            failed += 1
            continue

        config = version.cell_config(cell)
        engine = EcologyEngine(stored["run_id"], config, expected_seed)
        while not engine.done:
            engine.step()
        report = engine.build_report()
        fresh_metrics = {dv: report[dv]
                         for dv in version.design.dependent_variables}

        ok = True
        ok &= check(f"{key}: state hash",
                    engine.state_hash() == stored["state_hash"])
        ok &= check(f"{key}: event hash",
                    engine.event_hash() == stored["event_hash"])
        ok &= check(f"{key}: event-log chain",
                    engine.events_log.chain == stored["event_log_chain"])
        ok &= check(f"{key}: metrics",
                    fresh_metrics == stored["metrics"])
        verified += ok
        failed += not ok

    return _result(version_id, ref, checks, verified, failed,
                   sampled=len(keys), total_rows=len(rows))


def _result(version_id: str, ref: str, checks: List[Dict[str, Any]],
            verified: int, failed: int, sampled: int = 0,
            total_rows: int = 0) -> Dict[str, Any]:
    return {
        "reference": ref,
        "version_id": version_id,
        "checks": checks,
        "runs_verified": verified,
        "runs_failed": failed,
        "runs_sampled": sampled,
        "total_rows": total_rows,
        "success": failed == 0 and verified > 0,
    }
