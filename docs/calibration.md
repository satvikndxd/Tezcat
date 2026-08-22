# Stylized Facts, Data Providers, and Calibration (Phase F9)

## Honest scope

No licensed real-market dataset ships with this repository, and no network
adapter exists yet. Phase F9 therefore delivers the *infrastructure and
discipline* — validated file-based data ingestion, stylized-facts feature
extraction, ensemble comparison with uncertainty bands, and calibration
with enforced out-of-sample separation — tested on **labeled synthetic
fixtures**. No claim about real markets is made anywhere in this layer;
the first real claim requires a licensed dataset ingested through the file
provider with its license recorded in the lineage.

## Data providers (`tezcat/data`)

- **File provider only.** `load_series_file` validates a JSON series
  (required: `source_id`, `provider`, `license`, `sampling`, ≥30 positive
  finite prices; optional length-matched volumes) and stamps a SHA-256
  checksum, so every comparison names exactly which data it used
  (`SeriesData.lineage()`).
- **External adapters are explicitly absent.** An unknown `provider` fails
  validation cleanly and offline — a stub that fabricates data would be
  worse than none. Alpaca/FRED integration requires credentials, licensing,
  and timezone/sampling contracts, and is deferred until then.
- `run_price_series` extracts the synthetic counterpart from run snapshots,
  sampled identically (one observation per step, last trade).

## Stylized-facts features (`tezcat/analysis/stylized_facts.py`)

Following Cont (2001): return moments (std, skewness, **excess kurtosis**),
**Hill tail index** (smaller = heavier tail; fixture-tested to recover a
Pareto α within 15%), return ACF at lags 1/2/5/10 (absence of linear
autocorrelation), |return| ACF (**volatility clustering**; fixture-tested
to detect a constructed regime-switching series while its raw returns stay
unpredictable), max drawdown, optional volume ACF. Degenerate inputs return
`None`/warnings, never fabricated numbers.

## Real-vs-synthetic comparison

`compare_features(real, ensemble)` scores one real feature vector against a
synthetic **ensemble** (≥10 replications enforced — a band from 3 runs is
decoration): per feature, the real value, ensemble q05–q95 band,
inside/outside flag, and z-distance; plus overall band coverage.
`stylized_facts_report` renders it with the data lineage. The stated
philosophy: *a partial match is the expected outcome* — the report exists to
say which properties the model reproduces and which it does not.

### Acceptance evidence (fully offline)

A stressed margin-spiral run (F8 pump-and-dump scenario) compared against a
20-replication calm-ecology ensemble: coverage **3/15**. The stressed
target exhibits excess kurtosis ≈ 26, Hill tail index ≈ 1.05, volatility
clustering ≈ 0.40, and a 52% drawdown — all far outside the calm bands.
Two readings, both supported by the artifacts: the calm model does not
reproduce crisis-like properties, and the **margin-spiral ecology generates
the classic stylized facts endogenously** (heavy tails + volatility
clustering) where the calm ecology does not. This is a within-model
comparison between two synthetic regimes, not real-market validation.

## Calibration discipline (`tezcat/calibration`)

- `CalibrationSpec` **rejects identical calibration/validation sources at
  construction** — "no-validation-period tuning" is a schema violation,
  not a guideline.
- `calibrate()` is a deterministic, budgeted grid search: every evaluation
  simulates a seeded replication ensemble; every evaluation is logged;
  budget exhaustion sets an explicit `truncated` flag. The result is
  **frozen** (parameters + objective + full evaluation log).
- **Objective**: mean relative error of the ensemble mean vs the target
  features. A pure |z| objective was evaluated and rejected — dividing by
  ensemble spread rewards diffuse models. Per-feature z-distances remain as
  diagnostics. ABC and history matching are documented future methods.
- `validate_calibration()` scores the frozen parameters against the
  **pre-declared** held-out source only; it refuses the calibration source,
  refuses undeclared sources, and never re-tunes.

### Identifiability, observed

The recovery test calibrates tick size (strong, monotone effect on return
volatility → recovered exactly from a single target series). Agent *count*
was tried first and is **weakly identifiable**: its effect on return-std
saturates below single-series noise, and short-horizon ACF features are too
noisy to help. This is a concrete instance of the audit's parameter-
identifiability concern (experiment #19) — recorded here rather than
hidden by a friendlier test.

## Limitations

- Grid search only; no ABC posterior, no history-matching implausibility.
- Feature uncertainty on the *real* side (one series) is not yet modeled
  (block bootstrap of the target is the natural next step).
- Timezone/calendar handling is trivially "per step" until a real
  dataset's sampling contract exists.
