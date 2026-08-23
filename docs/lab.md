# Strategy Lab — the Tezcat ↔ NautilusTrader bridge (Phase S4)

Tezcat S4 adds a **two-layer financial laboratory**:

* **Layer A — market ecology (Tezcat).** What market environment emerges
  from interacting agents?
* **Layer B — participant/execution system (NautilusTrader).** How does a
  concrete strategy/execution stack behave *inside* that environment?

```
 Tezcat ExperimentVersion ── cell, replication, seed
            │
            ▼
      MARKET WORLD  (deterministic, immutable, content-addressed)
      canonical stream + ecology fingerprint
            │
            ▼
      NAUTILUS BRIDGE  (export → QuoteTick / TradeTick)
            │
            ▼
      BacktestEngine + hashed library strategy
            │
            ▼
      RESULT ARTIFACT  (metrics + regime breakdown + provenance chain)
            │
            └────────► research hash → world hash → strategy hash
```

Neither engine replaces the other: Tezcat stays the market-ecology lab,
Nautilus stays the participant/execution system, and the boundary is an
explicit export contract. The first milestone — **Strategy Under
Synthetic Worlds** — is strictly backtest/research; there is no live
trading, no sandbox, and no order that ever leaves the process.

## Market Worlds (`tezcat/worlds`)

A world is *one deterministic realization* of a registered experiment
cell: `(version, cell, replication)` → seed → `EcologyEngine` run →
canonical stream. Same registry + same reference ⇒ byte-identical world.

The canonical stream is framework-neutral and fully declared:

| Part | Content | Honesty rules |
|---|---|---|
| quotes | one L1 row/step: bid, ask, sizes | sizes = aggregate side depth (closest honest L1 mapping); steps with an empty side are skipped **and counted** (`n_quote_gaps`), never fabricated |
| trades | price, quantity, trade id | `aggressor = null` — Tezcat's matcher does not record taker side and the export does not guess |
| annotations | regime transitions, shocks, derived regime intervals | research metadata *next to* market data, not mixed into it |

Time mapping (part of the world hash): step *s* → `2020-01-01T00:00:00Z
+ s` seconds; intra-step trades get +i ns, the end-of-step quote closes
the step at +(1s − 1ns). The stream is strictly ordered and chronology
is deterministic.

The **world hash** covers the schema version, the experiment's research
hash, seed/config/state/event hashes, the time mapping, all stream
checksums, and the fingerprint. `WorldStore` refuses tampered artifacts
on load.

## Ecology Fingerprint

A derived, versioned, hashable summary of the environment — a
**market-environment identity**, so a result can say "strategy X under
ecology fingerprint Y" instead of "against some file". Purely numeric
(no subjective grades): relative spread, depth quantiles, order
imbalance, return volatility, Hill tail α, volatility clustering, max
drawdown/crash flag, trade intensity, regime occupancy, shock/transition
counts, and the configured agent composition. `FINGERPRINT_VERSION` is
part of the hash — feature-definition changes mint new identities.

## The bridge (`tezcat/lab`)

* **Optional dependency.** `nautilus_trader` is the `lab` extra
  (`pip install -e ".[lab]"`); the research kernel never imports it. All
  bridge imports are lazy and fail with a clear message when absent
  (tests skip cleanly).
* **Standard data types.** Quotes → `QuoteTick`, trades → `TradeTick`
  (with `NO_AGGRESSOR`) — no parallel pseudo-Nautilus format. Regime and
  shock annotations stay Tezcat-side and drive regime-conditioned
  metrics.
* **Strict validation.** Crossed quotes, duplicate/regressing
  timestamps, non-positive prices/quantities, and missing fields are
  rejected before conversion, never repaired.
* **Hashed strategies.** The reference library (`buy_hold`, `ema_cross`,
  `mean_reversion`) is deliberately simple: research probes, not trading
  advice. `strategy_hash` covers library version + id + exact params, so
  "the same strategy across different worlds" is verifiable.

## Metrics — deterministic derivation

Every reported number derives from primary records (the strategy's own
submission/fill log + the world quote stream); Nautilus' own summary
stats are attached verbatim under `nautilus_stats` for cross-reference.

* equity curve = cash + inventory × mid at every world quote → total
  return, max drawdown, exposure
* turnover = Σ |qty × price|; fill rate = fills / submissions
* slippage per fill = signed cost vs prevailing mid (buy: fill − mid);
  with L1 quotes this is dominated by the half-spread — exactly the
  execution cost the ecology imposes
* **regime breakdown**: every equity change and fill is attributed to
  the regime interval covering its timestamp — Tezcat's own regime
  detection exported as annotations, not reconstructed after the fact

## Provenance and reproduction

A result is not a backtest file. Its identity is
`sha256(lab schema, world hash, strategy hash, backtest config)`; the
artifact stores the full chain — research hash → world hash → strategy
hash → config — plus the execution environment (`nautilus_trader`
version, Python, platform).

`tezcat lab reproduce <result>` re-runs the chain: rebuild the world
from the registry (world hash must match), re-run the backtest, compare
every metric **byte-for-byte** through the same canonical JSON. The
declared determinism boundary is *same world + same strategy config +
same `nautilus_trader` version + same platform*; an environment mismatch
is reported as "non-identical" and fails — never silently accepted.

## Workflows

```bash
# Strategy Under Synthetic Worlds (first milestone)
tezcat worlds build expv_… --cell control          # deterministic world A
tezcat worlds build expv_… --cell high_herding     # deterministic world B
tezcat worlds show mw_…                            # manifest + fingerprint
tezcat worlds export mw_… --target nautilus        # canonical stream file
tezcat lab run mw_A mw_B --strategy ema_cross --param trade_size=50
tezcat lab compare lab_… lab_…                     # same strategy, different worlds
tezcat lab reproduce lab_…                         # full-chain verification
```

Same surface over HTTP under `/api/lab/*`, and in the dashboard as
**LAB** (build worlds → inspect fingerprints → run backtests → compare →
reproduce). Combined with S3, the full chain becomes: external
observation → signature → synthetic experiment → market worlds →
strategy exposure — with one provenance thread through all of it.

## Explicitly out of scope (future, gated)

* **Live/sandbox trading** — a separately gated project; nothing in this
  bridge touches an exchange.
* **Participant-in-the-ecology / co-simulation** (a Nautilus strategy as
  one more Tezcat agent). Not implemented, deliberately: it requires an
  explicit synchronization contract first — time authority, same-
  timestamp event ordering, market-boundary visibility, latency model,
  feedback timing, determinism, checkpointing, and a reproducible
  identity for the coupled run. No code before those semantics are
  specified.
* Ecology-space robustness surfaces (leverage × liquidity × herding
  grids) compose today from existing pieces — sweep/factorial
  experiments already generate per-cell worlds via `tezcat worlds build
  --cell` — but a dedicated surface product is second-milestone work.

## Limitations (stated, not hidden)

* The strategy is an **observer**: its orders fill against the exported
  stream inside Nautilus and do not change the Tezcat ecology. Claims
  about market impact of the strategy are out of scope by construction.
* L1 export: Nautilus fills at top-of-book quotes with aggregate-depth
  sizes; book-walking impact beyond L1 is not modeled in the bridge yet.
* Synthetic worlds are synthetic. Results describe strategy behavior
  under a specified model ecology — never a prediction about real
  markets, and never financial advice.
