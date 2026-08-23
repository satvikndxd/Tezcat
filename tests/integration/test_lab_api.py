"""Phase S4: the STRATEGY LAB API — worlds → backtest → compare → reproduce."""

import os

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("nautilus_trader",
                    reason="Strategy Lab is an optional bridge")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("lab_api"))
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
    return registry.register(small_version(experiment_id="exp_lab_api"))


@pytest.fixture(scope="module")
def world_ids(client, version_id):
    ids = []
    for cell in ("control", "high_herding"):
        r = client.post("/api/lab/worlds", json={"ref": version_id,
                                                 "cell": cell})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["data_label"] == "SYNTHETIC TEZCAT MARKET WORLD"
        ids.append(body["world_id"])
    return ids


def test_strategies_catalog(client):
    rows = client.get("/api/lab/strategies").json()
    assert {r["strategy_id"] for r in rows} >= {"buy_hold", "ema_cross"}


def test_world_detail_and_quotes(client, world_ids):
    detail = client.get(f"/api/lab/worlds/{world_ids[0]}").json()
    assert detail["manifest"]["research_hash"]
    assert detail["manifest"]["fingerprint"]["fingerprint_version"] == 1
    assert "regime_intervals" in detail["annotations"]
    quotes = client.get(f"/api/lab/worlds/{world_ids[0]}/quotes").json()
    assert quotes["n_total"] > 0
    assert quotes["quotes"][0]["bid"] <= quotes["quotes"][0]["ask"]


def test_unknown_world_404(client):
    assert client.get("/api/lab/worlds/mw_nope").status_code == 404


def test_backtest_compare_reproduce(client, world_ids):
    rids = []
    for wid in world_ids:
        r = client.post("/api/lab/backtests", json={
            "world_id": wid, "strategy_id": "ema_cross",
            "params": {"fast": 8, "slow": 24, "trade_size": 30},
            "starting_cash": 200_000})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["world"]["world_id"] == wid
        assert body["metrics"]["regime_breakdown"]
        rids.append(body["result_id"])

    cmp_result = client.post("/api/lab/results/compare",
                             json={"result_ids": rids}).json()
    assert cmp_result["mode"] == "same strategy, different worlds"

    rep = client.post(f"/api/lab/results/{rids[0]}/reproduce").json()
    assert rep["success"], rep["checks"]


def test_invalid_strategy_rejected(client, world_ids):
    r = client.post("/api/lab/backtests", json={
        "world_id": world_ids[0], "strategy_id": "hodl"})
    assert r.status_code == 422
    assert "unknown strategy" in r.json()["detail"]
