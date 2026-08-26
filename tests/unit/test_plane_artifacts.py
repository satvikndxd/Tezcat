"""S5-A: research artifact graph — identity, immutability, lineage."""

import json

import pytest

from tezcat.plane import ArtifactGraph, PlaneError


@pytest.fixture()
def graph(tmp_path):
    return ArtifactGraph(str(tmp_path))


class TestRegistration:
    def test_content_addressed_and_idempotent(self, graph):
        a = graph.register("forecast", {"x": 1}, config={"m": "ref"})
        b = graph.register("forecast", {"x": 1}, config={"m": "ref"})
        assert a.artifact_id == b.artifact_id
        assert a.artifact_id.startswith("fct_")
        c = graph.register("forecast", {"x": 2}, config={"m": "ref"})
        assert c.artifact_id != a.artifact_id

    def test_unknown_type_rejected(self, graph):
        with pytest.raises(PlaneError, match="unknown artifact type"):
            graph.register("order", {"x": 1})

    def test_parents_must_exist(self, graph):
        with pytest.raises(PlaneError, match="register parents before"):
            graph.register("portfolio", {"w": 0.5}, parents=["f" * 64])

    def test_external_identity_carried_verbatim(self, graph):
        a = graph.register("market_world", {"world_id": "mw_abc"},
                           external_identity="deadbeef" * 8)
        assert graph.get(a.artifact_id).external_identity == "deadbeef" * 8

    def test_environment_recorded_but_not_identity(self, graph):
        a = graph.register("analysis", {"x": 1})
        assert a.environment["python_version"]
        assert "plane_schema_version" in a.environment
        # created_at/environment differences must not change the hash:
        b = graph.register("analysis", {"x": 1})
        assert a.artifact_hash == b.artifact_hash


class TestIntegrity:
    def test_payload_checksum_verified_on_load(self, graph):
        a = graph.register("risk_report", {"metric": 1.5})
        assert graph.payload(a.artifact_id) == {"metric": 1.5}
        path = graph.root / a.artifact_id / "payload.json"
        path.write_text(json.dumps({"metric": 99}))
        with pytest.raises(PlaneError, match="tampered"):
            graph.payload(a.artifact_id)

    def test_verify(self, graph):
        a = graph.register("analysis", {"x": 1})
        assert graph.verify(a.artifact_id)["success"]

    def test_unknown_artifact(self, graph):
        with pytest.raises(PlaneError, match="unknown artifact"):
            graph.get("fct_nope")


class TestLineage:
    def test_chain_traversal(self, graph):
        ds = graph.register("external_dataset", {"prices": [1, 2]})
        fc = graph.register("forecast", {"mean": 0.1}, parents=[ds])
        pf = graph.register("portfolio", {"w": 0.3}, parents=[fc])
        chain = graph.lineage(pf.artifact_id)
        assert [a.artifact_type for a in chain] == \
            ["external_dataset", "forecast", "portfolio"]
        assert chain[1].parent_hashes == [ds.artifact_hash]

    def test_diamond_dedup(self, graph):
        ds = graph.register("external_dataset", {"p": [1]})
        f1 = graph.register("forecast", {"m": 1}, parents=[ds])
        f2 = graph.register("forecast", {"m": 2}, parents=[ds])
        pf = graph.register("portfolio", {"w": 1}, parents=[f1, f2])
        chain = graph.lineage(pf.artifact_id)
        assert len([a for a in chain
                    if a.artifact_type == "external_dataset"]) == 1

    def test_children(self, graph):
        ds = graph.register("external_dataset", {"p": [1, 2, 3]})
        graph.register("forecast", {"m": 1}, parents=[ds])
        kids = graph.children(ds.artifact_id)
        assert len(kids) == 1 and kids[0]["artifact_type"] == "forecast"

    def test_list_filter(self, graph):
        graph.register("external_dataset", {"p": [1]})
        graph.register("analysis", {"a": 1})
        assert len(graph.list("analysis")) == 1
        assert len(graph.list()) == 2
