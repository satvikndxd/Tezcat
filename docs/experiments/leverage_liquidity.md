# Dogfood Study: Does Leverage Amplify the Destabilizing Effect of Thin Liquidity?

**Status:** completed · **Method:** entirely through the F10 product surface
(CLI + documented artifacts; no internal APIs touched) · **Date:** 2026-08-22

This is the first study conducted *as a user* of Tezcat rather than as its
author. It has two deliverables: the scientific result, and the product
frictions found along the way.

## Question and hypothesis

> Does increasing leverage amplify the destabilizing effect of declining
> market liquidity?

Pre-stated hypothesis: drawdown, forced liquidation, and crash frequency
rise with leverage, and rise *faster* when market-maker liquidity is thin
(a positive leverage×illiquidity interaction).

## Method (verbatim)

Two experiments over the pump-then-dump margin ecology
([docs/risk.md](../risk.md)), specs committed in `examples/`:

```bash
tezcat run examples/leverage_liquidity_map.json    # 5 leverage caps × 3 MM counts × 20 reps = 300 runs (59s)
tezcat analyze expv_60fabaa6f7ca
tezcat report  expv_60fabaa6f7ca -o docs/experiments/leverage_liquidity_map_report.md

tezcat run examples/leverage_liquidity_2x2.json    # extremes: {2×,10×} × {1,5 MMs} × 30 reps = 120 runs (23s)
tezcat analyze expv_cfc17774801a
tezcat report  expv_cfc17774801a -o docs/experiments/leverage_liquidity_2x2_report.md

tezcat reproduce 60fabaa6                          # ✓ 3/3 sampled runs match all stored hashes
tezcat reproduce cfc17774                          # ✓ 3/3 sampled runs match all stored hashes
```

Leverage cap = 1/initial_margin: im ∈ {0.5, 0.25, 0.1667, 0.125, 0.1} ≈
{2×, 4×, 6×, 8×, 10×}. Liquidity = market-maker count ∈ {1, 3, 5}.
Primary metric: max drawdown. Secondary: crash frequency
(`crash_detected` rate across seeds), forced liquidation volume/slices,
realized volatility.

## Results

### 1. The leverage effect is large, threshold-like, and confirmed

Mean max drawdown by leverage cap (pooled over liquidity):
~0.022 at 2× → ~0.06 at 4× → 0.11–0.24 at 6–10×. Crash frequency: **0/60
runs at 2×** across all liquidity levels, rising to 20–65% per cell at
8–10×. The 2×2 main effect of leverage is +0.113 max drawdown. This
replicates and extends the margin-spiral AB result
(`417305c1…`, Δ=0.163, p_holm=0.0005).

### 2. The hypothesized interaction is NOT supported

At the extremes (2× vs 10×, 1 vs 5 MMs, 30 reps/cell):

```
interaction = +0.015,  95% CI [−0.023, +0.057]   → spans zero
main effect of liquidity (1→5 MMs) = +0.004      → essentially nothing
```

Thin liquidity does **not** measurably amplify leverage-driven drawdowns in
this model. The pre-stated hypothesis is rejected as stated.

### 3. The surprise: liquidity is a transmission channel, not a cushion

Crash frequency and forced volume by cell (map experiment, n=20/cell):

| leverage cap | mm=1 | mm=3 | mm=5 |
|---|---|---|---|
| 2× | 0% · 0 | 0% · 0 | 0% · 0 |
| 4× | 15% · 0 | 10% · 0 | 0% · 0 |
| 6× | 30% · 0.1 | 25% · 3.0 | 30% · 6.4 |
| 8× | 20% · 0.0 | **65% · 10.5** | 20% · 0.0 |
| 10× | 30% · 1.8 | 50% · 3.1 | 40% · 5.5 |

(cells: crash frequency · mean forced volume filled)

Forced liquidation volume concentrates in the *deeper* books, not the thin
ones — consistent with the F8 finding that **forced volume measures fills,
not intent**: a thin book exhausts, forced sells fail to execute, and the
cascade self-limits; a deeper book lets the deleveraging actually transact,
transmitting the price impact into other levered agents' collateral.
Within this model, market-maker liquidity under high leverage is not simply
protective — it is the channel through which the spiral propagates.

## Limitations (read before quoting)

- Crash-frequency cells use n=20; a 65% vs 20% contrast at n=20 has wide
  binomial uncertainty. The mm=3 spike at 8× is suggestive, not confirmed —
  the obvious follow-up is a higher-n replication of the 8× row.
- No formal test of the non-monotone liquidity effect was run (the 2×2
  covers only the extremes, exactly where the middle-liquidity effect
  would be invisible). This is a lesson about extremes-only designs as
  much as about markets.
- All claims are within the specified synthetic model (single asset, one
  venue, market-order liquidation, no funding network). No real-market
  claim is made.

## Reproduction

| Experiment | Version | Research hash |
|---|---|---|
| 5×3 map | `expv_60fabaa6f7ca` | `60fabaa6f7ca84537f2339033a9841f8fa79576e11b27de93c69e313996f4466` |
| 2×2 extremes | `expv_cfc17774801a` | `cfc17774801a…` (see report) |

```bash
tezcat run examples/leverage_liquidity_map.json && tezcat reproduce 60fabaa6
```

## Product frictions found (the other deliverable)

1. **`tezcat report -o path/` crashed with a raw traceback** when the
   output directory didn't exist. *Fixed in this change* (creates parent
   dirs).
2. **CLI and dashboard can silently point at different registries**
   (`--data-dir` / `TEZCAT_DATA_DIR` vs the server's env). A researcher's
   CLI experiment doesn't appear in the dashboard and nothing says why.
   → Requirement: surface the active data directory in `tezcat list`
   output and in `/api/health`.
3. **Secondary dependent variables have no rendered surface.** `analyze`
   prints and `report` tabulates only the primary metric; crash frequency
   and forced volume had to be read from the (documented) analysis
   artifact JSON. → Requirement: secondary-DV tables in the report.
4. **Large factorials get no inference.** The 5×3 map correctly warns
   "interaction estimation implemented for 2x2 factorials only", but a
   researcher with a 15-cell map has no in-product way to test the
   non-monotone pattern that the descriptives clearly show.
   → Requirement (larger): trend/contrast tests for multi-level factors.
5. Minor: `analyze` truncates nothing and orders cells by design order —
   good — but with 15 cells a sorted-by-effect view would help reading.

These frictions — not the roadmap — define what gets built next.
