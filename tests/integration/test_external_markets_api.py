from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tezcat.core.config import config_hash
from tezcat.external.providers.base import ProviderHttpError
from tezcat.external.providers.kalshi import KalshiAdapter
from tezcat.external.service import ExternalMarketService


FIXTURE = Path(__file__).parents[1] / "fixtures" / "kalshi" / "market_bundle.json"


class FixtureClient:
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        if path not in self.responses:
            raise AssertionError(f"unexpected fixture path {path}")
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        pass


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("TEZCAT_DATA_DIR", str(tmp_path / "registry"))
    app_module = importlib.import_module("tezcat.api.app")
    app_module = importlib.reload(app_module)
    markets_module = importlib.import_module("tezcat.api.markets")

    fixture = json.loads(FIXTURE.read_text())
    provider_client = FixtureClient({
        "/markets/KXTEST-YES": {"market": fixture["market"]},
        "/markets/KXTEST-YES/orderbook": fixture["orderbook"],
        "/markets/trades": fixture["trades"],
    })
    service = ExternalMarketService(
        app_module.store,
        providers={"kalshi": KalshiAdapter(client=provider_client)},
    )
    monkeypatch.setattr(markets_module, "_service", lambda request: service)

    with TestClient(app_module.app) as client:
        yield client, service, app_module


def _snapshot(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/markets/KXTEST-YES/snapshot?provider=kalshi",
        json={"source_id": "fixture-kx", "license_terms": "fixture-only"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _proposal(dataset_id: str) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "market_id": "KXTEST-YES",
        "question": "Which declared mechanism resembles the observed event?",
        "hypothesis": "Thin liquidity may amplify a synthetic probability displacement.",
    }


def test_static_s3_routes_are_not_captured_by_market_id(api_client):
    client, _, _ = api_client
    assert client.get("/api/markets/providers").status_code == 200
    assert client.get("/api/markets/events?provider=unknown").status_code == 422
    assert client.get("/api/markets/datasets").status_code == 200
    assert client.post("/api/markets/divergence", json={"probability_a": 0.4, "probability_b": 0.6}).status_code == 200
    proposal = client.post(
        "/api/markets/research",
        json=_proposal("missing-dataset"),
    )
    assert proposal.status_code == 404


def test_snapshot_dataset_signature_and_approval_gated_compile(api_client):
    client, service, app_module = api_client
    snapshot = _snapshot(client)
    manifest = snapshot["manifest"]
    dataset_id = manifest["dataset_id"]

    listed = client.get("/api/markets/datasets")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["dataset_id"] == dataset_id

    stored = client.get(f"/api/markets/datasets/{dataset_id}")
    assert stored.status_code == 200
    assert stored.json()["manifest"]["normalized_checksum"] == manifest["normalized_checksum"]
    assert stored.json()["normalized"]["data_class"] == "OBSERVED"

    signature_response = client.post(
        f"/api/markets/datasets/{dataset_id}/signature",
        json={"window": {"pre": "2026-08-22T00:00:00Z", "post": "2026-08-22T01:00:00Z"}},
    )
    assert signature_response.status_code == 200, signature_response.text
    signature_payload = signature_response.json()
    signature = signature_payload["signature"]
    assert signature_payload["data_class"] == "INFERRED"
    assert signature["dataset_id"] == dataset_id

    base_config = app_module.build_config("stable_baseline").model_dump(mode="json")
    proposal = _proposal(dataset_id)
    unapproved = client.post(
        "/api/markets/research/compile",
        json={**proposal, "signature": signature, "base_config": base_config,
              "experiment_id": "exp_external", "name": "External baseline"},
    )
    assert unapproved.status_code == 409

    compiled = client.post(
        "/api/markets/research/compile",
        json={**proposal, "signature": signature, "base_config": base_config,
              "experiment_id": "exp_external", "name": "External baseline",
              "approved": True, "root_seed": 123, "replications": 1},
    )
    assert compiled.status_code == 201, compiled.text
    payload = compiled.json()
    record = payload["experiment"]
    context = record["external_context"]
    assert payload["data_class"] == "MODEL_RESULT"
    assert context["dataset_id"] == dataset_id
    assert context["dataset_checksum"] == manifest["normalized_checksum"]
    assert context["raw_checksum"] == manifest["raw_checksum"]
    assert context["provider"] == "kalshi"
    assert record["config_hash"] == config_hash(app_module.build_config("stable_baseline"))
    assert record["design"]["design_type"] == "baseline"
    assert payload["external_research_manifest"]["dataset_hash"] == manifest["normalized_checksum"]
    assert payload["external_research_manifest"]["signature_hash"] == signature["checksum"]
    assert app_module.research_registry.get_external_manifest(payload["version_id"]) == payload["external_research_manifest"]

    loaded = app_module.research_registry.load(payload["version_id"])
    assert loaded.external_context == context
    assert loaded.config.model_dump(mode="json") == base_config
    assert service.datasets.get(dataset_id)["raw_checksum"] == manifest["raw_checksum"]


def test_report_and_reproduction_verify_external_dataset_identity(api_client):
    client, service, app_module = api_client
    snapshot = _snapshot(client)
    manifest = snapshot["manifest"]
    signature = client.post(
        f"/api/markets/datasets/{manifest['dataset_id']}/signature",
        json={"window": {"pre": "2026-08-22T00:00:00Z", "post": "2026-08-22T01:00:00Z"}},
    ).json()["signature"]
    base_config = app_module.build_config("stable_baseline").model_dump(mode="json")
    base_config["total_steps"] = 20
    base_config["shocks"] = []
    proposal = {
        **_proposal(manifest["dataset_id"]),
        "primary_metric": "max_drawdown",
        "signature": signature,
        "base_config": base_config,
        "experiment_id": "exp_external_repro",
        "name": "External reproducibility baseline",
        "approved": True,
        "root_seed": 321,
        "replications": 1,
    }
    compiled = client.post("/api/markets/research/compile", json=proposal)
    assert compiled.status_code == 201, compiled.text
    version_id = compiled.json()["version_id"]

    from tezcat.analysis import analyze, build_report
    from tezcat.experiments.batch import BatchRunner
    from tezcat.experiments.reproduce import reproduce

    BatchRunner(app_module.research_registry).run(version_id)
    analyze(app_module.research_registry, version_id, seed=0)
    report = build_report(app_module.research_registry, version_id)
    assert manifest["normalized_checksum"] in report
    assert signature["signature_id"] in report

    verified = reproduce(app_module.research_registry, version_id, sample=None)
    assert verified["success"]
    assert any(check["check"] == "external dataset identity verified" and check["ok"]
               for check in verified["checks"])

    row = next(row for row in service.datasets.list() if row["dataset_id"] == manifest["dataset_id"])
    service.datasets.store.save_artifact(row["artifact_prefix"], "normalized.json", {"tampered": True})
    tampered = reproduce(app_module.research_registry, version_id, sample=None)
    assert not tampered["success"]
    assert any(check["check"] == "external dataset identity verified" and not check["ok"]
               for check in tampered["checks"])


def test_provider_failure_is_explicit_and_read_only(api_client, monkeypatch):
    client, service, _ = api_client

    def fail(*args, **kwargs):
        raise ProviderHttpError("fixture provider timeout")

    monkeypatch.setattr(service.provider("kalshi"), "get_market", fail)
    response = client.get("/api/markets/KXTEST-YES?provider=kalshi")
    assert response.status_code == 502
    assert "timeout" in response.json()["detail"]
    assert "order" not in response.text.lower()
