# Events, Replay, Checkpoints, and Forks (Phase F3)

## The event log

Every financially meaningful transition is recorded in an append-only,
hash-chained log (`tezcat.events.log.EventLog`). Each event is

```json
{"seq": 421, "step": 17, "type": "trade", "data": {...}}
```

with `seq` strictly sequential from 1. The chain head is
`sha256(prev_chain + canonical_json(event))` seeded with the run identity and
event-schema version, so it names the entire prefix: tampering, reordering,
duplication, or omission is detected at the first bad event
(`EventLog.verify_chain`).

### Event types (schema v1)

| Type | Emitted when | State semantics |
| --- | --- | --- |
| `order_rejected` | validation failed | none (by definition) |
| `order_accepted` | validation passed | reservations taken (sell: inventory; limit buy: `price × qty` cash); payload records the snapped price and reserved amounts |
| `trade` | each fill | settlement: buyer releases limit reservation for the filled qty, seller releases inventory, then `apply_buy`/`apply_sell`; both orders' `remaining` decrement; an exhausted resting order leaves the book as `filled` |
| `order_rested` | limit remainder enters the book | order rests at `price` with `remaining`, FIFO `seq`, `status` |
| `order_discarded` | market remainder discarded | sell reservations for `remaining` released |
| `order_cancelled` | agent cancel or self-trade prevention (`reason`) | order leaves book; reservations for `remaining` released |
| `order_expired` | max-age expiry | same as cancel with status `expired` |
| `shock` | shock fires | forensic record (market before/after, payload) |
| `regime_transition` | regime engine transitions | forensic record |
| `step_ended` | end of every step | environment truth: `last_price`, `sentiment`, `mm_withdrawn`, `regime` |
| `intervention` | fork creation | declared operations + parent lineage |

## Replay

`tezcat.events.replay.ReplayKernel` is the **executable definition of event
completeness**: it consumes the event stream into a clean kernel — no agent
decisions, no RNG — and rebuilds the book and every portfolio by applying
the same float operations in the same order as the matching engine. Tests
require the replayed `state_hash` to equal the live engine's bit for bit,
under both self-trade policies, with and without shocks, and at intermediate
prefixes.

Duplicate/ordering policy is explicit: replay raises `ReplayError` on any
`seq` that is not exactly the next expected value.

Forensics: the crash chain is reconstructible from events alone — shock →
whale orders (`order_accepted` with `agent_id="whale"`) → attributable
trades → environment path in `step_ended` records
(`tests/unit/test_events_replay.py::test_forensic_shock_chain_from_events_alone`).

## Checkpoints

`EcologyEngine.checkpoint()` returns a strictly JSON-native dict capturing
the *complete* kernel state: RNG state, book (orders in price-level FIFO
**and** arrival order — expiry iterates the arrival-ordered map, so that
order is semantic), portfolios, agent memories and open-order references,
environment, shock/regime/metrics engine internals, all counters, the event
log with its chain head, and accumulated history (trades, snapshots).

Guarantees (tested):

- **Continuity:** restore + continue is bit-identical to the uninterrupted
  run — same state hash, event hash, event chain, trades, and report.
- **JSON durability:** a checkpoint survives `json.dumps`/`loads` exactly.
- **Compatibility:** restore refuses a config whose hash differs from the
  checkpoint's recorded `config_hash`, and refuses unknown
  `checkpoint_version`s.
- **Identity:** `checkpoints.checkpoint_hash` is content-addressed — same
  state, same hash.

Checkpoints are inter-step (between `step()` calls); mid-step checkpointing
is undefined.

## Forks

`EcologyEngine.fork(checkpoint, config, new_run_id, intervention)` restores
the parent state under a new run identity and records an `intervention`
event carrying the parent lineage (`parent_run_id`, `checkpoint_step`,
`checkpoint_hash`) *before* any new market activity. Supported operations
(F3 scope):

```json
{"op": "inject_shock", "shock_type": "whale_order", "side": "sell",
 "magnitude": 800, "duration": 5}
{"op": "set_sentiment", "value": -0.9, "duration": 30}
{"op": "withdraw_mm", "duration": 20}
```

Tested properties: the child's trade/event prefix equals the parent's; a
no-op fork continues on the parent's exact market path (a pure control);
a real intervention diverges only after the fork point. Structural changes
at fork time (agent parameters, market rules) are **not** supported in F3 —
that requires the F4 experiment-version model.

Terminology rule: a fork comparison is a **controlled intervention** inside
the specified model — not automatically a causal claim about real markets.

## Costs (measured, flash-crash preset, 2,000 steps)

Event logging with per-event chain hashing roughly doubles engine runtime
(~0.8s → ~1.6s) and produces ~150k events (~29MB serialized JSON), persisted
as the `events/events.json` artifact. This is the deliberate research-mode
default; compressed encodings and snapshot-interval logging are the listed
F3 performance follow-ups if scale demands them.
