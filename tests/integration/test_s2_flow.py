"""Phase S2 integration: the complete product path over HTTP.

Custom Scenario → Validate → Experiment → TradeOps Queue → Worker →
Artifacts → Analyze → Report → Reproduce.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("s2"))
    os.environ["TEZCAT_STORE"] = "local"
    os.environ["TEZCAT_OPS_WORKERS"] = "1"
    os.environ["TEZCAT_OPS_CHUNK"] = "3"
    os.environ["TEZCAT_MAX_REPLICATIONS"] = "50"
    os.environ["TEZCAT_MAX_STEPS"] = "1000"
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def _spec():
    return {
        "experiment_id": "exp_s2",
        "name": "s2-custom-market",
        "root_seed": 42,
        "config": {
            "agents": [
                {"agent_type": "noise_trader", "count": 6},
                {"agent_type": "retail_trader", "count": 5,
                 "params": {"herding": 0.7}},
                {"agent_type": "market_maker", "count": 2},
            ],
            "shocks": [{"shock_id": "whale", "shock_type": "whale_order",
                        "trigger": {"kind": "scheduled", "step": 40},
                        "side": "sell", "magnitude": 300, "duration": 5}],
            "total_steps": 80,
        },
        "design": {
            "design_type": "baseline",
            "question": "What does this custom shocked ecology produce?",
            "hypothesis": "The whale sell dents the book measurably.",
            "dependent_variables": ["max_drawdown", "total_return"],
            "primary_metric": "max_drawdown",
            "replications": 4,
        },
    }


def _wait_job(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/ops/jobs/{job_id}").json()
        if job["state"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return job
        time.sleep(0.1)
    raise TimeoutError("job did not finish")


def test_full_s2_product_path(client):
    # 1. Draft: save, list, edit — drafts are mutable.
    r = client.post("/api/scenarios", json={"name": "my market", "spec": _spec()})
    assert r.status_code == 201
    sid = r.json()["scenario_id"]
    assert any(s["scenario_id"] == sid for s in client.get("/api/scenarios").json())

    # 2. Validate: exact identity preview through the canonical machinery.
    preview = client.post("/api/scenarios/validate", json={"spec": _spec()}).json()
    assert preview["valid"] and preview["planned_runs"] == 4
    rh = preview["research_hash"]

    # 3. Register: mints the immutable experiment (idempotent).
    reg1 = client.post("/api/scenarios/register", json={"spec": _spec()}).json()
    assert reg1["research_hash"] == rh
    vid = reg1["version_id"]
    assert client.post("/api/scenarios/register",
                       json={"spec": _spec()}).json()["version_id"] == vid

    # 4. TradeOps: queue it; duplicate guard; watch it execute.
    job = client.post("/api/ops/jobs", json={"version_ref": vid}).json()
    assert job["state"] == "QUEUED"
    dup = client.post("/api/ops/jobs", json={"version_ref": rh[:10]})
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "duplicate_execution"

    done = _wait_job(client, job["job_id"])
    assert done["state"] == "COMPLETED" and done["completed"] == 4
    status = client.get("/api/ops/status").json()
    assert status["completed"] >= 1
    assert client.get("/api/ops/workers").json()[0]["worker_id"] == "worker-01"

    # 5. Artifacts → analysis → report → reproduction (existing S1 loop).
    a = client.post(f"/api/research/experiments/{vid}/analyze",
                    json={"seed": 0}).json()
    assert a["descriptives"]["baseline"]["max_drawdown"]["n"] == 4
    report = client.get(f"/api/research/experiments/{vid}/report").json()["markdown"]
    assert rh in report
    rep = client.post(f"/api/research/experiments/{rh[:10]}/reproduce",
                      json={"sample": 2}).json()
    assert rep["success"] and rep["runs_verified"] == 2


def test_ops_guardrails_over_http(client):
    big = _spec()
    big["design"]["replications"] = 60          # over TEZCAT_MAX_REPLICATIONS=50
    big["name"] = "too-big"
    vid = client.post("/api/scenarios/register", json={"spec": big}).json()["version_id"]
    r = client.post("/api/ops/jobs", json={"version_ref": vid})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "limit_replications"

    long = _spec()
    long["config"]["total_steps"] = 2000        # over TEZCAT_MAX_STEPS=1000
    long["name"] = "too-long"
    vid2 = client.post("/api/scenarios/register", json={"spec": long}).json()["version_id"]
    r2 = client.post("/api/ops/jobs", json={"version_ref": vid2})
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "limit_steps"


def test_templates_and_preset_conversion_endpoints(client):
    templates = client.get("/api/scenarios/templates").json()
    assert {t["template_id"] for t in templates} == {
        "high_leverage_spiral", "retail_panic", "momentum_bubble",
        "liquidity_vacuum"}
    conv = client.get("/api/scenarios/from-preset/flash_crash").json()
    assert conv["spec"]["config"]["total_steps"] > 0
    ok = client.post("/api/scenarios/validate", json={"spec": conv["spec"]})
    assert ok.status_code == 200

    bad = client.post("/api/scenarios/validate",
                      json={"spec": {"config": {}, "design": {}}})
    assert bad.status_code == 422
    assert "agents" in bad.json()["detail"] or "Field required" in bad.json()["detail"]


def test_scenario_duplicate_and_delete_over_http(client):
    sid = client.post("/api/scenarios",
                      json={"name": "dup-me", "spec": _spec()}).json()["scenario_id"]
    dup = client.post(f"/api/scenarios/{sid}/duplicate").json()
    assert dup["scenario_id"] != sid and "(copy)" in dup["name"]
    assert client.delete(f"/api/scenarios/{dup['scenario_id']}").status_code == 200
    assert client.get(f"/api/scenarios/{dup['scenario_id']}").status_code == 404
