# External Event-Market Intelligence Layer (S3)

S3 adds an additive, read-only research boundary for public event-market data. It currently supports documented public market-data surfaces for **Kalshi** and **Polymarket**. External observations are preserved as external observations; they are not copied into Tezcat’s synthetic order book, agent state, portfolio state, or deterministic seed stream.

> External observations are a calibration target and research context. They are not synthetic state, a trading signal, a price forecast, or evidence of causality.

## Scope and safety boundary

The S3 layer is deliberately read-only. It contains no order placement, cancellation, wallet, deposit, withdrawal, portfolio, or private-key API. Credentials are never requested by the dashboard. Polymarket’s documented `/data/trades` endpoint is authenticated and user-scoped, so the public adapter refuses to pretend that unrestricted public trades are available. Recorded fixtures or an explicitly configured server-side read-only credential are required for that path.

The layer never mutates `tezcat/market`, `tezcat/agents`, `tezcat/engine`, `tezcat/events`, or the synthetic experiment seed plan. A researcher must explicitly approve a research proposal before it can be compiled into an ordinary `ExperimentVersion`.

## Provider surfaces

### Kalshi

The adapter uses Kalshi’s documented public REST base, `https://external-api.kalshi.com/trade-api/v2` [1]. Public reads include series, events, markets, market details, market order books, paginated trades, and candlesticks. Kalshi’s binary book returns YES and NO bids. S3 preserves those native levels and derives a canonical YES ask as `1 - NO bid` while retaining the native representation in the normalized artifact.

Market history uses the documented live path `/series/{series_ticker}/markets/{ticker}/candlesticks` and accepts 1-, 60-, or 1440-minute periods. Settled markets before the historical cutoff use `/historical/markets/{ticker}/candlesticks`.

### Polymarket

The adapter uses the current public Gamma metadata/discovery surface and the public CLOB market-data surface, including `GET https://clob.polymarket.com/book?token_id=...` and `GET https://clob.polymarket.com/prices-history?market=...` [2] [3]. Events contain markets; markets contain YES/NO outcomes with token IDs. S3 retains condition IDs, token IDs, tick size, minimum order size, negative-risk flag, last trade price, and order-book hash.

The implementation does not depend on the archived `@polymarket/clob-client`. The adapter follows the current provider documentation linked in the References section below.

## Canonical schemas

The schemas in `tezcat/external/schemas.py` are immutable Pydantic models:

| Object | Meaning |
|---|---|
| `Event` | Provider event metadata and resolution context |
| `Market` | Tradable market and explicit outcomes/token IDs |
| `Quote` | Canonical probability quote or history observation |
| `OrderbookSnapshot` | Canonical book plus provider-native bid/ask levels |
| `Trade` | A normalized trade with provider units and native fields retained |
| `DatasetManifest` | Immutable raw/normalized dataset identity, checksums, terms, sampling, and lineage |
| `EventSignature` | Versioned observed feature object with explicit nulls for unavailable fields |
| `EventMatch` | Cautious cross-provider relation: `EXACT`, `LIKELY_EQUIVALENT`, `POSSIBLY_RELATED`, or `UNMATCHED` |

Probability is represented as `p in [0,1]`. Provider price units remain in `provider_units` and native payloads remain in `metadata`. Derived values are labeled as inferred; an implied probability is described as a **market-implied probability proxy**, not a calibrated probability unless a researcher supplies resolution-based calibration evidence.

## Lineage and artifacts

A snapshot is written through the existing LocalStore/AWS-compatible artifact interface:

```text
external/{provider}/{source_id}/{dataset_id}/
├── raw.json
├── normalized.json
├── manifest.json
└── signatures/{signature_id}.json

registry/external_manifests/{version_id}.json  # durable bridge identity
```

The manifest records provider, source and market IDs, retrieval time, endpoint ID, provider adapter version, schema version, sampling and time window, permitted-use text, license/terms text, raw checksum, normalized checksum, and optional parent dataset lineage. Repeating the same raw response is idempotent; changing the raw response creates a new dataset version and identity. Approved compilation additionally writes `registry/external_manifests/{version_id}.json`, a write-once research-manifest link that is checked by reports and reproduction.

## FastAPI API

All routes are under `/api/markets` and are additive to the existing `/api/research` surface.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/markets/providers` | List configured read-only providers |
| `GET` | `/api/markets/events?provider=kalshi` | Discover observed events |
| `GET` | `/api/markets/markets?provider=polymarket` | Discover observed markets |
| `GET` | `/api/markets/{market_id}?provider=...` | Fetch normalized market metadata |
| `GET` | `/api/markets/{market_id}/orderbook?provider=...` | Fetch one read-only observed book |
| `GET` | `/api/markets/{market_id}/history?provider=...` | Fetch documented public history |
| `GET` | `/api/markets/{market_id}/trades?provider=kalshi` | Fetch Kalshi public trades; Polymarket returns an explicit credential-required error |
| `POST` | `/api/markets/{market_id}/snapshot` | Persist raw and normalized immutable lineage |
| `GET` | `/api/markets/datasets` | List registered external datasets |
| `GET` | `/api/markets/datasets/{dataset_id}` | Read a manifest and normalized artifact |
| `POST` | `/api/markets/datasets/{dataset_id}/signature` | Derive and persist an inferred event signature |
| `POST` | `/api/markets/divergence` | Compute descriptive probability divergence only |
| `POST` | `/api/markets/research` | Return an unapproved mechanism proposal |
| `POST` | `/api/markets/research/compile` | Approval-gated compilation into an ordinary `ExperimentVersion` |

The dashboard’s **MARKETS** tab is a read-only explorer for provider markets, observed order books, and public history. A researcher can manually register a snapshot, inspect its immutable dataset manifest, extract an inferred signature, draft an unapproved hypothesis proposal, and explicitly approve compilation into a normal `ExperimentVersion`. It exposes no order, wallet, account, execution, arbitrage, or financial-advice controls.

## CLI

The existing five-command research loop is unchanged. The additive namespace is:

```bash
tezcat markets providers
tezcat markets events kalshi --limit 20
tezcat markets list polymarket --limit 20
tezcat markets datasets
tezcat markets snapshot kalshi KX... --source-id example
```

`markets snapshot` collects provider data, writes raw and normalized artifacts, and prints the resulting dataset ID and checksums. It never places an order.

## Research workflow

A disciplined external-to-synthetic workflow is:

1. Discover an event or market using a provider adapter.
2. Read the provider’s current public market data and persist raw payloads.
3. Normalize without discarding provider-native identifiers, price units, timestamps, or status.
4. Create a versioned observed signature from a declared time window.
5. Compare observed features with synthetic metrics descriptively; preserve `OBSERVED`, `SYNTHETIC`, and `INFERRED` data classes.
6. Write an explicit mechanism proposal with a primary metric, secondary metrics, and failure modes.
7. Have a researcher approve the proposal.
8. Compile it into an ordinary `ExperimentVersion` and run the existing BatchRunner, analysis, report, and reproduction workflow.

The bridge persists external dataset/signature identity in both the research manifest returned at compilation and the optional `external_context` field on the immutable `ExperimentVersion`. That context includes dataset ID, raw and normalized checksums, provider/event/market identifiers, time window, adapter/schema versions, and signature checksum. Reports render the identity, and reproduction verifies the stored raw/normalized artifacts before synthetic run checks. The bridge does not insert external probability values into the synthetic configuration or alter Tezcat’s event log, seed allocator, or state-hash semantics.

## Data-quality policy

Adapters fail loudly on malformed JSON, missing required identifiers, invalid timestamps, unsupported probability units, probabilities outside `[0,1]`, malformed books, impossible pagination values, HTTP errors, and provider rate limits. Empty books are valid observed states, but best bid, best ask, mid, and spread remain null when unavailable. Missing optional fields remain null rather than being filled with zero.

All tests use offline, labeled fixtures under `tests/fixtures/kalshi` and `tests/fixtures/polymarket`; the normal test suite does not call provider networks. The S3 test suite covers provider path contracts, normalization, Kalshi binary semantics, Polymarket authentication boundaries, dataset idempotence/versioning, event matching, signature checksums, and divergence language.

## Reproduction and limitations

Provider responses are time-varying. A future reproduction must use the stored raw artifact, its checksum, the manifest, adapter version, schema version, and the declared sampling window. Re-running a live public request is not a reproduction of the original observation.

This implementation is the **S3-A snapshot/REST milestone**. It does not provide a WebSocket collector, a public Polymarket trade feed, order-book reconstruction from increments, resolution-based calibration, persistent cross-provider match review, a dedicated comparison page, TradeOps reuse, scenario-builder integration, or automatic nightly refresh. These remain S3-B through S3-E work. Public-demo mode bounds discovery item counts, snapshot refresh frequency, and external dataset count; there is no scheduled scraper. Deferred work must remain additive and must not mutate the frozen synthetic kernel.

## Public-demo operating rule

The deployed public demo should show previously registered, terms-reviewed datasets by default. Live provider reads are on-demand and server-side only, with bounded limits and rate-aware transports. Provider terms and redistribution permissions are retained in each manifest; no raw provider response should be republished unless those terms permit it. The frontend never receives provider credentials.

## References

[1]: https://docs.kalshi.com/ "Kalshi API documentation"
[2]: https://docs.polymarket.com/ "Polymarket API documentation"
[3]: https://docs.polymarket.com/developers/CLOB/introduction "Polymarket CLOB documentation"
