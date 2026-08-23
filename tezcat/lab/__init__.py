"""Strategy Lab (Phase S4): the Tezcat ↔ NautilusTrader bridge.

Two-layer laboratory:

* **Layer A — market ecology (Tezcat).** What environment emerges from
  interacting agents? Realized as immutable :mod:`tezcat.worlds`.
* **Layer B — participant/execution system (NautilusTrader).** How does a
  concrete strategy/execution stack behave inside that environment?

The bridge is strictly **backtest/research oriented**: worlds are exported
into Nautilus' standard data types and replayed through its backtest
engine. No live trading, no sandbox, no real exchange — and Nautilus never
becomes a dependency of the Tezcat research kernel: everything in this
package imports Nautilus lazily and fails with a clear message when the
optional dependency is absent.

Boundary rules (Phase S4, first milestone):

* Tezcat's engine is not replaced; Nautilus' engine is not replaced.
* The strategy is an *observer* of the world: its orders fill against the
  exported stream inside Nautilus and never feed back into the Tezcat
  ecology. Participant-in-the-ecology co-simulation is future work gated
  on an explicit synchronization contract (see docs/lab.md).
* Every result carries the full provenance chain — research hash → world
  hash → strategy hash → backtest config — and reproduction re-runs the
  chain, failing loudly on environment mismatch. "Close enough" is not
  accepted.
"""

from tezcat.lab.strategies import (  # noqa: F401
    STRATEGY_LIBRARY_VERSION, LabError, list_strategies, strategy_hash,
)
from tezcat.lab.results import LabResultStore  # noqa: F401
