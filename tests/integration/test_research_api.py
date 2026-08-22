"""Phase F10: the research API — register → batch → analyze → report → reproduce."""

import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("research"))
    os.environ["TEZCAT_STORE"] = "local"
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def _spec():
    return {
        "experiment_id": "exp_api",
        "name": "api-herding-ab",
        "root_seed": 42,
        "config": {
            "agents": [
                {"agent_type": "noise_trader", "count": 6},
                {"agent_type": "retail_trader", "count": 5,
                 "params": {"herding": 0.5}},
                {"agent_type": "market_maker", "count": 2},
            ],
            "total_steps": 60,
        },
        "design": {
            "design_type": "ab",
            "question": "Does herding change drawdown?",
            "hypothesis": "Higher herding raises drawdown.",
            "independent_variables": ["agents.1.params.herding"],
            "dependent_variables": ["max_drawdown", "total_trades"],
            "primary_metric": "max_drawdown",
            "control": {"name": "low", "overrides": {"agents.1.params.herding": 0.1}},
            "treatments": [{"name": "high",
                            "overrides": {"agents.1.params.herding": 0.9}}],
            "replications": 3,
        },
    }


def _wait_batch(client, batch_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = client.get(f"/api/research/batches/{batch_id}").json()
        if b.get("status") == "completed" and not b.get("executing_now"):
            return b
        time.sleep(0.2)
    raise TimeoutError("batch did not complete")


def test_research_loop_end_to_end(client):
    # Validate-only first: nothing registered.
    r = client.post("/api/research/experiments", json={**_spec(), "validate_only": True})
    assert r.status_code == 201 and r.json()["validated"]
    assert client.get("/api/research/experiments").json() == []

    # Register.
    r = client.post("/api/research/experiments", json=_spec())
    assert r.status_code == 201
    vid = r.json()["version_id"]
    rh = r.json()["research_hash"]
    assert client.get(f"/api/research/experiments/{vid}").json()["planned_runs"] == 6

    # Batch (background) and wait via status polling.
    r = client.post(f"/api/research/experiments/{vid}/batch")
    assert r.status_code == 202
    batch = _wait_batch(client, r.json()["batch_id"])
    assert batch["completed"] == 6

    # Analyze, summary, report.
    a = client.post(f"/api/research/experiments/{vid}/analyze", json={"seed": 0}).json()
    assert a["comparisons"][0]["treatment"] == "high"
    assert client.get(f"/api/research/experiments/{vid}/analysis").json() == a
    summary = client.get(f"/api/research/experiments/{vid}/summary").json()
    assert summary["complete"] and set(summary["cells"]) == {"low", "high"}
    report = client.get(f"/api/research/experiments/{vid}/report").json()["markdown"]
    assert "## Reproducibility" in report and rh in report

    # Reproduce — by hash prefix, like a citation.
    rep = client.post(f"/api/research/experiments/{rh[:10]}/reproduce",
                      json={"sample": 2}).json()
    assert rep["success"] and rep["runs_verified"] == 2

    # Idempotent registration.
    again = client.post("/api/research/experiments", json=_spec())
    assert again.json()["version_id"] == vid


def test_research_error_paths(client):
    bad = _spec()
    bad["design"]["replications"] = 1
    assert client.post("/api/research/experiments", json=bad).status_code == 422
    assert client.get("/api/research/experiments/expv_missing00").status_code == 404
    assert client.get("/api/research/batches/bat_missing00").status_code == 404
    # Report before analysis on a fresh version.
    fresh = _spec()
    fresh["design"]["replications"] = 2
    vid = client.post("/api/research/experiments", json=fresh).json()["version_id"]
    assert client.get(f"/api/research/experiments/{vid}/report").status_code == 409
