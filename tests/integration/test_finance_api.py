"""Phase S6: the FINANCE API — run case → results → report → reproduce."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SPEC = json.loads((Path(__file__).resolve().parents[2] / "examples"
                   / "finance" / "meridian_case.json").read_text())


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["TEZCAT_DATA_DIR"] = str(tmp_path_factory.mktemp("fin_api"))
    os.environ["TEZCAT_STORE"] = "local"
    from tezcat.api import app as app_module
    import importlib
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="module")
def registered(client):
    r = client.post("/api/finance/cases", json={"spec": SPEC})
    assert r.status_code == 201, r.text
    return r.json()


def test_validate_only(client):
    r = client.post("/api/finance/cases",
                    json={"spec": SPEC, "validate_only": True})
    assert r.status_code == 201
    assert r.json()["version_id"].startswith("fin_")


def test_case_results_and_report(client, registered):
    ca = registered["case_artifact"]
    detail = client.get(f"/api/finance/cases/{ca}").json()
    assert detail["results"]["triangulation"]["methods"]
    assert detail["results"]["pro_forma"]["year_1_verdict"] == "accretive"
    md = client.get(f"/api/finance/cases/{ca}/report").json()["markdown"]
    assert "Executive summary" in md and "not investment advice" in md


def test_listing(client, registered):
    rows = client.get("/api/finance/cases").json()
    assert any(r["case_artifact"] == registered["case_artifact"]
               for r in rows)


def test_reproduce(client, registered):
    rep = client.post(
        f"/api/finance/cases/{registered['case_artifact']}/reproduce").json()
    assert rep["success"], rep["checks"]


def test_invalid_spec_422(client):
    bad = json.loads(json.dumps(SPEC))
    bad["dcf"]["wacc"] = 0.09
    bad["dcf"]["terminal_growth"] = 0.095
    r = client.post("/api/finance/cases", json={"spec": bad})
    assert r.status_code == 422
    assert "WACC" in r.json()["detail"]
