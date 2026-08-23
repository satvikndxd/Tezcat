# External Event-Market Intelligence (Phase S3)

Tezcat S3 connects real-world prediction-market observations (Kalshi,
Polymarket) to the synthetic laboratory. It is an **ingestion and research
layer**, not a trading layer, and not a data feed into the simulation
kernel.

```
                 TEZCAT
                    │
        ┌───────────┴────────────┐
   Synthetic Market        External Markets
        │                  Kalshi · Polymarket
        │                        │
        │              Normalize · Snapshot · Lineage
        │                        │
        │                 Research Dataset (immutable)
        │                        │
        │            Signature · Calibration target · Comparison
        │                        │
        └────────► Ordinary ExperimentVersion ◄──────┘
                            │
                   Reproducible results
```

## The two non-negotiable rules

1. **External data never mutates the synthetic kernel.** Observations
   become datasets → signatures → experiment designs and calibration
   targets. No observed price ever enters the price engine.
2. **Observed and synthetic data stay distinguishable at every layer.**
   Every dataset records provider, adapter version, retrieval provenance,
   license/terms, and checksums; the UI badges everything as OBSERVED /
   SYNTHETIC FIXTURE / INFERRED; reports repeat the labels.

## Read-only, by construction

The provider layer (`tezcat/external/`) has **no trading surface**: no
order placement, no accounts, no wallets, no private keys. All endpoints
used are public market-data endpoints requiring no credentials. Live
network access is opt-in (`TEZCAT_EXTERNAL_LIVE=1`, server-side only);
the default install — and the entire test suite — is fully offline,
served by labeled fixture bundles (`tests/fixtures/external/`). Public
demo deployments never scrape providers on visitor traffic.

## Prediction-market semantics stay explicit

A canonical schema (`tezcat/external/schema.py`) models what these
markets actually are:

- **Event** — a real-world proposition (provider-scoped identity).
- **Market** — the tradable contract; **Outcome** — a settlement branch.
- **Probability** — always the **market-implied probability proxy**
  `p ∈ [0, 1]`, never "the price of a stock" and never "the true
  probability". Provider-native units (Kalshi cents/dollar strings,
  Polymarket decimal strings) are preserved verbatim beside every
  canonical value, and each conversion names its rule
  (`cents_int/100`, `dollars_string/1.0`, `decimal_string/1.0`).
- **Order book** — provider-native book preserved; the canonical YES-side
  book is derived only where mathematically exact (Kalshi: a YES ask at
  `1 − p` *is* a NO bid at `p` for binary contracts; the rule is recorded
  in `book_transform` on every snapshot).
- **Status / Resolution** — canonical lifecycle plus the verbatim
  provider status; settlement data kept where exposed.
- Missing fields stay `null`. Nothing is fabricated.

Schema drift fails loudly: a renamed or missing provider field raises
`ProviderSchemaError` naming the field — adapters never silently
reinterpret (`tests/unit/test_external_adapters.py` is the tripwire).

## Datasets: immutable, checksummed, licensed

Every ingestion follows `raw response → immutable raw artifact →
canonical normalization → registered dataset` (`tezcat/external/datasets.py`):

- `dataset_id = exd_<sha256[:12]>` content-addressed over schema version,
  provider, adapter version, raw checksum, and normalized observations.
- Registration is idempotent for identical content and **refuses**
  overwrites; changed source data becomes a new version (`supersedes`).
- Quality violations (duplicate observations, timestamp regressions,
  impossible probabilities, negative quantities) **reject** the dataset —
  data is never silently cleaned.
- The manifest records license/terms and a `permitted_use` category:
  endpoint accessibility never implies redistribution rights.

## Signatures and bounded-probability stylized facts

`tezcat/external/signature.py` extracts a versioned, hashable
**EventSignature** from a declared window: pre/post probability, Δp,
peak, time-to-peak, spread/volume/depth changes, plus stylized facts of
the probability increments (volatility, jump frequency, clustering,
mean-reversion ACF). Increments default to `Δp` — the economically
meaningful unit for contracts settling at 0/1; a `logit` transform is
offered for tail-sensitive analyses and **refuses** boundary
probabilities rather than clipping. The transform name travels with
every feature vector.

## Cross-provider research

`tezcat/external/matching.py`:

- Event matches carry levels `EXACT / LIKELY_EQUIVALENT /
  POSSIBLY_RELATED / UNMATCHED` with rationale, confidence, origin, and
  reviewer notes. **Algorithmic matching caps at LIKELY_EQUIVALENT**;
  only a human review with a written rationale can mint EXACT, because
  wording similarity says nothing about resolution rules.
- Matched series get **cross-provider probability divergence**
  (deliberately not called arbitrage) and a **lead/lag observation**
  (lagged Δp cross-correlation) — labeled observational, no causal claim.
- Alignment is by exact timestamps; no interpolation.

## The research bridge

`tezcat/external/research.py` implements
**observe → hypothesize → simulate → compare → reproduce**:

- `propose_mechanisms(signature)` proposes explicit candidate mechanisms
  (information shock, herding, momentum amplification, MM withdrawal,
  thin liquidity) from observed cues — rule-based, worded as hypotheses,
  never as explanations.
- `experiment_from_signature(...)` compiles the signature into an
  **ordinary `ExperimentVersion`** (control = information shock alone;
  one treatment per candidate mechanism) — same registry, BatchRunner,
  analysis, reports, TradeOps, and `tezcat reproduce` as every other
  experiment. No second research-identity system.
- `research_manifest(version, dataset, signature)` binds
  `experiment_hash + dataset_hash + signature_hash` into a single
  research identity: reproduction means re-executing the experiment
  *against that exact dataset version*.
- `compare_episode(...)` compares observed vs synthetic **only** through
  dimensionless episode descriptors (standardized increments, fractional
  time-to-peak, overshoot, jump frequency at 2σ, clustering) computed
  identically on both sides, against a ≥10-member synthetic ensemble
  with q05–q95 bands. Comparing a bounded probability level to a
  synthetic price level directly would be a category error, so it is not
  offered. A partial match is the expected outcome.

## Surfaces

CLI (namespaced group feeding the ordinary five-verb loop):

```bash
tezcat markets providers
tezcat markets import kalshi SYN-MKT-YES \
    --fixture tests/fixtures/external/kalshi/synthetic_event.json
tezcat markets datasets
tezcat markets show exd_…
tezcat markets signature exd_…            # INFERRED, hashed, versioned
tezcat markets propose exd_…              # candidate mechanisms
tezcat markets research exd_… --mechanisms herding,mm_withdrawal --run
tezcat markets compare exd_A exd_B        # divergence + lead/lag
# then the ordinary loop:
tezcat analyze expv_… && tezcat report expv_… && tezcat reproduce <hash>
```

API: `/api/markets/{providers,mechanisms,datasets,import,research,compare}`
plus per-dataset `observations`, `signature`, `propose`. Provider
implementations stay behind the service layer; no credentials exist in
or pass through any of it.

Dashboard: a **MARKETS** section (Explore datasets → dataset detail with
labeled probability chart and full lineage → signature → candidate
mechanisms → RESEARCH THIS EVENT → opens the ordinary RESEARCH section).

## Language discipline

The system says *candidate mechanism*, *consistent with*, *reproduces /
does not reproduce*, *lead/lag observation*, *synthetic analogue*. It
never claims a mechanism **caused** a real-world move, never calls
divergence arbitrage before equivalence is human-verified, and a failed
validation ("model does not reproduce this target behavior") is a
successful research result.

## Environment variables

| Variable | Meaning |
|---|---|
| `TEZCAT_EXTERNAL_LIVE` | opt-in live provider access (default: offline) |
| `KALSHI_API_BASE` | override the Kalshi API base URL |
| `POLYMARKET_GAMMA_BASE` / `POLYMARKET_CLOB_BASE` / `POLYMARKET_DATA_BASE` | override Polymarket API base URLs |

No API keys are required for any endpoint currently used. If a
credentialed endpoint is ever added, secrets belong in backend
environment variables only — never in experiment JSON, research hashes,
artifacts, logs, or the frontend.
