"""Fundamental valuation & transaction analysis (Phase S6).

A financial-analysis research domain built on the same principles as the
rest of Tezcat: deterministic computation, immutable content-addressed
research objects, artifact-backed reporting, and hash-verified
reproduction. It composes with the existing platform — valuation cases
register through the S5 research artifact graph, reports render only
from persisted artifacts, and the CLI/API extend the existing surfaces.

Non-negotiable domain rules (enforced by types and validators):

* **Facts ≠ assumptions ≠ derived values.** Historical financials are
  sourced facts; forecast drivers are explicit analyst assumptions;
  everything downstream is derived and labeled as such.
* **Deterministic.** Same inputs + same model version ⇒ identical
  outputs. There is no hidden randomness anywhere in this package.
* **Fail loudly.** Impossible financial states (terminal growth ≥ WACC,
  crossed sources & uses, non-reconciling ownership, incomplete
  statements) raise named errors; nothing silently produces
  plausible-looking numbers.
* **No fake precision.** Outputs carry the disclosures that bound them
  (e.g. terminal value share of EV, peer dispersion, scenario spread).
* **Analytical, not advisory.** Nothing in this package is investment
  advice or a prediction about real companies or markets.
"""

from tezcat.finance.statements import (  # noqa: F401
    FINANCE_MODEL_VERSION, CompanyFinancials, FinanceError, FiscalPeriod,
)
from tezcat.finance.case import ValuationCase, run_case  # noqa: F401
