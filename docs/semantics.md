# Financial Semantics Contract (Phase F1)

This document is the executable-law reference for Tezcat's market accounting.
Every rule here is enforced by code and covered by tests
(`tests/unit/test_semantics.py`, `tests/unit/test_invariants.py`,
`tests/unit/test_matching.py`, `tests/unit/test_order_book.py`). A behavior
not written here should not be relied upon.

## Order lifecycle

```
submit → validate ─ reject ("rejected", no side effects)
              │
              ├─ reserve resources (sell: inventory; limit buy: price × qty cash)
              ├─ match against opposite side (price-time priority,
              │   execution at the *resting* order's price)
              ├─ limit remainder rests ("new"/"partial")
              ├─ market remainder is discarded ("cancelled", reservations released)
              └─ full fill → "filled"
resting → cancel/expire → reservations released → "cancelled"/"expired"
```

## Validation rules (in evaluation order)

1. Unknown agent → `"unknown agent"`.
2. Order ID already resting in the book → `"duplicate order id"`.
   `OrderBook.add_limit` additionally raises `ValueError` as a last-resort
   defense; reaching it is a caller bug, not a market condition.
3. Quantity below `min_order_size` / above `max_order_size` → rejected.
4. Limit price that snaps below one tick → `"invalid limit price"`.
5. Sell without sufficient *available* (unreserved) inventory when
   `allow_short` is false → rejected.
6. Buy without sufficient *available* cash when `allow_negative_cash` is
   false → rejected (limit: full notional; market: one minimum order at the
   best-ask reference).

## Reservation law

Reserved cash must equal, at all times outside a matching call, the exact sum
of `price × remaining` over the agent's resting buy orders; reserved
inventory must equal the sum of `remaining` over resting sell orders. This is
asserted by `EcologyEngine.check_invariants` and by randomized-stream
property tests. Reservations are released on every terminal path: fill (at
the reserved limit price), cancel, expiry, market-remainder discard, and
self-trade-prevention cancellation.

## Self-trade policy (`MarketConfig.self_trade_policy`)

- `"allow"` (default, legacy baseline): an incoming order may match the same
  agent's resting order. Cash and inventory net to zero; realized PnL and
  average cost are perturbed. This default preserves the frozen F0 baseline
  byte-for-byte and is scheduled to flip to `cancel_resting` in a future
  schema version.
- `"cancel_resting"` (standard STP): the resting order is cancelled, its
  reservations released, and matching continues at the next level.

## Cancellation consistency

`OrderBook.cancel` drops the order-ID record only *after* successful removal
from the book side. A tracked order missing from its side raises
`RuntimeError` ("order book inconsistency") instead of silently returning
`None` — silent failure would leak the caller's reservations permanently.

## Conservation laws (checked at run completion)

- **Inventory:** total inventory across all portfolios (agents + whale) is
  constant — the market creates and destroys nothing.
- **Cash:** with no fees or financing modeled, total cash is constant up to
  float accumulation drift (tolerance `max(1.0, 1e-9 × total)`).
- **Floors:** no negative cash (unless `allow_negative_cash`), no negative
  inventory (unless `allow_short`), reservations never exceed holdings.

## Monetary precision policy

Cash is binary floating point; prices are integer-tick multiples and
quantities are integers, so all settlement amounts are `tick_price × int`.

- `CASH_EPS = 1e-9` — comparison slack for order admission (`can_buy`).
- `INVARIANT_EPS = 1e-6` — slack for conservation/invariant assertions.

A discrepancy beyond `INVARIANT_EPS` is an accounting bug, never rounding
noise. If fees, financing, or fractional quantities are ever introduced,
this policy must be revisited (decimal or integer-cent ledger) *before*
implementation.

## Changes from the F0 baseline

| Change | Old behavior | New behavior | Rationale |
| --- | --- | --- | --- |
| Duplicate order IDs | Silently overwrote `_orders` entry; cancellation of the shadowed order became impossible; reservations could leak. | Rejected at validation; `add_limit` raises. | ID collision corrupts cancel/reservation accounting. |
| Self-trade | Implicitly allowed, undocumented. | Explicit `self_trade_policy` config; default `"allow"` preserves baseline. | The policy must be a declared experimental variable, not an accident. |
| Cancel inconsistency | Order record popped before side removal; on failure the record was lost and reservations leaked silently. | Record dropped only after successful removal; inconsistency raises. | Corruption must stop the run, not corrupt PnL quietly. |
| Precision | Implicit `1e-9`/`1e-6` magic numbers. | Named constants + written policy. | Tolerances are semantics, not implementation detail. |

Baseline preservation evidence: after all F1/F2 changes, `scripts/smoke.py`
reproduces the frozen F0 seed-42 figures byte-for-byte (same returns,
drawdowns, volumes, trade counts, regime shares).
