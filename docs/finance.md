# Valuation & Transaction Analysis (Phase S6)

A fundamental-valuation and M&A research domain built with the same
discipline as the rest of Tezcat: deterministic pure calculations,
immutable content-addressed research objects, one provenance graph, and
artifact-backed reporting. Nothing here is investment advice; the
bundled example is entirely synthetic fixture data.

## Workflow

```
Company inputs (sourced facts + provenance)
  → Operating forecast (explicit per-year driver assumptions)
  → DCF · Trading comps · Precedent transactions
  → Valuation triangulation (range, not a point)
  → Transaction structure (sources & uses, ownership)
  → Pro forma earnings → accretion/dilution
  → Scenarios (downside/base/upside) + sensitivity grids
  → Immutable artifacts → professional report → reproduction
```

```bash
tezcat finance run examples/finance/meridian_case.json
tezcat finance report vca_…  -o meridian.md
tezcat finance reproduce vca_…          # outputs must be byte-identical
tezcat finance list
```

Same surface over HTTP under `/api/finance/*`, and in the dashboard as
**FINANCE**.

## Domain model (tezcat/finance/)

| Module | Responsibility |
|---|---|
| `statements.py` | `FiscalPeriod` / `CompanyFinancials`: sourced historical facts with provenance; derived metrics (EBITDA, margins, FCF, net debt, leverage) computed, never stored back as inputs |
| `forecast.py` | `ForecastAssumptions` (per-year drivers) → `ProjectedPeriod`s |
| `dcf.py` | enterprise DCF, Gordon-growth + exit-multiple terminal values |
| `comps.py` | peer multiples, statistics, explicit selection, dispersion |
| `precedents.py` | transaction multiples + premium statistics (kept separate from trading comps by design) |
| `transaction.py` | offer/premium, consideration mix, sources & uses, ownership |
| `proforma.py` | per-year EPS bridge + accretion/dilution verdicts |
| `scenarios.py` | layered dotted-path overrides (strict; no invented paths) |
| `sensitivity.py` | generic two-axis grids, full model re-run per cell |
| `case.py` | `ValuationCase` research object; execution, registration, reproduction |
| `report.py` | artifact-only markdown report |

## The formulas (implementation-aligned)

**Statements (derived):** gross profit = revenue − COGS; EBITDA = gross
profit − opex; EBIT = EBITDA − D&A; EBT = EBIT − interest; net income =
EBT − taxes; net debt = debt − cash.

**Unlevered FCF:**
`FCF_t = EBIT_t·(1−τ) + D&A_t − capex_t − ΔNWC_t`, with ΔNWC from the
NWC *level* path (index 0 of history is not computable and refuses to
assume zero).

**DCF (end-of-period discounting):**
```
PV(FCF)   = Σ_{t=1..N} FCF_t / (1+WACC)^t
TV_gordon = FCF_N (1+g) / (WACC − g)        requires g < WACC (enforced)
TV_exit   = EBITDA_N × exit multiple        requires EBITDA_N > 0
EV        = PV(FCF) + TV/(1+WACC)^N         must be > 0 (else refused)
Equity    = EV − net debt;   per share = Equity / diluted shares
```
`terminal_value_pct_of_ev` is a mandatory disclosure. Mid-year
convention is a documented extension point, not a hidden switch.

**Comps:** peer EV = price × shares + debt − cash; multiples EV/Revenue,
EV/EBITDA, EV/EBIT, P/E; peers with non-meaningful denominators are
excluded *and counted*. Implied value applies the analyst's selected
statistic (mean/median/q25/q75) of the selected multiple to the
target's metric; the report always shows the implied value at peer q25
and q75 next to the point estimate.

**Transaction:**
```
offer   = unaffected price × (1 + premium)     [or explicit]
EqP     = offer × target shares;   purchase EV = EqP + target net debt
USES    = EqP + advisory fees + financing fees (+ debt refinanced)
SOURCES = new debt + stock (stock% × EqP) + balance-sheet cash (residual)
```
Sources must equal uses (verified), post-close cash must respect the
minimum-cash floor (else the structure is *not fundable* and fails),
over-funding fails, new shares = stock consideration / acquirer price,
and ownership percentages must sum to exactly 1.

**Pro forma EPS bridge (per year, sums exactly to pro forma NI):**
acquirer NI + target NI + synergies·phase-in·(1−τ) − new-debt
interest·(1−τ) − foregone cash yield·(1−τ) − financing-fee
amortization·(1−τ). One-time advisory fees are excluded from recurring
EPS and disclosed. Accretion = PF EPS / standalone EPS − 1; verdicts
use a stated ±0.5% neutrality threshold.

**Scenarios/sensitivities:** declared assumption deltas over the base
spec (strict path validation); every sensitivity cell is a full model
re-run, and invalid combinations (e.g. g ≥ WACC) appear as explicit
`invalid` cells, never interpolated.

## Research-object integration

A case is content-addressed:
`case_hash = SHA256(finance model version ‖ canonical spec)` →
`fin_<hash12>`. Registration flows through the S5 artifact graph — no
second provenance system:

```
company_financials (target, acquirer)  →  valuation_case
                                              →  valuation_output
                                                    →  finance_report
```

`tezcat finance reproduce` re-runs the persisted spec and requires the
regenerated output artifact to be hash-identical to the stored one.
Because valuation outputs are ordinary graph artifacts, they are
available to the wider platform (e.g. as inputs to future
scenario/Monte-Carlo experiments through the same graph) without any
special bridge.

## Facts vs assumptions vs derived — enforced, not stylistic

* Historical periods carry a mandatory `provenance` record and are the
  only *facts* in the system.
* Forecast drivers, valuation inputs, and deal structure are explicit
  *assumption* objects with rationale fields.
* Everything computed is labeled `derived` in the payloads, and reports
  restate the category at each section.

## Failure semantics (tested)

Terminal growth ≥ WACC; Gordon perpetuity on non-positive FCF; exit
multiple on non-positive EBITDA; negative enterprise value; COGS above
revenue; taxes above pre-tax income; assumption-vector length
mismatches; premium *and* explicit offer both set; unfundable or
over-funded structures; non-reconciling ownership; accretion analysis
on non-positive acquirer earnings; unknown scenario paths; non-numeric
sensitivity metrics — all raise named `FinanceError`s.

## Example case

`examples/finance/meridian_case.json` — "Meridian Microdevices," a
synthetic mid-cap semiconductor target with three historical years,
five peers, five precedent transactions, and a hypothetical 60/40
cash-stock acquisition by "Vantera Systems" at a 30% premium.
Every company, deal, and number is fixture data; provenance fields say
so. Base-case outputs: DCF ≈ $40.7/share, comps ≈ $40.9, precedents ≈
$50.1 (control premium visible exactly where theory expects it),
year-1 accretion ≈ +4.4%, downside/base/upside DCF ≈ $27.5/$40.7/$49.0.

## Limitations / deferred by design

Single-segment operating model; simple driver forecasting (no
three-statement circularity); purchase accounting (goodwill,
step-ups, intangible amortization) is an explicit extension point;
debt principal constant over the pro forma horizon; no live market-data
dependency in the analytical core (offline-first is deliberate —
external sourcing plugs in through the provenance fields).
