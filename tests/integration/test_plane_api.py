"""Phase S5: the RESEARCH PLANE API — slice → lineage → verify → reproduce."""

import os

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("nautilus_trader",
                    reason="the slice executes through the S4 bridge")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("plane_api"))
    os.environ["TEZCAT_STORE"] = "local"
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="module")
def version_id(client):
    from tests.unit.test_worlds import small_version
    from tezcat.experiments.registry import Registry
    registry = Registry(os.environ["TEZCAT_DATA_DIR"])
    return registry.register(small_version(experiment_id="exp_plane_api",
                                           total_steps=260))


def test_capabilities_position_honest(client):
    caps = client.get("/api/plane/capabilities").json()
    assert "bootstrap_reference" in caps["forecast_models"]
    assert "reference_cvar" in caps["portfolio_optimizers"]
    assert "not a trading system" in caps["position"]


def test_slice_lineage_verify_reproduce(client, version_id):
    r = client.post("/api/plane/slice", json={
        "history_ref": version_id, "history_cell": "control",
        "stress_cells": ["control", "high_herding"],
        "horizon": 40, "n_paths": 120, "seed": 7,
        "optimizer_params": {"risk_aversion": 0.0},
        "transaction_cost_bps": 0.0, "capital": 200_000})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["risky_weight"] > 0

    lineage = client.get(
        f"/api/plane/artifacts/{out['report_artifact']}/lineage").json()
    types = [a["artifact_type"] for a in lineage["lineage"]]
    assert types[-1] == "research_report"
    assert "forecast" in types and "portfolio" in types

    art = client.get(f"/api/plane/artifacts/{out['forecast_artifact']}",
                     params={"include_payload": True}).json()
    assert "NOT an order" in art["payload"]["disclaimer"]

    verify = client.post(
        f"/api/plane/artifacts/{out['portfolio_artifact']}/verify").json()
    assert verify["success"]

    rep = client.post(f"/api/plane/reproduce/{out['report_artifact']}").json()
    assert rep["success"], rep["checks"]
    assert rep["identical"]


def test_unknown_artifact_404(client):
    assert client.get("/api/plane/artifacts/fct_nope").status_code == 404


def test_invalid_model_rejected(client, version_id):
    r = client.post("/api/plane/slice", json={
        "history_ref": version_id, "model_id": "kronos"})
    assert r.status_code == 422
    assert "ForecastModel protocol" in r.json()["detail"]
