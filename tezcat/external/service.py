"""Markets service: the one façade CLI and API share (Phase S3).

Wraps adapters + dataset store + signature/research bridge behind a small
surface so provider details never leak into the CLI or HTTP layers.

Live-network policy
-------------------
Live provider requests are **opt-in** via ``TEZCAT_EXTERNAL_LIVE=1``
(server-side). Without it, only labeled fixture bundles can be imported —
the default install (and the whole test suite) is fully offline, and a
public demo never scrapes providers on visitor traffic. Live imports are
bounded (max history window, one market per request) and rate-limited at
the adapter layer.

Credentials: the endpoints used here are public; no API keys are read,
stored, or forwarded. If a credentialed endpoint is ever added it must
source secrets from server-side environment variables only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from tezcat.experiments.registry import Registry
from tezcat.external.datasets import (
    DatasetError, DatasetManifest, ExternalDatasetStore,
)
from tezcat.external.fixtures import FixtureTransport, load_fixture
from tezcat.external.kalshi import KalshiAdapter
from tezcat.external.matching import divergence_series, lead_lag
from tezcat.external.polymarket import PolymarketAdapter
from tezcat.external.provider import ProviderError, RateLimiter, RequestPolicy
from tezcat.external.research import (
    MECHANISMS, experiment_from_signature, propose_mechanisms,
    research_manifest,
)
from tezcat.external.schema import MarketObservation
from tezcat.external.signature import EventSignature, extract_signature

#: Hard cap on a single live-history import (in periods), so no request
#: can ask a provider for an unbounded window.
MAX_HISTORY_PERIODS = 2000

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "kalshi": {
        "provider_id": "kalshi",
        "adapter_version": KalshiAdapter.adapter_version,
        "read_only": True,
        "auth": "none — public market-data endpoints only",
        "data": ["events", "markets", "orderbook", "trades",
                 "candlestick history"],
    },
    "polymarket": {
        "provider_id": "polymarket",
        "adapter_version": PolymarketAdapter.adapter_version,
        "read_only": True,
        "auth": "none — Gamma/CLOB/Data public endpoints; no wallet, ever",
        "data": ["events", "markets", "orderbook", "trades",
                 "price history"],
    },
}


class ServiceError(ValueError):
    pass


def live_enabled() -> bool:
    return os.environ.get("TEZCAT_EXTERNAL_LIVE", "").lower() in ("1", "true", "yes")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketsService:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.datasets = ExternalDatasetStore(data_dir)
        self.registry = Registry(data_dir)

    # -- providers -----------------------------------------------------
    def providers(self) -> List[Dict[str, Any]]:
        return [dict(v, live_enabled=live_enabled())
                for v in PROVIDERS.values()]

    def _adapter(self, provider: str, fixture_path: Optional[str]):
        if provider not in PROVIDERS:
            raise ServiceError(f"unknown provider {provider!r}; "
                               f"available: {sorted(PROVIDERS)}")
        if fixture_path is not None:
            bundle = load_fixture(fixture_path)  # validates the label
            if bundle.get("provider") not in (None, provider):
                raise ServiceError(
                    f"fixture {fixture_path} is for provider "
                    f"{bundle.get('provider')!r}, not {provider!r}")
            transport = FixtureTransport(bundle["routes"],
                                         label=bundle["fixture_label"])
            source_kind = ("synthetic_fixture"
                           if bundle["fixture_label"] == "SYNTHETIC FIXTURE"
                           else "recorded")
            kw = dict(transport=transport, source_kind=source_kind,
                      limiter=RateLimiter(sleep=lambda s: None),
                      policy=RequestPolicy(sleep=lambda s: None))
        elif live_enabled():
            kw = dict(source_kind="live")
        else:
            raise ServiceError(
                "live provider access is disabled (offline mode). Set "
                "TEZCAT_EXTERNAL_LIVE=1 server-side to allow live imports, "
                "or import from a labeled fixture bundle (--fixture)")
        if provider == "kalshi":
            return KalshiAdapter(**kw)
        return PolymarketAdapter(**kw)

    # -- import → immutable dataset ------------------------------------
    def import_market(self, provider: str, market_id: str, *,
                      fixture_path: Optional[str] = None,
                      start_ts: int = 0, end_ts: Optional[int] = None,
                      period_minutes: int = 60,
                      supersedes: Optional[str] = None) -> DatasetManifest:
        """Fetch a market's history and register it as an immutable dataset."""
        adapter = self._adapter(provider, fixture_path)  # offline check first
        if end_ts is None:
            end_ts = int(datetime.now(timezone.utc).timestamp())
        n_periods = max(0, end_ts - start_ts) / (period_minutes * 60)
        if fixture_path is None and n_periods > MAX_HISTORY_PERIODS:
            raise ServiceError(
                f"requested window is {n_periods:.0f} periods; the live "
                f"import cap is {MAX_HISTORY_PERIODS} — narrow the window "
                "or increase period_minutes")
        market = adapter.get_market(market_id)
        if provider == "polymarket":
            yes = next((o for o in market.outcomes if o.token_id), None)
            if yes is None:
                raise ServiceError(
                    f"polymarket market {market_id} exposes no outcome "
                    "token ids; cannot fetch history")
            rows = adapter.get_history(yes.token_id, start_ts, end_ts,
                                       period_minutes)
            market_ids = [market.market_id, yes.token_id]
            endpoint = "/prices-history"
            sampling = f"{period_minutes}-minute price history (YES token)"
        else:
            rows = adapter.get_history(market_id, start_ts, end_ts,
                                       period_minutes)
            market_ids = [market.market_id]
            endpoint = "candlesticks"
            sampling = f"{period_minutes}-minute candles, close"

        observations = [MarketObservation.model_validate(r) for r in rows]
        source_kind = adapter.source_kind
        license_note = (
            "synthetic fixture, no license required"
            if source_kind == "synthetic_fixture" else
            f"{provider} API terms apply; imported for local research "
            "analysis; redistribution not assumed")
        return self.datasets.register(
            provider=provider, adapter_version=adapter.adapter_version,
            source_kind=source_kind, event_id=market.event_id,
            market_ids=market_ids, observations=observations,
            endpoints=[endpoint],
            retrieved_at=_utc_now(), sampling=sampling,
            license=license_note,
            raw={"market": market.model_dump(mode="json"), "history": rows},
            supersedes=supersedes,
            notes=f"question: {market.question}"[:400])

    # -- dataset access ------------------------------------------------
    def list_datasets(self) -> List[Dict[str, Any]]:
        return self.datasets.list()

    def dataset(self, dataset_id: str) -> DatasetManifest:
        return self.datasets.get(dataset_id)

    def observations(self, dataset_id: str) -> List[MarketObservation]:
        return self.datasets.observations(dataset_id)

    # -- signatures ----------------------------------------------------
    def detect_t0(self, observations: Sequence[MarketObservation]) -> int:
        """Heuristic event anchor: index of the largest |Δp|.

        A convenience default only — the chosen index is recorded in the
        signature, and researchers can (should) override it.
        """
        probs = [(i, o.implied_probability) for i, o in enumerate(observations)
                 if o.implied_probability is not None]
        if len(probs) < 3:
            raise ServiceError("not enough probability observations to "
                               "suggest an event anchor")
        best_i, best = probs[1][0], 0.0
        for (_, p0), (i1, p1) in zip(probs, probs[1:]):
            if abs(p1 - p0) > best:
                best_i, best = i1, abs(p1 - p0)
        return best_i

    def signature(self, dataset_id: str, *, t0_index: Optional[int] = None,
                  pre_window: Optional[int] = None,
                  post_window: Optional[int] = None) -> EventSignature:
        obs = self.observations(dataset_id)
        if t0_index is None:
            t0_index = self.detect_t0(obs)
        pre = pre_window if pre_window is not None else min(t0_index, 24)
        post = (post_window if post_window is not None
                else min(len(obs) - 1 - t0_index, 24))
        return extract_signature(obs, dataset_id=dataset_id,
                                 t0_index=t0_index, pre_window=pre,
                                 post_window=post)

    def propose(self, dataset_id: str, **window: Any) -> Dict[str, Any]:
        return propose_mechanisms(self.signature(dataset_id, **window))

    # -- research bridge ----------------------------------------------
    def mechanisms(self) -> Dict[str, Any]:
        return {name: {"description": m["description"],
                       "signature_cue": m["signature_cue"]}
                for name, m in MECHANISMS.items()}

    def create_research(self, dataset_id: str, *,
                        mechanisms: Sequence[str], name: str,
                        replications: int = 5,
                        t0_index: Optional[int] = None,
                        pre_window: Optional[int] = None,
                        post_window: Optional[int] = None,
                        t0_step: int = 400, total_steps: int = 1200,
                        root_seed: Optional[int] = None) -> Dict[str, Any]:
        """Signature → ordinary ExperimentVersion + research manifest.

        The experiment lands in the normal registry; batches, analysis,
        reports, and reproduction all go through the existing machinery
        (CLI ``tezcat run/analyze/...`` or the research API / TradeOps).
        """
        dataset = self.dataset(dataset_id)
        sig = self.signature(dataset_id, t0_index=t0_index,
                             pre_window=pre_window, post_window=post_window)
        version = experiment_from_signature(
            sig, mechanisms=mechanisms, name=name,
            experiment_id=f"exp_event_{dataset_id}",
            replications=replications, t0_step=t0_step,
            total_steps=total_steps, root_seed=root_seed)
        vid = self.registry.register(version)
        manifest = research_manifest(version, dataset, sig)
        # persist the binding next to the dataset for report lineage
        row = self.datasets._index()[dataset_id]
        self.datasets._write(
            self.datasets._dir_for(row) / f"research_{vid}.json", manifest)
        return {"version_id": vid, "research_hash": version.research_hash,
                "planned_runs": version.design.planned_runs(),
                "signature": sig.model_dump(mode="json"),
                "research_manifest": manifest}

    # -- cross-dataset comparison --------------------------------------
    def compare_datasets(self, dataset_a: str, dataset_b: str, *,
                         threshold: float = 0.02,
                         max_lag: int = 10) -> Dict[str, Any]:
        """Cross-provider divergence + lead/lag for two datasets.

        Alignment is by exact timestamps; contract equivalence is NOT
        assumed — the output repeats that caveat.
        """
        def series(dataset_id: str):
            return [(o.timestamp, o.implied_probability)
                    for o in self.observations(dataset_id)
                    if o.implied_probability is not None]

        ma, mb = self.dataset(dataset_a), self.dataset(dataset_b)
        sa, sb = series(dataset_a), series(dataset_b)
        out: Dict[str, Any] = {
            "dataset_a": ma.lineage(), "dataset_b": mb.lineage(),
            "equivalence": "NOT verified — use the event-match review flow "
                           "before treating these as the same contract",
            "divergence": divergence_series(sa, sb, threshold=threshold),
        }
        try:
            out["lead_lag"] = lead_lag(sa, sb, max_lag=max_lag)
        except Exception as exc:  # noqa: BLE001 — report, don't fabricate
            out["lead_lag"] = {"unavailable": str(exc)}
        return out
