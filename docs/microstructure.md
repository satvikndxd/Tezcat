# Microstructure and TCA Metrics (Phase F7)

Every flow metric is computed by replaying the run's canonical event log
through an embedded `ReplayKernel` (`tezcat.microstructure.flow`), so metric
values have **exact lineage to source events**: the analyzer's replayed book
must land on the live engine's `state_hash` (asserted in tests), and the
pre-trade mids it uses are the proven-correct replayed quotes.

## Point metrics (`tezcat.microstructure.metrics`)

| Metric | Definition | Units | Undefined when |
| --- | --- | --- | --- |
| microprice | `(ask_qty·bid + bid_qty·ask)/(bid_qty+ask_qty)` — leans toward the thinner queue | price | one-sided book, zero depth |
| queue_imbalance | `(bid_qty−ask_qty)/(bid_qty+ask_qty)` at level 1, in [−1, 1] | — | zero depth |
| relative_spread | `(ask−bid)/mid` | fraction of mid | one-sided book |

`None` is returned for undefined states — never a fabricated number.

## Flow metrics (per trade, horizon *h* end-of-step mids)

| Metric | Definition | Interpretation |
| --- | --- | --- |
| aggressor sign | +1 if the incoming order buys, −1 if it sells (inferred from event order: trades follow their aggressor's `order_accepted`) | trade direction |
| effective spread | `2·sign·(p − mid_pre)` | what the aggressor paid vs. pre-trade mid |
| realized spread | `2·sign·(p − mid_{t+h})` | what the liquidity provider kept after the move |
| price impact | `sign·(mid_{t+h} − mid_pre)` | permanent move attributable to the trade window |

The identity `effective = realized + 2·impact` holds by construction and is
asserted in tests. Trades too close to the end of the run for the horizon
are **skipped and counted** (`skipped_no_horizon_or_reference`), not
silently dropped.

## Order lifecycle metrics

- **Queue position at rest**: shares already at the price level when a limit
  order rests (FIFO shares ahead).
- **Fill statistics** (limit orders): fill probability (fully filled),
  mean fill fraction, mean steps to first fill.
- **Cancellation intensity**: cancels + expiries per step.
- **Signed volume**: aggressor-signed trade quantity per step.

## TCA (`per_order_tca`)

Zero-fee market — fees are reported explicitly as `0.0`, not omitted.
Perold-style shortfall decomposition per order with an arrival mid:

```
slippage/share          = sign · (avg_fill − arrival_mid)
execution cost          = slippage/share · filled
opportunity cost        = sign · (final_mid − arrival_mid) · unfilled   (terminal orders)
implementation shortfall = execution + opportunity
```

Orders arriving into an empty book have no reference mid and are counted in
`orders_without_reference_mid` rather than being invented. Aggregation by
agent type must partition all TCA orders (tested).

## Queue-imbalance association

`qi_association` reports the correlation between level-1 queue imbalance at
step *t* and the mid move to *t+1*, with sample size. This is a
**within-model experimental association** — real-market QI predictability
depends on tick size, sampling, horizon, and model specification (Gould &
Bonart 2016), and no real-market claim is made here.

## Deliberately absent

- **Latency categories** — the engine has no latency model yet; reporting
  latency metrics would fabricate structure. Deferred to the phase that
  adds latency mechanics.
- **Persistence wiring** — the analyzer is opt-in (call it on a run's
  event artifact); it is not yet part of the default run pipeline, per the
  F7 rollback rule that metrics stay opt-in until definitions stabilize.

## Usage

```python
from tezcat.microstructure import MicrostructureAnalyzer

out = MicrostructureAnalyzer(config, run_id, horizon=5).run(events)
out["trades"]["mean_effective_spread"]
out["fills"]["fill_probability"]
out["tca"]["by_agent_type"]["market_maker"]
assert out["replay_state_hash"] == engine.state_hash()   # lineage check
```
