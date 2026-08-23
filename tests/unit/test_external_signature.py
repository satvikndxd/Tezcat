"""Event signatures and bounded-probability stylized facts."""

import math

import pytest

from tezcat.external.schema import MarketObservation
from tezcat.external.signature import (
    EventSignature, SignatureError, extract_signature, logit_deltas,
    probability_deltas, probability_features,
)


def obs(i, p=None, spread=None, volume=None, depth=None):
    return MarketObservation(
        market_id="M", timestamp=f"2026-08-01T{i // 60:02d}:{i % 60:02d}:00+00:00",
        implied_probability=p, spread=spread, volume=volume, depth=depth)


def shock_series(n_pre=20, n_post=20):
    """Synthetic episode: flat 0.42, jump to 0.70, spread/volume shift."""
    rows = []
    for i in range(n_pre):
        rows.append(obs(i, p=0.42 + 0.001 * (i % 3), spread=0.02,
                        volume=100.0, depth=500.0))
    for j in range(n_post + 1):
        p = min(0.70 + 0.001 * (j % 3), 0.71) if j > 3 else 0.42 + 0.07 * j
        rows.append(obs(n_pre + j, p=p, spread=0.05, volume=300.0, depth=300.0))
    return rows


class TestTransforms:
    def test_deltas(self):
        assert probability_deltas([0.4, 0.5, 0.45]) == \
               pytest.approx([0.1, -0.05])

    def test_deltas_reject_out_of_range(self):
        with pytest.raises(SignatureError):
            probability_deltas([0.4, 1.4])

    def test_logit_refuses_boundaries_instead_of_clipping(self):
        with pytest.raises(SignatureError, match="refusing to clip"):
            logit_deltas([0.5, 1.0])
        with pytest.raises(SignatureError, match="refusing to clip"):
            logit_deltas([0.0, 0.5])

    def test_logit_interior(self):
        d = logit_deltas([0.5, 0.6])
        assert d[0] == pytest.approx(math.log(0.6 / 0.4), rel=1e-9)


class TestFeatures:
    def test_transform_name_always_recorded(self):
        f = probability_features([0.4 + 0.01 * (i % 5) for i in range(40)])
        assert f["transform"] == "delta"
        assert f["jump_threshold"] == 0.05

    def test_jump_frequency(self):
        probs = [0.4] * 20 + [0.6] + [0.6] * 20   # exactly one 0.2 jump
        f = probability_features(probs)
        assert f["jump_frequency"] == pytest.approx(1 / 40)
        assert f["max_jump"] == pytest.approx(0.2)

    def test_short_series_warns_instead_of_fabricating(self):
        f = probability_features([0.4, 0.5])
        assert f.get("warning_too_short") == 1.0
        assert "prob_volatility" not in f

    def test_unknown_transform_rejected(self):
        with pytest.raises(SignatureError):
            probability_features([0.5] * 40, transform="sqrt")


class TestExtractSignature:
    def test_window_must_fit_the_data(self):
        rows = shock_series()
        with pytest.raises(SignatureError, match="window"):
            extract_signature(rows, dataset_id="exd_x", t0_index=20,
                              pre_window=25, post_window=5)

    def test_extracted_values(self):
        rows = shock_series()
        sig = extract_signature(rows, dataset_id="exd_x", t0_index=20,
                                pre_window=15, post_window=18)
        assert sig.pre_event_probability == pytest.approx(0.42, abs=0.01)
        assert sig.post_event_probability == pytest.approx(0.71, abs=0.02)
        assert sig.delta_probability == pytest.approx(0.29, abs=0.03)
        assert sig.peak_probability >= sig.post_event_probability - 0.02
        assert sig.time_to_peak is not None and sig.time_to_peak > 0
        assert sig.spread_change == pytest.approx(0.03, abs=0.005)
        assert sig.volume_change == pytest.approx(3.0, abs=0.2)
        assert sig.depth_change == pytest.approx(-0.4, abs=0.05)
        assert sig.window_start < sig.window_end

    def test_signature_hash_deterministic_and_content_sensitive(self):
        rows = shock_series()
        s1 = extract_signature(rows, dataset_id="exd_x", t0_index=20,
                               pre_window=10, post_window=10)
        s2 = extract_signature(rows, dataset_id="exd_x", t0_index=20,
                               pre_window=10, post_window=10)
        s3 = extract_signature(rows, dataset_id="exd_x", t0_index=20,
                               pre_window=10, post_window=12)
        assert s1.signature_hash() == s2.signature_hash()
        assert s1.signature_hash() != s3.signature_hash()

    def test_missing_fields_stay_none(self):
        rows = [obs(i, p=0.4 + 0.005 * i) for i in range(30)]  # no volume/spread
        sig = extract_signature(rows, dataset_id="exd_x", t0_index=15,
                                pre_window=10, post_window=10)
        assert sig.volume_change is None
        assert sig.spread_change is None
        assert sig.depth_change is None

    def test_signature_is_versioned(self):
        rows = shock_series()
        sig = extract_signature(rows, dataset_id="exd_x", t0_index=20,
                                pre_window=10, post_window=10)
        assert sig.signature_version == 1
        assert "delta" in sig.transform_notes
