"""Observed-event → synthetic-experiment bridge: language, identity, math."""

import math
import random

import pytest

from tezcat.experiments.registry import Registry
from tezcat.external.datasets import ExternalDatasetStore
from tezcat.external.research import (
    MECHANISMS, BridgeError, bridge_base_config, compare_episode,
    dynamics_features, experiment_from_signature, propose_mechanisms,
    research_manifest,
)
from tezcat.external.schema import MarketObservation
from tezcat.external.signature import extract_signature

FORBIDDEN_CAUSAL = ("caused", "proves", "explains the real", "demonstrates that")


def shock_observations():
    rows = []
    for i in range(30):
        rows.append(MarketObservation(
            market_id="M", timestamp=f"2026-08-01T00:{i:02d}:00+00:00",
            implied_probability=0.42 + 0.0005 * (i % 3), spread=0.02,
            volume=100.0, depth=500.0))
    for j in range(31):
        p = min(0.42 + 0.075 * j, 0.71)
        rows.append(MarketObservation(
            market_id="M", timestamp=f"2026-08-01T01:{j:02d}:00+00:00",
            implied_probability=p, spread=0.05, volume=320.0, depth=290.0))
    return rows


def signature():
    return extract_signature(shock_observations(), dataset_id="exd_test",
                             t0_index=30, pre_window=20, post_window=25)


class TestProposal:
    def test_candidates_are_hypotheses_not_claims(self):
        prop = propose_mechanisms(signature())
        names = {c["mechanism"] for c in prop["candidates"]}
        assert "information_shock" in names
        assert "herding" in names            # volume ratio > 1.5
        assert "mm_withdrawal" in names      # spread expanded
        assert "thin_liquidity" in names     # depth fell 42%
        text = str(prop).lower()
        for phrase in FORBIDDEN_CAUSAL:
            assert phrase not in text
        assert all("hypothesis" in c["rationale"] for c in prop["candidates"])
        assert "disclaimer" in prop

    def test_quiet_signature_still_proposes_minimal_baseline(self):
        rows = [MarketObservation(market_id="M",
                                  timestamp=f"2026-08-01T00:{i:02d}:00+00:00",
                                  implied_probability=0.5)
                for i in range(40)]
        sig = extract_signature(rows, dataset_id="exd_q", t0_index=20,
                                pre_window=10, post_window=10)
        prop = propose_mechanisms(sig)
        assert [c["mechanism"] for c in prop["candidates"]] == ["information_shock"]


class TestExperimentCompilation:
    def test_produces_ordinary_experiment_version(self, tmp_path):
        version = experiment_from_signature(
            signature(), mechanisms=["herding", "mm_withdrawal"],
            name="event-bridge-test", replications=2,
            t0_step=100, total_steps=300)
        assert version.design.design_type == "ab"
        assert [t.name for t in version.design.treatments] == \
               ["herding", "mm_withdrawal"]
        # registered through the completely ordinary registry machinery
        registry = Registry(str(tmp_path))
        vid = registry.register(version)
        reloaded = registry.load(vid)  # re-verifies research hash
        assert reloaded.research_hash == version.research_hash

    def test_shock_side_follows_observed_direction(self):
        up = experiment_from_signature(signature(), mechanisms=["herding"],
                                       name="x", t0_step=100, total_steps=300)
        assert up.config.shocks[0].side == "buy"  # observed Δp > 0

    def test_unknown_mechanism_rejected(self):
        with pytest.raises(BridgeError, match="unknown mechanisms"):
            experiment_from_signature(signature(), mechanisms=["telepathy"],
                                      name="x")

    def test_needs_a_comparison(self):
        with pytest.raises(BridgeError, match="comparison"):
            experiment_from_signature(signature(),
                                      mechanisms=["information_shock"], name="x")

    def test_mechanism_overrides_are_valid_config_paths(self):
        base = bridge_base_config()
        from tezcat.experiments.schema import apply_overrides
        for name, spec in MECHANISMS.items():
            cfg = apply_overrides(base, spec["overrides"])  # must not raise
            assert cfg is not base


class TestResearchManifest:
    def test_binds_experiment_dataset_and_signature(self, tmp_path):
        sig = signature()
        version = experiment_from_signature(sig, mechanisms=["herding"],
                                            name="m", t0_step=100,
                                            total_steps=300)
        store = ExternalDatasetStore(str(tmp_path))
        manifest = store.register(
            provider="kalshi", adapter_version="kalshi-adapter/1.0.0",
            source_kind="synthetic_fixture", market_ids=["M"],
            observations=shock_observations(),
            retrieved_at="2026-08-23T00:00:00+00:00",
            sampling="1-minute synthetic", license="synthetic fixture")
        rm = research_manifest(version, manifest, sig)
        assert rm["experiment_hash"] == version.research_hash
        assert rm["dataset_hash"] == manifest.dataset_hash
        assert rm["signature_hash"] == sig.signature_hash()
        assert len(rm["research_identity"]) == 64
        assert rm["adapter_version"] == "kalshi-adapter/1.0.0"
        assert rm["source_kind"] == "synthetic_fixture"


class TestDynamicsComparison:
    def _path(self, seed, jumpy=False):
        rng = random.Random(seed)
        vals, v = [], 100.0
        for i in range(120):
            if i == 60:
                v *= 1.06 if not jumpy else 1.12
            v *= 1 + rng.gauss(0, 0.002)
            vals.append(v)
        return vals

    def test_features_are_dimensionless(self):
        probs = [0.42] * 40 + [0.42 + 0.03 * j for j in range(10)] + [0.71] * 30
        f_prob = dynamics_features(probs, 40)
        f_price = dynamics_features(self._path(1), 60)
        # identical feature names computable on both domains
        assert set(f_prob) == set(f_price)
        assert f_prob["move_direction"] == 1.0
        assert 0 <= f_prob["time_to_peak_fraction"] <= 1

    def test_compare_requires_real_ensemble(self):
        with pytest.raises(BridgeError, match=">= 10"):
            compare_episode([0.4] * 50 + [0.6] * 50, 50,
                            [self._path(i) for i in range(3)], 60)

    def test_compare_output_is_labeled(self):
        observed = [0.42] * 50 + [min(0.42 + 0.04 * j, 0.70) for j in range(50)]
        ensemble = [self._path(i) for i in range(12)]
        result = compare_episode(observed, 50, ensemble, 60)
        assert result["labels"]["observed"].startswith("OBSERVED")
        assert result["labels"]["synthetic"].startswith("SYNTHETIC")
        assert result["n_ensemble"] == 12
        assert result["n_features"] > 0
        for row in result["features"].values():
            assert "observed" in row
        text = str(result).lower()
        for phrase in FORBIDDEN_CAUSAL:
            assert phrase not in text
