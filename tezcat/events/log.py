"""Canonical append-only event log (Phase F3).

Every financially meaningful state transition is recorded as an ordered
event. The log is hash-chained: each append folds the event's canonical JSON
into a running SHA-256 chain, so the chain head names the entire prefix and
any tampering, reordering, or divergence is detectable at the first bad
event.

Event record shape (all JSON-native)::

    {"seq": <int, 1-based strictly sequential>,
     "step": <simulation step>,
     "type": <event type string>,
     "data": {...}}

Event types (schema version 1)
------------------------------
order_accepted   order passed validation; reservations taken
                 (order_id, agent_id, side, order_type, price [snapped|None],
                  quantity, reserved_cash, reserved_inventory)
order_rejected   (order_id, agent_id, side, order_type, price, quantity, reason)
trade            (trade_id, price, quantity, buy_order_id, sell_order_id,
                  buy_agent_id, sell_agent_id)
order_rested     limit remainder entered the book
                 (order_id, price, remaining, status, seq)
order_discarded  market remainder discarded; reservations released
                 (order_id, side, remaining)
order_cancelled  (order_id, side, price, remaining, reason: agent|self_trade)
order_expired    (order_id, side, price, remaining)
shock            shock fired (shock event record)
regime_transition regime engine transitioned (regime event record)
step_ended       end-of-step environment state
                 (step, last_price, sentiment, mm_withdrawn, regime)
intervention     fork-time declared intervention with parent lineage
                 (name, operations, parent_run_id, checkpoint_step,
                  checkpoint_hash)

The full accounting semantics implied by each event are documented in
docs/events.md; the replay kernel (tezcat.events.replay) is the executable
definition.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from tezcat.core.config import canonical_json

#: Version of the event schema; bump on any change to types or payloads.
EVENT_SCHEMA_VERSION = 1


class ReplayError(ValueError):
    """Raised on duplicate, missing, or out-of-order events during replay."""


class EventLog:
    """Ordered, hash-chained event log for one run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: List[Dict[str, Any]] = []
        self._seq = 0
        # Chain genesis binds the log to run identity and schema version.
        self.chain = hashlib.sha256(
            f"tezcat-events-v{EVENT_SCHEMA_VERSION}:{run_id}".encode()
        ).hexdigest()

    def append(self, step: int, type_: str, data: Dict[str, Any]) -> Dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "step": step, "type": type_, "data": dict(data)}
        self.chain = hashlib.sha256(
            (self.chain + canonical_json(event)).encode()
        ).hexdigest()
        self.events.append(event)
        return event

    def __len__(self) -> int:
        return self._seq

    # -- serialization (used by checkpoints and artifacts) --------------
    def state_dict(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "seq": self._seq, "chain": self.chain,
                "events": self.events}

    def load_state(self, state: Dict[str, Any]) -> None:
        self._seq = state["seq"]
        self.chain = state["chain"]
        self.events = list(state["events"])

    @staticmethod
    def verify_chain(run_id: str, events: List[Dict[str, Any]]) -> str:
        """Recompute the chain over an event list; raises ReplayError on a
        sequence gap. Returns the chain head."""
        chain = hashlib.sha256(
            f"tezcat-events-v{EVENT_SCHEMA_VERSION}:{run_id}".encode()
        ).hexdigest()
        expected_seq = 1
        for ev in events:
            if ev["seq"] != expected_seq:
                raise ReplayError(
                    f"event sequence violation: expected seq {expected_seq}, "
                    f"got {ev['seq']} (duplicate or missing event)")
            chain = hashlib.sha256((chain + canonical_json(ev)).encode()).hexdigest()
            expected_seq += 1
        return chain
