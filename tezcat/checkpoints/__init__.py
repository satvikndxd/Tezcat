"""Checkpoint identity and persistence helpers (Phase F3).

A checkpoint is the JSON-native dict produced by ``EcologyEngine.checkpoint``.
Its content hash names the exact kernel state; forks record it as lineage.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

from tezcat.core.config import canonical_json


def checkpoint_hash(checkpoint: Dict[str, Any]) -> str:
    """Content hash of a checkpoint (canonical JSON, sha256)."""
    return hashlib.sha256(canonical_json(checkpoint).encode()).hexdigest()


def lineage(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    """The parent-lineage record a fork should carry."""
    return {
        "parent_run_id": checkpoint["run_id"],
        "checkpoint_step": checkpoint["step"],
        "checkpoint_hash": checkpoint_hash(checkpoint),
        "config_hash": checkpoint["config_hash"],
        "seed": checkpoint["seed"],
    }
