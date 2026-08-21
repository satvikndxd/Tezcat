"""API integration tests using a temp data dir and instant (no-delay) runs."""

import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    os.environ["TEZCAT_STORE"] = "local"
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def _wait_completed(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("completed", "failed", "cancelled"):
            return r
        time.sleep(0.2)
    raise TimeoutError("run did not finish")


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_presets_listed(client):
    presets = client.get("/api/presets").json()
    ids = {p["preset_id"] for p in presets}
    assert {"stable_baseline", "flash_crash", "bubble_formation"} <= ids
    p = client.get("/api/presets/flash_crash").json()
    assert "config_template" in p


def test_unknown_preset_404(client):
    assert client.get("/api/presets/nope").status_code == 404


def test_full_lifecycle(client):
    # create experiment from preset with a short run for test speed
    resp = client.post("/api/presets/stable_baseline/experiments", json={
        "name": "it-test", "overrides": {"total_steps": 200, "step_delay_ms": 0, "shocks": []},
    })
    assert resp.status_code == 201
    exp = resp.json()
    assert exp["config_hash"]
    assert client.get(f"/api/experiments/{exp['experiment_id']}").status_code == 200

    # report should 404 before run exists / completes
    resp = client.post(f"/api/experiments/{exp['experiment_id']}/runs", json={"seed": 11})
    assert resp.status_code == 201
    run = resp.json()
    assert run["seed"] == 11

    final = _wait_completed(client, run["run_id"])
    assert final["status"] == "completed"
    assert final["steps_completed"] == 200

    # market/history/trades/metrics/report all served
    market = client.get(f"/api/runs/{run['run_id']}/market").json()
    assert market["snapshot"]["step"] == 200
    hist = client.get(f"/api/runs/{run['run_id']}/history").json()
    assert len(hist["snapshots"]) == 200
    assert hist["next_start"] == 200
    trades = client.get(f"/api/runs/{run['run_id']}/trades?limit=10").json()["trades"]
    assert len(trades) == 10
    metrics = client.get(f"/api/runs/{run['run_id']}/metrics").json()["metrics"]
    assert metrics[-1]["step"] == 200
    report = client.get(f"/api/runs/{run['run_id']}/report").json()
    assert report["run_id"] == run["run_id"]
    assert "agent_pnl_by_type" in report

    # provenance (Phase F2): report is traceable to config, seed, and hashes
    prov = report["provenance"]
    assert prov["config_hash"] == exp["config_hash"]
    assert prov["seed"] == 11
    assert len(prov["state_hash"]) == 64
    assert len(prov["event_hash"]) == 64

    # export
    export = client.post(f"/api/runs/{run['run_id']}/export").json()
    assert export["files"]


def test_stale_experiment_hash_refused(client):
    """A stored experiment whose config no longer matches its hash must not run."""
    resp = client.post("/api/presets/stable_baseline/experiments", json={
        "name": "stale", "overrides": {"total_steps": 100, "step_delay_ms": 0, "shocks": []}})
    exp = resp.json()
    # Corrupt the stored hash to simulate a hand-edited/stale record.
    from tezcat.api import app as app_module
    stored = app_module.store.load_experiment(exp["experiment_id"])
    stored["config_hash"] = "0" * 64
    app_module.store.save_experiment(stored)
    r = client.post(f"/api/experiments/{exp['experiment_id']}/runs", json={"seed": 1})
    assert r.status_code == 409
    assert "config_hash" in r.json()["detail"]


def test_incremental_history_polling(client):
    resp = client.post("/api/presets/stable_baseline/experiments", json={
        "overrides": {"total_steps": 150, "step_delay_ms": 0, "shocks": []}})
    exp = resp.json()
    run = client.post(f"/api/experiments/{exp['experiment_id']}/runs", json={}).json()
    _wait_completed(client, run["run_id"])
    first = client.get(f"/api/runs/{run['run_id']}/history?start=0&limit=100").json()
    second = client.get(
        f"/api/runs/{run['run_id']}/history?start={first['next_start']}").json()
    assert first["next_start"] == 100
    assert second["snapshots"][0]["step"] == 101


def test_shock_injection_and_pause(client):
    resp = client.post("/api/presets/stable_baseline/experiments", json={
        "overrides": {"total_steps": 3000, "step_delay_ms": 5}})
    exp = resp.json()
    run = client.post(f"/api/experiments/{exp['experiment_id']}/runs", json={}).json()
    rid = run["run_id"]
    time.sleep(1.0)

    r = client.post(f"/api/runs/{rid}/shocks", json={
        "shock_type": "whale_order", "side": "sell", "magnitude": 500, "duration": 10})
    assert r.status_code == 202
    time.sleep(1.0)
    shocks = client.get(f"/api/runs/{rid}/shocks").json()
    assert any(s["trigger_reason"] == "manual" for s in shocks)

    assert client.post(f"/api/runs/{rid}/pause").json()["status"] == "paused"
    steps_a = client.get(f"/api/runs/{rid}").json()["steps_completed"]
    time.sleep(0.6)
    steps_b = client.get(f"/api/runs/{rid}").json()["steps_completed"]
    assert steps_a == steps_b
    assert client.post(f"/api/runs/{rid}/resume").json()["status"] == "running"
    cancelled = client.post(f"/api/runs/{rid}/cancel")
    assert cancelled.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/runs/{rid}").json()["status"] == "cancelled":
            break
        time.sleep(0.1)
    assert client.get(f"/api/runs/{rid}").json()["status"] == "cancelled"


def test_bad_input_validation(client):
    assert client.post("/api/experiments", json={"name": "x", "config": {}}).status_code == 422
    assert client.post("/api/presets/flash_crash/experiments", json={
        "overrides": {"total_steps": -5}}).status_code == 422
    r = client.post("/api/runs/run_missing/shocks", json={
        "shock_type": "whale_order", "magnitude": 10})
    assert r.status_code == 409
    r = client.post("/api/runs/run_missing/shocks", json={
        "shock_type": "bad_type", "magnitude": 10})
    assert r.status_code == 422
    assert client.get("/api/runs/run_missing").status_code == 404
