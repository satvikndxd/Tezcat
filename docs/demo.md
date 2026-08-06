# Demo Script (~5 minutes)

Open the dashboard (`/`). Status bar: TEZCAT — MARKET ECOLOGY LAB.

## 1. Select preset — "Flash Crash"

Show the preset card: agent population (16 noise / 14 retail / 7 momentum /
5 mean-reversion / 2 market makers), the scheduled shock chain
(whale @ 800 → MM withdrawal @ 810 → panic sentiment @ 815).

> "This is an *experiment*, not just a simulation — immutable config, hashed, seeded."

## 2. Start run

CREATE EXPERIMENT → START RUN. Show the calm phase: price ~100, tight spread,
symmetric book depth, STABLE regime.

## 3. The crash (step ~800)

Narrate as it happens: whale sell program sweeps the bid side → depth collapses →
spread blows out → regime flips to **CRISIS** (red) → retail panic-sells into the
vacuum. Point at the shock markers on the price chart and the regime band.

## 4. Inject a manual shock

In the INJECT SHOCK panel: `whale_order`, side `sell`, magnitude 1500, duration 20 → FIRE.
Liquidity worsens again — cause and effect, live.

## 5. Recovery

Mean-reversion capital buys the dislocation, market makers re-enter, spread
narrows, regime → **RECOVERY**, then **STABLE**.

## 6. The report

Open VIEW REPORT: max drawdown (~30%), crash detected ✔, liquidity crisis ✔,
regime step share, and PnL by agent type — panic sellers lost, dip buyers won.

> "Tezcat lets us run controlled market experiments and observe emergent crisis
> dynamics reproducibly — same seed, same crash, tick for tick."

## Fallback (no browser)

```bash
.venv/bin/python scripts/smoke.py
```
