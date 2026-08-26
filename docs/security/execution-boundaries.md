# Execution boundaries and security posture

Tezcat separates four regimes; only the first two exist in the codebase.

| Regime | Status | Boundary |
|---|---|---|
| **Research** | implemented | synthetic simulation, artifact graph, analysis — no external side effects |
| **Paper / backtest** | implemented (S4/S5) | NautilusTrader **backtest engine only**; no order ever leaves the process |
| **Sandbox** | not implemented | would be a separately gated project with its own review |
| **Live execution** | not implemented, out of scope | never part of a public demo; would require explicit, separately gated policy controls |

Standing rules, enforced in code and tests:

* **No forecast→order path.** Forecasts are probabilistic research
  observations; they inform portfolio *research* and backtest *sizing*,
  nothing else.
* **No trading surface.** The S3 provider layer is read-only (no
  accounts, wallets, keys); the S4 bridge and S5 slice run exclusively
  through Nautilus' backtest engine.
* **Credentials.** Public market-data endpoints require none and none
  are sent. Any future credentialed integration keeps secrets in
  server-side environment variables only — never in experiment JSON,
  research hashes, artifacts, logs, or the frontend.
* **Public demo mode** (`TEZCAT_PUBLIC_DEMO`) additionally disables live
  provider imports, restricts fixtures to bundled files, and caps run
  sizes, worker counts, and slice parameters.
* **No AI in the measurement loop.** If an analyst layer is added, it
  reads artifacts and proposes hypotheses behind a human approval gate;
  metric extraction, statistics, hashing, and reproduction remain
  deterministic code.
* **Never claimed:** "automated hedge fund", "AI trading fund",
  "guaranteed alpha", market prediction, or production trading
  readiness. Tezcat is research infrastructure.
