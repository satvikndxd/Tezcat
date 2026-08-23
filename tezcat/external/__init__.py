"""External Event-Market Intelligence layer (Phase S3).

Connects real-world prediction-market observations (Kalshi, Polymarket) to
Tezcat's synthetic laboratory as an **ingestion and research layer** — never
a trading layer, and never a data feed into the simulation kernel.

Architectural rules (non-negotiable):

* **Read-only.** No order placement, no accounts, no wallets, no keys.
* **External data never mutates the synthetic kernel.** Observations become
  research datasets → target signatures → calibration targets / experiment
  designs. The synthetic market stays synthetic.
* **Observed and synthetic data stay distinguishable at every layer.**
  Every object carries its provider, retrieval provenance, and checksums;
  reports label OBSERVED / SYNTHETIC / INFERRED explicitly.
* **Prediction-market semantics stay explicit.** A YES price is a
  *market-implied probability proxy*, not a stock price and not a truth.
* **Fail loudly.** Missing fields, schema drift, and quality violations are
  errors — data is never fabricated or silently cleaned.
"""

from tezcat.external.schema import (  # noqa: F401
    EXTERNAL_SCHEMA_VERSION,
    BookLevel,
    ExternalEvent,
    ExternalMarket,
    MarketObservation,
    MarketStatus,
    NormalizedTrade,
    OrderbookSnapshot,
    OutcomeSpec,
    Provenance,
    Quote,
    Resolution,
)
from tezcat.external.provider import (  # noqa: F401
    EventMarketProvider,
    ProviderDataError,
    ProviderError,
    ProviderHTTPError,
    ProviderRateLimited,
    ProviderSchemaError,
    ProviderTimeout,
)
