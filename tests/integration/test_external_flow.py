"""S3 first-milestone acceptance, end to end and fully offline.

A researcher can: select an (fixture) external prediction-market event,
inspect its observed data, register an immutable dataset, extract a
documented event signature, construct a synthetic Tezcat experiment from
that signature, run it through the ordinary BatchRunner, compare observed
vs synthetic dynamics, and reproduce the experiment against stored hashes
— with the dataset identity bound into the research manifest.
"""

from pathlib import Path

import pytest

from tezcat.core.seeds import derive_seed
from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.batch import BatchRunner
from tezcat.experiments.registry import Registry
from tezcat.experiments.reproduce import reproduce
from tezcat.external.datasets import ExternalDatasetStore
from tezcat.external.fixtures import FixtureTransport
from tezcat.external.kalshi import KalshiAdapter
from tezcat.external.provider import RateLimiter, RequestPolicy
from tezcat.external.research import (
    compare_episode, experiment_from_signature, propose_mechanisms,
    research_manifest,
)
from tezcat.external.schema import MarketObservation
from tezcat.external.signature import extract_signature

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"


@pytest.fixture()
def adapter():
    return KalshiAdapter(
        transport=FixtureTransport.from_file(
            FIXTURES / "kalshi" / "synthetic_event.json"),
        base_url="https://fixture.local/trade-api/v2",
        limiter=RateLimiter(sleep=lambda s: None),
        policy=RequestPolicy(sleep=lambda s: None),
        source_kind="synthetic_fixture",
        now=lambda: "2026-08-23T00:00:00+00:00")


def test_milestone_flow(tmp_path, adapter):
    # 1. inspect observed data through the provider layer -------------
    market = adapter.get_market("SYN-MKT-YES")
    assert market.provider == "kalshi"
    rows = adapter.get_history("SYN-MKT-YES", 0, 10**10)
    observations = [MarketObservation.model_validate(r) for r in rows]

    # 2. register the immutable dataset -------------------------------
    store = ExternalDatasetStore(str(tmp_path))
    dataset = store.register(
        provider=adapter.provider_id,
        adapter_version=adapter.adapter_version,
        source_kind="synthetic_fixture",
        event_id=market.event_id, market_ids=[market.market_id],
        observations=observations,
        endpoints=["/series/SYN-SERIES/markets/SYN-MKT-YES/candlesticks"],
        retrieved_at="2026-08-23T00:00:00+00:00",
        sampling="60-minute candles, close",
        license="synthetic fixture, no license required",
        raw={"note": "fixture bundle served the raw payloads"})
    assert dataset.dataset_id.startswith("exd_")
    reloaded = store.observations(dataset.dataset_id)  # checksum-verified
    assert len(reloaded) == len(observations)

    # 3. extract the documented event signature ------------------------
    sig = extract_signature(reloaded, dataset_id=dataset.dataset_id,
                            t0_index=36, pre_window=25, post_window=30)
    assert sig.delta_probability is not None and sig.delta_probability > 0.15
    assert sig.volume_change is not None and sig.volume_change > 1.5
    proposal = propose_mechanisms(sig)
    assert any(c["mechanism"] == "herding" for c in proposal["candidates"])

    # 4. compile into an ordinary ExperimentVersion whose research hash
    #    binds the dataset identity (external_context) ------------------
    version = experiment_from_signature(
        sig, mechanisms=["herding"], name="milestone-event-bridge",
        replications=2, t0_step=80, total_steps=240, dataset=dataset)
    assert version.external_context["dataset_hash"] == dataset.dataset_hash
    assert version.external_context["dataset_checksum"] == \
        dataset.normalized_checksum
    registry = Registry(str(tmp_path))
    vid = registry.register(version)

    # 5. run through the ordinary BatchRunner --------------------------
    batch = BatchRunner(registry).run(vid)
    assert batch["status"] == "completed"
    assert batch["completed"] == version.design.planned_runs() == 4

    # 6. research manifest binds experiment + dataset + signature ------
    rm = research_manifest(version, dataset, sig)
    assert rm["experiment_hash"] == version.research_hash
    assert rm["dataset_hash"] == dataset.dataset_hash
    assert rm["market_ids"] == ["SYN-MKT-YES"]

    # 7. observed vs synthetic comparison (dimensionless, labeled) -----
    observed_probs = [o.implied_probability for o in reloaded
                      if o.implied_probability is not None]
    cfg = version.cell_config("herding")
    ensemble = []
    for i in range(10):
        seed = derive_seed(version.root_seed, "s3-compare", "rep", i)
        eng = EcologyEngine(f"cmp_{i}", cfg, seed)
        while not eng.done:
            eng.step()
        ensemble.append([s["last_price"] for s in eng.snapshots])
    comparison = compare_episode(observed_probs, 36, ensemble, 80)
    assert comparison["labels"]["observed"].startswith("OBSERVED")
    assert comparison["n_features"] >= 4
    assert comparison["coverage"] is not None

    # 8. reproduce against stored hashes AND the exact dataset version -
    result = reproduce(registry, vid, sample=1)
    assert result["success"], result["checks"]
    dataset_checks = [c for c in result["checks"]
                      if c["check"] == "external dataset identity verified"]
    assert dataset_checks and dataset_checks[0]["ok"]


def test_bridge_without_dataset_keeps_legacy_identity(adapter):
    """No external context → the legacy research-hash payload is unchanged."""
    rows = adapter.get_history("SYN-MKT-YES", 0, 10**10)
    observations = [MarketObservation.model_validate(r) for r in rows]
    sig = extract_signature(observations, dataset_id="exd_x", t0_index=36,
                            pre_window=25, post_window=30)
    version = experiment_from_signature(sig, mechanisms=["herding"], name="x",
                                        replications=2, t0_step=80,
                                        total_steps=240)
    assert version.external_context is None
