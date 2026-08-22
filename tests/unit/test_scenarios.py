"""Phase S2: scenario drafts, templates, canonical identity, conversions."""

import pytest

from tezcat.experiments.registry import Registry
from tezcat.experiments.scenarios import (
    ScenarioError, ScenarioStore, scenario_from_preset, scenario_templates,
    validate_spec, version_from_spec,
)


def _spec(**overrides):
    spec = {
        "experiment_id": "exp_custom",
        "name": "my-market",
        "root_seed": 42,
        "config": {
            "agents": [
                {"agent_type": "noise_trader", "count": 8},
                {"agent_type": "retail_trader", "count": 6,
                 "params": {"herding": 0.6}},
                {"agent_type": "market_maker", "count": 2},
            ],
            "total_steps": 120,
        },
        "design": {
            "design_type": "baseline",
            "question": "What does this custom ecology produce?",
            "hypothesis": "A stable market with moderate herding.",
            "dependent_variables": ["max_drawdown", "total_return"],
            "primary_metric": "max_drawdown",
            "replications": 3,
        },
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# Validation and canonical identity
# ---------------------------------------------------------------------------
def test_validate_spec_returns_identity_preview():
    out = validate_spec(_spec())
    assert out["valid"] and out["version_id"].startswith("expv_")
    assert len(out["research_hash"]) == 64
    assert out["planned_runs"] == 3


def test_identical_spec_identical_identity_changed_spec_new_identity():
    a, b = validate_spec(_spec()), validate_spec(_spec())
    assert a["research_hash"] == b["research_hash"]     # deterministic
    c = validate_spec(_spec(root_seed=7))
    d_spec = _spec()
    d_spec["config"]["total_steps"] = 121
    d = validate_spec(d_spec)
    assert len({a["research_hash"], c["research_hash"], d["research_hash"]}) == 3


def test_invalid_specs_rejected_with_exact_reason():
    bad = _spec()
    bad["design"]["replications"] = 0
    with pytest.raises(ScenarioError, match="replications"):
        validate_spec(bad)
    with pytest.raises(ScenarioError, match="missing required section"):
        validate_spec({"name": "x"})
    bad2 = _spec()
    bad2["config"]["agents"] = []
    with pytest.raises(ScenarioError, match="agent population"):
        validate_spec(bad2)
    bad3 = _spec()
    bad3["design"]["primary_metric"] = "sharpe"
    with pytest.raises(ScenarioError, match="primary_metric"):
        validate_spec(bad3)


def test_spec_roundtrip_through_registration(tmp_path):
    """Scenario → experiment uses the exact CLI path: same identity."""
    import json
    reg = Registry(str(tmp_path))
    spec = _spec()
    vid = reg.register(version_from_spec(spec))
    # JSON round-trip (export → import) preserves the research identity.
    again = version_from_spec(json.loads(json.dumps(spec)))
    assert again.version_id == vid
    assert reg.register(again) == vid   # idempotent, content-addressed


# ---------------------------------------------------------------------------
# Draft store: mutable drafts, immutable experiments
# ---------------------------------------------------------------------------
def test_draft_crud_and_duplicate(tmp_path):
    store = ScenarioStore(str(tmp_path))
    rec = store.save("my market", _spec())
    sid = rec["scenario_id"]
    assert sid.startswith("scn_")
    assert store.get(sid)["name"] == "my market"
    assert [s["scenario_id"] for s in store.list()] == [sid]

    # Drafts are mutable (no research identity to protect).
    edited = _spec()
    edited["config"]["total_steps"] = 200
    store.save("my market v2", edited, scenario_id=sid)
    assert store.get(sid)["spec"]["config"]["total_steps"] == 200

    dup = store.duplicate(sid)
    assert dup["scenario_id"] != sid and "(copy)" in dup["name"]
    assert dup["spec"] == store.get(sid)["spec"]

    store.delete(dup["scenario_id"])
    with pytest.raises(ScenarioError, match="unknown scenario"):
        store.get(dup["scenario_id"])


def test_draft_edit_creates_new_experiment_identity(tmp_path):
    """The critical invariant: editing a draft and re-registering mints a
    NEW research object; the old one is untouched."""
    reg = Registry(str(tmp_path))
    store = ScenarioStore(str(tmp_path))
    rec = store.save("m", _spec())
    v1 = reg.register(version_from_spec(rec["spec"]))

    edited = dict(rec["spec"])
    edited["config"] = dict(edited["config"], total_steps=240)
    store.save("m", edited, scenario_id=rec["scenario_id"])
    v2 = reg.register(version_from_spec(store.get(rec["scenario_id"])["spec"]))

    assert v1 != v2
    assert reg.get(v1)["config"]["total_steps"] == 120   # history intact
    assert reg.get(v2)["config"]["total_steps"] == 240


def test_store_rejects_invalid_draft(tmp_path):
    store = ScenarioStore(str(tmp_path))
    bad = _spec()
    bad["design"]["replications"] = -1
    with pytest.raises(ScenarioError):
        store.save("bad", bad)


# ---------------------------------------------------------------------------
# Templates and preset conversion
# ---------------------------------------------------------------------------
def test_templates_are_valid_and_registrable(tmp_path):
    reg = Registry(str(tmp_path))
    templates = scenario_templates()
    ids = {t["template_id"] for t in templates}
    assert ids == {"high_leverage_spiral", "retail_panic",
                   "momentum_bubble", "liquidity_vacuum"}
    hashes = set()
    for t in templates:
        preview = validate_spec(t["spec"])
        hashes.add(preview["research_hash"])
        reg.register(version_from_spec(t["spec"]))   # full canonical path
    assert len(hashes) == 4                          # all distinct research objects
    # The spiral template really enables margin mechanics.
    spiral = next(t for t in templates if t["template_id"] == "high_leverage_spiral")
    assert spiral["spec"]["config"]["risk"]["enabled"] is True
    assert "risk_forced_volume" in spiral["spec"]["design"]["dependent_variables"]


def test_preset_to_scenario_conversion(tmp_path):
    reg = Registry(str(tmp_path))
    for preset in ("stable_baseline", "flash_crash", "bubble_formation"):
        spec = scenario_from_preset(preset, replications=2)
        preview = validate_spec(spec)
        assert preview["planned_runs"] == 2
        reg.register(version_from_spec(spec))
    with pytest.raises(ScenarioError, match="unknown preset"):
        scenario_from_preset("nope")
