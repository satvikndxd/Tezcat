"""Event matching, divergence, and lead/lag: equivalence is never assumed."""

import math

import pytest

from tezcat.external.matching import (
    MatchError, MatchLevel, MatchStore, divergence_series, lead_lag,
    propose_match,
)
from tezcat.external.schema import ExternalMarket

NOW = "2026-08-23T00:00:00+00:00"


def market(provider, mid, question, close=None):
    return ExternalMarket(provider=provider, market_id=mid, question=question,
                          close_time=close)


class TestProposal:
    def test_same_provider_rejected(self):
        a = market("kalshi", "A", "Will X happen?")
        with pytest.raises(MatchError):
            propose_match(a, a, now=NOW)

    def test_algorithmic_cap_is_likely_equivalent(self):
        a = market("kalshi", "A", "Will candidate Smith win the 2026 election?",
                   close="2026-11-03T00:00:00Z")
        b = market("polymarket", "B", "Will candidate Smith win the 2026 "
                                      "election?", close="2026-11-03T23:00:00Z")
        m = propose_match(a, b, now=NOW)
        assert m.level is MatchLevel.LIKELY_EQUIVALENT  # never EXACT
        assert m.origin == "algorithmic"
        assert m.status == "proposed"
        assert "equivalence unverified" in m.rationale

    def test_unrelated_questions_unmatched(self):
        a = market("kalshi", "A", "Will candidate Smith win the election?")
        b = market("polymarket", "B", "Bitcoin above 100k by March?")
        assert propose_match(a, b, now=NOW).level is MatchLevel.UNMATCHED

    def test_match_id_deterministic(self):
        a = market("kalshi", "A", "Will X happen?")
        b = market("polymarket", "B", "Will X happen?")
        assert propose_match(a, b, now=NOW).match_id == \
               propose_match(a, b, now=NOW).match_id


class TestReview:
    def _store_with_match(self, tmp_path):
        store = MatchStore(str(tmp_path))
        a = market("kalshi", "A", "Will candidate Smith win the election?")
        b = market("polymarket", "B", "Will candidate Smith win election?")
        return store, store.save(propose_match(a, b, now=NOW))

    def test_exact_requires_human_confirmation(self, tmp_path):
        store, m = self._store_with_match(tmp_path)
        reviewed = store.review(m.match_id, "confirmed",
                                "compared resolution rules and settlement "
                                "sources; identical", NOW,
                                promote_to_exact=True)
        assert reviewed.level is MatchLevel.EXACT
        assert reviewed.origin == "human"
        assert reviewed.status == "confirmed"

    def test_review_requires_note(self, tmp_path):
        store, m = self._store_with_match(tmp_path)
        with pytest.raises(MatchError, match="rationale"):
            store.review(m.match_id, "confirmed", "  ", NOW)

    def test_rejected_cannot_become_exact(self, tmp_path):
        store, m = self._store_with_match(tmp_path)
        with pytest.raises(MatchError):
            store.review(m.match_id, "rejected", "different settlement",
                         NOW, promote_to_exact=True)

    def test_reject_flow(self, tmp_path):
        store, m = self._store_with_match(tmp_path)
        reviewed = store.review(m.match_id, "rejected",
                                "different resolution sources", NOW)
        assert reviewed.status == "rejected"
        assert store.list()[0].status == "rejected"


class TestDivergence:
    def _series(self):
        ts = [f"2026-08-01T{i:02d}:00:00+00:00" for i in range(12)]
        a = [(t, 0.60 + 0.001 * i) for i, t in enumerate(ts)]
        b = [(t, 0.60 + 0.001 * i + (0.05 if 4 <= i <= 7 else 0.0))
             for i, t in enumerate(ts)]
        return a, b

    def test_divergence_metrics(self):
        a, b = self._series()
        d = divergence_series(a, b, threshold=0.02)
        assert "not arbitrage" in d["label"]
        assert d["n_aligned"] == 12
        assert d["peak_abs_divergence"] == pytest.approx(0.05)
        assert d["longest_divergent_run"] == 4
        assert d["converged_at"] is not None
        assert d["mean_signed_divergence"] < 0  # a below b during divergence

    def test_no_interpolation(self):
        a = [("t1", 0.5), ("t2", 0.5)]
        b = [("t3", 0.5), ("t4", 0.5)]
        with pytest.raises(MatchError, match="aligned"):
            divergence_series(a, b)


class TestLeadLag:
    def test_detects_constructed_lead(self):
        # b follows a with a 2-period delay
        n = 60
        base = [0.5 + 0.2 * math.sin(i / 4) * 0.5 for i in range(n + 2)]
        ts = [f"2026-08-01T{i // 60:02d}:{i % 60:02d}:00+00:00" for i in range(n)]
        a = [(t, min(0.95, max(0.05, base[i + 2]))) for i, t in enumerate(ts)]
        b = [(t, min(0.95, max(0.05, base[i]))) for i, t in enumerate(ts)]
        result = lead_lag(a, b, max_lag=5)
        assert result["best_lag"] == 2         # a leads b by 2
        assert result["best_correlation"] > 0.8
        assert "no causal claim" in result["label"]

    def test_too_few_points_rejected(self):
        a = [(f"t{i}", 0.5) for i in range(5)]
        with pytest.raises(MatchError, match="too few"):
            lead_lag(a, a, max_lag=5)
