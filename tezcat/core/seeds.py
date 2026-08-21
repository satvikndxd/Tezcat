"""Deterministic seed derivation and allocation.

A ``SeedPlan`` maps a single root seed to reproducible child seeds for
replications and named random streams. Derivation is pure SHA-256 over the
canonical component tuple, so it is independent of process, platform hash
randomization, and scheduling order.

``ALLOCATOR_VERSION`` is part of every derivation. If the derivation rule
ever changes, the version must be bumped so old and new seeds can never be
confused for one another.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union

#: Bump whenever the derivation function changes (part of the hash input).
ALLOCATOR_VERSION = 1

_SEED_SPACE = 2**63  # seeds fit in a signed 64-bit integer

Component = Union[str, int]


def derive_seed(root_seed: int, *components: Component) -> int:
    """Derive a child seed from a root seed and a component path.

    Deterministic across processes and platforms. Examples::

        derive_seed(42, "replication", 0)
        derive_seed(42, "replication", 3, "stream", "agent_decisions")
    """
    parts = [f"tezcat-seed-v{ALLOCATOR_VERSION}", str(int(root_seed))]
    for c in components:
        if not isinstance(c, (str, int)):
            raise TypeError(f"seed component must be str or int, got {type(c).__name__}")
        parts.append(f"{type(c).__name__}:{c}")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _SEED_SPACE


@dataclass(frozen=True)
class SeedPlan:
    """Deterministic seed allocation for one experiment version.

    ``replication_seed(i)`` is stable regardless of which worker executes
    replication ``i`` or in what order replications are scheduled.
    """

    root_seed: int
    allocator_version: int = ALLOCATOR_VERSION
    stream_names: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.allocator_version != ALLOCATOR_VERSION:
            raise ValueError(
                f"seed plan built for allocator v{self.allocator_version}, "
                f"but this runtime implements v{ALLOCATOR_VERSION}"
            )

    def replication_seed(self, index: int) -> int:
        if index < 0:
            raise ValueError("replication index must be >= 0")
        return derive_seed(self.root_seed, "replication", index)

    def replication_seeds(self, count: int) -> List[int]:
        return [self.replication_seed(i) for i in range(count)]

    def stream_seed(self, replication_index: int, stream: str) -> int:
        return derive_seed(self.root_seed, "replication", replication_index,
                           "stream", stream)

    def to_dict(self) -> Dict[str, object]:
        return {
            "root_seed": self.root_seed,
            "allocator_version": self.allocator_version,
            "stream_names": list(self.stream_names),
        }
