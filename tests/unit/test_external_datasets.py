"""Immutable external datasets: lineage, checksums, quality rejection."""

import json

import pytest

from tezcat.external.datasets import (
    DataQualityError, DatasetError, ExternalDatasetStore, validate_observations,
)
from tezcat.external.schema import MarketObservation


def obs(i: int, p: float = 0.5, market: str = "M") -> MarketObservation:
    return MarketObservation(market_id=market,
                             timestamp=f"2026-08-01T{i:02d}:00:00+00:00",
                             implied_probability=p, volume=100.0)


def series(n: int = 12):
    return [obs(i, 0.4 + i * 0.01) for i in range(n)]


def register(store, observations, **kw):
    args = dict(provider="kalshi", adapter_version="kalshi-adapter/1.0.0",
                source_kind="synthetic_fixture", market_ids=["M"],
                observations=observations,
                retrieved_at="2026-08-23T00:00:00+00:00",
                sampling="hourly candles, close",
                license="synthetic fixture, no license required")
    args.update(kw)
    return store.register(**args)


class TestQuality:
    def test_empty_rejected(self):
        with pytest.raises(DataQualityError, match="empty"):
            validate_observations([])

    def test_duplicates_rejected_not_cleaned(self):
        rows = series(5) + [obs(4)]
        rows.sort(key=lambda o: o.timestamp)
        with pytest.raises(DataQualityError, match="duplicate"):
            validate_observations(rows)

    def test_timestamp_regression_rejected(self):
        rows = [obs(3), obs(1)]
        with pytest.raises(DataQualityError, match="regression"):
            validate_observations(rows)

    def test_completeness_reported(self):
        d = validate_observations(series(10))
        assert d["n_observations"] == 10
        assert d["field_completeness"]["implied_probability"] == 1.0
        assert d["field_completeness"]["depth"] == 0.0


class TestStore:
    def test_register_and_reload_with_checksum_verification(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        manifest = register(store, series(), raw={"anything": "verbatim"})
        assert manifest.dataset_id.startswith("exd_")
        assert manifest.raw_checksum and manifest.normalized_checksum
        assert manifest.time_window["start"] < manifest.time_window["end"]
        loaded = store.observations(manifest.dataset_id)
        assert [o.implied_probability for o in loaded] == \
               [o.implied_probability for o in series()]
        assert store.raw(manifest.dataset_id) == {"anything": "verbatim"}

    def test_idempotent_reregistration(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        a = register(store, series())
        b = register(store, series())
        assert a.dataset_id == b.dataset_id
        assert len(store.list()) == 1

    def test_different_content_is_a_new_dataset(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        a = register(store, series())
        b = register(store, series()[:-1])
        assert a.dataset_id != b.dataset_id

    def test_quality_failure_registers_nothing(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        with pytest.raises(DataQualityError):
            register(store, [obs(3), obs(1)])
        assert store.list() == []

    def test_license_is_mandatory(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        with pytest.raises(Exception):
            register(store, series(), license="")

    def test_tampered_normalized_artifact_refused(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        m = register(store, series())
        row = store._index()[m.dataset_id]
        path = store._dir_for(row) / "normalized.json"
        payload = json.loads(path.read_text())
        payload[0]["implied_probability"] = 0.99
        path.write_text(json.dumps(payload))
        with pytest.raises(DatasetError, match="checksum"):
            store.observations(m.dataset_id)

    def test_version_chain(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        v1 = register(store, series())
        v2 = register(store, series() + [obs(20, 0.7)], supersedes=v1.dataset_id)
        assert v2.version == 2 and v2.supersedes == v1.dataset_id
        assert store.versions(v2.dataset_id) == [v1.dataset_id, v2.dataset_id]

    def test_permitted_use_defaults_to_local_analysis(self, tmp_path):
        store = ExternalDatasetStore(str(tmp_path))
        m = register(store, series())
        assert m.permitted_use == "local_analysis"
        assert "license" in m.lineage()
