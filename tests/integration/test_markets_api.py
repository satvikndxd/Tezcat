"""Phase S3: the MARKETS API — import → inspect → signature → research."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"
KALSHI_FIXTURE = str(FIXTURES / "kalshi" / "synthetic_event.json")
POLY_FIXTURE = str(FIXTURES / "polymarket" / "synthetic_event.json")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("markets"))
    os.environ["TEZCAT_STORE"] = "local"
    os.environ.pop("TEZCAT_EXTERNAL_LIVE", None)
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="module")
def dataset_id(client):
    r = client.post("/api/markets/import", json={
        "provider": "kalshi", "market_id": "SYN-MKT-YES",
        "fixture_path": KALSHI_FIXTURE})
    assert r.status_code == 201, r.text
    return r.json()["dataset_id"]


def test_providers_are_read_only_and_offline(client):
    rows = client.get("/api/markets/providers").json()
    assert {r["provider_id"] for r in rows} == {"kalshi", "polymarket"}
    assert all(r["read_only"] for r in rows)
    assert all(not r["live_enabled"] for r in rows)


def test_live_import_refused_in_offline_mode(client):
    r = client.post("/api/markets/import", json={
        "provider": "kalshi", "market_id": "ANY"})
    assert r.status_code == 422
    assert "offline mode" in r.json()["detail"]


def test_dataset_lineage_and_labeling(client, dataset_id):
    r = client.get(f"/api/markets/datasets/{dataset_id}").json()
    assert r["data_label"] == "SYNTHETIC FIXTURE DATA"
    m = r["manifest"]
    assert m["adapter_version"].startswith("kalshi-adapter/")
    assert m["normalized_checksum"]
    assert m["license"]
    obs = client.get(f"/api/markets/datasets/{dataset_id}/observations").json()
    assert obs["n_total"] == 72
    assert "proxy" in obs["note"]


def test_unknown_dataset_404(client):
    assert client.get("/api/markets/datasets/exd_nope").status_code == 404


def test_signature_and_proposal(client, dataset_id):
    r = client.get(f"/api/markets/datasets/{dataset_id}/signature",
                   params={"t0_index": 36, "pre_window": 25,
                           "post_window": 30}).json()
    assert r["data_label"].startswith("INFERRED")
    assert r["signature"]["delta_probability"] > 0.1
    p = client.get(f"/api/markets/datasets/{dataset_id}/propose").json()
    assert p["candidates"]
    assert "hypothesis" in p["candidates"][0]["rationale"]


def test_research_creates_ordinary_experiment(client, dataset_id):
    r = client.post("/api/markets/research", json={
        "dataset_id": dataset_id, "mechanisms": ["herding"],
        "name": "api-event-bridge", "replications": 2,
        "t0_index": 36, "pre_window": 25, "post_window": 30,
        "t0_step": 80, "total_steps": 240})
    assert r.status_code == 201, r.text
    body = r.json()
    vid = body["version_id"]
    rm = body["research_manifest"]
    assert rm["experiment_hash"] and rm["dataset_hash"]
    # visible through the completely ordinary research API, with the
    # dataset identity bound into the record itself
    reg = client.get(f"/api/research/experiments/{vid}")
    assert reg.status_code == 200
    record = reg.json()
    assert record["research_hash"] == body["research_hash"]
    assert record["external_context"]["dataset_id"] == dataset_id
    assert record["external_context"]["dataset_hash"] == rm["dataset_hash"]


def test_cross_provider_compare(client, dataset_id):
    r = client.post("/api/markets/import", json={
        "provider": "polymarket", "market_id": "912001",
        "fixture_path": POLY_FIXTURE})
    assert r.status_code == 201, r.text
    poly_id = r.json()["dataset_id"]
    c = client.post("/api/markets/compare", json={
        "dataset_a": dataset_id, "dataset_b": poly_id}).json()
    assert "NOT verified" in c["equivalence"]
    assert c["divergence"]["n_aligned"] > 50
    assert "not arbitrage" in c["divergence"]["label"]


def test_mechanism_catalog(client):
    m = client.get("/api/markets/mechanisms").json()
    assert "herding" in m and "description" in m["herding"]
