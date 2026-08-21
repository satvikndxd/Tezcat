"""Shared canonical state hashing (Phase F2/F3).

One definition of "semantic kernel state", used by both the live engine and
the event-replay kernel, so replay correctness is checked against exactly the
same hash the engine publishes. Floats are encoded with ``repr`` (shortest
exact round-trip form): two states hash equal iff they are bit-identical.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

from tezcat.core.config import SCHEMA_VERSION, canonical_json


def _side_state(side: Any) -> list:
    return [
        [t, [[o.order_id, o.agent_id, o.remaining, o.seq, o.status]
             for o in side.levels[t]]]
        for t in side.sorted_ticks
    ]


def compute_state_hash(book: Any, portfolios: Dict[str, Any], step: int,
                       last_price: float, sentiment: float, mm_withdrawn: bool,
                       regime: str) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "step": step,
        "last_price": repr(last_price),
        "book": {"bids": _side_state(book.bids), "asks": _side_state(book.asks)},
        "portfolios": {
            aid: [repr(p.cash), p.inventory, repr(p.reserved_cash),
                  p.reserved_inventory, repr(p.realized_pnl), repr(p.avg_cost)]
            for aid, p in sorted(portfolios.items())
        },
        "env": {"sentiment": repr(sentiment), "mm_withdrawn": mm_withdrawn},
        "regime": regime,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
