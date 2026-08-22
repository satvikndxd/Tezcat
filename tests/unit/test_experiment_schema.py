"""Phase F4: research-object schema, design validation, registry, lineage."""

import time

import pytest
from pydantic import ValidationError

from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.experiments.registry import Registry, RegistryError
from tezcat.experiments.schema import (
    Arm, DesignSpec, ExperimentVersion, Factor, apply_overrides,
    default_model_card,
)


def _base_config(**kw):
    base = dict(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=10),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=8,
                             params={"herding": 0.5}),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        total_steps=100,
    )
    base.update(kw)
    return ExperimentConfig(**base)


def _ab_design(**kw):
    base = dict(
        design_type="ab",
        question="Does retail herding increase crash severity?",
        hypothesis="Higher herding raises max drawdown at fixed liquidity.",
        independent_variables=["agents.1.params.herding"],
        dependent_variables=["max_drawdown", "total_return"],
        primary_metric="max_drawdown",
        control=Arm(name="low_herding", overrides={"agents.1.params.herding": 0.2}),
        treatments=[Arm(name="high_herding",
                        overrides={"agents.1.params.herding": 0.8})],
        replications=10,
    )
    base.update(kw)
    return DesignSpec(**base)


# ---------------------------------------------------------------------------
# Override application
# ---------------------------------------------------------------------------
def test_apply_overrides_nested_and_list_paths():
    cfg = _base_config()
    out = apply_overrides(cfg, {
        "total_steps": 250,
        "market.tick_size": 0.1,
        "agents.1.params.herding": 0.9,
        "agents.0.count": 20,
    })
    assert out.total_steps == 250
    assert out.market.tick_size == 0.1
    assert out.agents[1].params["herding"] == 0.9
    assert out.agents[0].count == 20
    # Source config untouched (frozen + copy semantics).
    assert cfg.total_steps == 100 and cfg.agents[0].count == 10


def test_apply_overrides_creates_new_params_keys_only():
    cfg = _base_config()
    out = apply_overrides(cfg, {"agents.1.params.panic_threshold": 0.4})
    assert out.agents[1].params["panic_threshold"] == 0.4
    with pytest.raises(KeyError, match="unknown segment"):
        apply_overrides(cfg, {"market.tick_sizee": 0.1})  # typo must be fatal
    with pytest.raises(KeyError, match="out of range"):
        apply_overrides(cfg, {"agents.9.count": 5})


def test_apply_overrides_invalid_value_rejected():
    with pytest.raises(ValidationError):
        apply_overrides(_base_config(), {"total_steps": -5})


# ---------------------------------------------------------------------------
# Design validation
# ---------------------------------------------------------------------------
def test_valid_ab_design():
    d = _ab_design()
    assert [c["cell"] for c in d.cells()] == ["low_herding", "high_herding"]
    assert d.planned_runs() == 20


def test_ab_requires_control_and_treatment():
    with pytest.raises(ValidationError, match="control"):
        _ab_design(control=None)
    with pytest.raises(ValidationError, match="treatment"):
        _ab_design(treatments=[])


def test_one_seed_is_not_evidence():
    with pytest.raises(ValidationError, match="not evidence"):
        _ab_design(replications=1)


def test_primary_metric_must_be_dependent_variable():
    with pytest.raises(ValidationError, match="primary_metric"):
        _ab_design(primary_metric="sharpe_ratio")


def test_treatment_must_differ_from_control():
    with pytest.raises(ValidationError, match="no overrides"):
        _ab_design(treatments=[Arm(name="t", overrides={})])
    with pytest.raises(ValidationError, match="equals the control"):
        _ab_design(treatments=[
            Arm(name="t", overrides={"agents.1.params.herding": 0.2})])


def test_sweep_and_factorial_shape_rules():
    with pytest.raises(ValidationError, match="exactly one factor"):
        DesignSpec(design_type="sweep", question="Sweep something?",
                   hypothesis="Something changes.", dependent_variables=["x"],
                   primary_metric="x", replications=5, factors=[])
    with pytest.raises(ValidationError, match=">=2 factors"):
        DesignSpec(design_type="factorial", question="Interact?",
                   hypothesis="They interact.", dependent_variables=["x"],
                   primary_metric="x", replications=5,
                   factors=[Factor(name="a", path="total_steps", levels=[50, 100])])


def test_factorial_design_matrix_is_full_product():
    d = DesignSpec(
        design_type="factorial",
        question="Does herding interact with liquidity?",
        hypothesis="Crash severity is superadditive in herding x thin books.",
        independent_variables=["agents.1.params.herding", "agents.2.count"],
        dependent_variables=["max_drawdown"],
        primary_metric="max_drawdown",
        factors=[
            Factor(name="herding", path="agents.1.params.herding", levels=[0.2, 0.8]),
            Factor(name="mm", path="agents.2.count", levels=[1, 4]),
        ],
        replications=3,
    )
    cells = [c["cell"] for c in d.cells()]
    assert cells == ["herding=0.2|mm=1", "herding=0.2|mm=4",
                     "herding=0.8|mm=1", "herding=0.8|mm=4"]
    assert d.planned_runs() == 12


# ---------------------------------------------------------------------------
# Experiment versions
# ---------------------------------------------------------------------------
def test_version_resolves_cells_and_allocates_seeds():
    ev = ExperimentVersion("exp1", "herding-ab", _base_config(), _ab_design())
    matrix = ev.design_matrix()
    assert len(matrix) == 20
    seeds = [m["seed"] for m in matrix]
    assert len(set(seeds)) == 20  # distinct across cells and replications
    # Deterministic and order-independent.
    assert ev.seed_for("high_herding", 3) == matrix[13]["seed"]
    ev2 = ExperimentVersion("exp1", "herding-ab", _base_config(), _ab_design())
    assert ev2.research_hash == ev.research_hash
    assert [m["seed"] for m in ev2.design_matrix()] == seeds
    # Cell configs resolve with the treatment applied.
    assert ev.cell_config("high_herding").agents[1].params["herding"] == 0.8
    assert ev.cell_config("low_herding").agents[1].params["herding"] == 0.2


def test_version_rejects_bad_override_path_upfront():
    bad = _ab_design(treatments=[Arm(name="t", overrides={"nope.nope": 1})])
    with pytest.raises(KeyError, match="unknown segment"):
        ExperimentVersion("exp1", "x", _base_config(), bad)


def test_version_enforces_budget():
    d = _ab_design(max_runs=10)  # plans 20
    with pytest.raises(ValueError, match="max_runs"):
        ExperimentVersion("exp1", "x", _base_config(), d)


def test_research_hash_distinguishes_designs_and_seeds():
    cfg = _base_config()
    a = ExperimentVersion("exp1", "x", cfg, _ab_design())
    b = ExperimentVersion("exp1", "x", cfg, _ab_design(replications=20))
    c = ExperimentVersion("exp1", "x", cfg, _ab_design(), root_seed=7)
    assert len({a.research_hash, b.research_hash, c.research_hash}) == 3


# ---------------------------------------------------------------------------
# Registry: registration without execution, lineage, dedupe
# ---------------------------------------------------------------------------
def test_register_baseline_and_herding_treatment_without_running(tmp_path):
    """The Phase F4 acceptance scenario from the specification."""
    reg = Registry(str(tmp_path))
    cfg = _base_config()

    baseline = ExperimentVersion(
        "exp_herding", "baseline", cfg,
        DesignSpec(design_type="baseline",
                   question="What does the stable ecology look like?",
                   hypothesis="The baseline market is stable at these settings.",
                   dependent_variables=["max_drawdown"],
                   primary_metric="max_drawdown", replications=1))
    bid = reg.register(baseline)

    treatment = ExperimentVersion(
        "exp_herding", "herding-ab", cfg, _ab_design(),
        parent_version_id=bid)
    tid = reg.register(treatment)

    # Registered, linked, and loadable with hash re-verification.
    assert reg.get(bid)["planned_runs"] == 1
    assert reg.get(tid)["parent_version_id"] == bid
    assert reg.lineage(tid) == [bid, tid]
    assert [c["version_id"] for c in reg.children(bid)] == [tid]
    loaded = reg.load(tid)
    assert loaded.research_hash == treatment.research_hash
    assert loaded.design_matrix() == treatment.design_matrix()
    # Nothing was executed: no batches, no results.
    assert reg.get_batch("anything") is None


def test_registry_dedupe_and_immutability(tmp_path):
    reg = Registry(str(tmp_path))
    ev = ExperimentVersion("exp1", "x", _base_config(), _ab_design())
    assert reg.register(ev) == reg.register(ev)  # idempotent
    assert reg.by_hash(ev.research_hash)["version_id"] == ev.version_id
    assert reg.by_hash("0" * 64) is None


def test_registry_rejects_unknown_parent(tmp_path):
    reg = Registry(str(tmp_path))
    ev = ExperimentVersion("exp1", "x", _base_config(), _ab_design(),
                           parent_version_id="expv_missing")
    with pytest.raises(RegistryError, match="parent"):
        reg.register(ev)


def test_load_detects_tampered_record(tmp_path):
    reg = Registry(str(tmp_path))
    ev = ExperimentVersion("exp1", "x", _base_config(), _ab_design())
    vid = reg.register(ev)
    path = reg.root / "versions" / f"{vid}.json"
    import json
    record = json.loads(path.read_text())
    record["root_seed"] = 999  # silent edit of a stored research object
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="research_hash"):
        reg.load(vid)


def test_model_card_states_unsupported_claims():
    card = default_model_card()
    assert any("prediction" in c for c in card.claims_not_supported)
    assert card.calibration_status.startswith("uncalibrated")


# ---------------------------------------------------------------------------
# Registry scale (spec: queries at 10k records)
# ---------------------------------------------------------------------------
def test_registry_queries_at_scale(tmp_path):
    reg = Registry(str(tmp_path))
    # Simulate a large index without paying 10k version-construction costs.
    index = {}
    for i in range(10_000):
        vid = f"expv_{i:012d}"
        index[vid] = {"version_id": vid, "experiment_id": f"exp_{i % 100}",
                      "name": f"v{i}", "research_hash": f"{i:064d}",
                      "design_type": "ab", "primary_metric": "max_drawdown",
                      "planned_runs": 20,
                      "parent_version_id": f"expv_{i - 1:012d}" if i % 10 else None}
    reg._write(reg.root / "index.json", index)

    t0 = time.time()
    assert len(reg.list()) == 10_000
    assert len(reg.list(experiment_id="exp_7")) == 100
    kids = reg.children("expv_000000000041")
    elapsed = time.time() - t0
    assert [k["version_id"] for k in kids] == ["expv_000000000042"]
    assert elapsed < 5.0, f"registry queries too slow at 10k records: {elapsed:.1f}s"


def test_external_context_changes_research_identity_and_is_detached():
    context = {
        "data_class": "OBSERVED_CONTEXT",
        "dataset_id": "extds_fixture",
        "dataset_checksum": "a" * 64,
        "raw_checksum": "b" * 64,
        "provider": "kalshi",
        "market_ids": ["KXTEST-YES"],
        "signature_id": "sig_fixture",
        "signature_hash": "c" * 64,
    }
    ev = ExperimentVersion("exp1", "x", _base_config(), _ab_design(), external_context=context)
    context["dataset_checksum"] = "d" * 64
    assert ev.external_context["dataset_checksum"] == "a" * 64
    changed = ExperimentVersion(
        "exp1", "x", _base_config(), _ab_design(),
        external_context={**ev.external_context, "dataset_checksum": "d" * 64},
    )
    assert changed.research_hash != ev.research_hash
    loaded = ExperimentVersion.from_dict(ev.to_dict())
    assert loaded.research_hash == ev.research_hash
