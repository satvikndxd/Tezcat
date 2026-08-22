"""Phase F10: reproduce verification and the research CLI.

The acceptance standard: a researcher completes register → run → analyze →
report → reproduce using only the documented CLI, and tampered artifacts
are detected.
"""

import json

import pytest

from tezcat.cli import main as cli_main
from tezcat.core.config import AgentGroupConfig, AgentType, ExperimentConfig
from tezcat.experiments.batch import BatchRunner, batch_id_for
from tezcat.experiments.registry import Registry
from tezcat.experiments.reproduce import (
    ReproductionError, reproduce, resolve_reference,
)
from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion


def _spec_dict():
    return {
        "experiment_id": "exp_cli",
        "name": "cli-herding-ab",
        "root_seed": 42,
        "config": ExperimentConfig(
            agents=[
                AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=6),
                AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=5,
                                 params={"herding": 0.5}),
                AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
            ],
            total_steps=60).model_dump(mode="json"),
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


def _registered_and_run(tmp_path):
    reg = Registry(str(tmp_path))
    spec = _spec_dict()
    version = ExperimentVersion(
        spec["experiment_id"], spec["name"],
        ExperimentConfig.model_validate(spec["config"]),
        DesignSpec.model_validate(spec["design"]), root_seed=spec["root_seed"])
    vid = reg.register(version)
    BatchRunner(reg).run(vid)
    return reg, vid


# ---------------------------------------------------------------------------
# Reproduce verification
# ---------------------------------------------------------------------------
def test_reproduce_succeeds_and_verifies_hashes(tmp_path):
    reg, vid = _registered_and_run(tmp_path)
    result = reproduce(reg, vid, sample=2)
    assert result["success"]
    assert result["runs_verified"] == 2 and result["runs_failed"] == 0
    assert result["total_rows"] == 6
    assert all(c["ok"] for c in result["checks"])


def test_reproduce_by_hash_and_prefix(tmp_path):
    reg, vid = _registered_and_run(tmp_path)
    rh = reg.get(vid)["research_hash"]
    assert resolve_reference(reg, rh) == vid
    assert resolve_reference(reg, rh[:10]) == vid
    assert reproduce(reg, rh[:10], sample=1)["success"]
    with pytest.raises(ReproductionError, match="no registered experiment"):
        resolve_reference(reg, "deadbeef00")


def test_reproduce_detects_tampered_result_row(tmp_path):
    reg, vid = _registered_and_run(tmp_path)
    bid = batch_id_for(reg.load(vid))
    row = reg.load_result_row(bid, "c000_r0000")
    row["metrics"]["max_drawdown"] = 0.999  # falsified result
    reg.save_result_row(bid, "c000_r0000", row)

    result = reproduce(reg, vid, sample=None)  # full verification
    assert not result["success"]
    assert result["runs_failed"] == 1 and result["runs_verified"] == 5
    bad = [c for c in result["checks"] if not c["ok"]]
    assert bad and bad[0]["check"] == "c000_r0000: metrics"


def test_reproduce_detects_tampered_version_record(tmp_path):
    reg, vid = _registered_and_run(tmp_path)
    path = reg.root / "versions" / f"{vid}.json"
    record = json.loads(path.read_text())
    record["root_seed"] = 7  # silent edit of the research object
    path.write_text(json.dumps(record))
    result = reproduce(reg, vid)
    assert not result["success"]
    failed = [c for c in result["checks"] if not c["ok"]]
    assert failed[0]["check"] == "research hash verified"


# ---------------------------------------------------------------------------
# CLI end-to-end (in-process)
# ---------------------------------------------------------------------------
def test_cli_full_researcher_journey(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec_dict()))
    data = str(tmp_path / "data")

    assert cli_main(["--data-dir", data, "run", str(spec_file)]) == 0
    out = capsys.readouterr().out
    assert "experiment registered" in out and "batch completed: 6/6" in out
    vid = next(line.split()[-1] for line in out.splitlines()
               if line.strip().startswith("version:"))

    assert cli_main(["--data-dir", data, "list"]) == 0
    assert vid in capsys.readouterr().out

    assert cli_main(["--data-dir", data, "analyze", vid]) == 0
    out = capsys.readouterr().out
    assert "analysis ana_" in out and "low vs high" in out

    report_file = tmp_path / "report.md"
    assert cli_main(["--data-dir", data, "report", vid,
                     "-o", str(report_file)]) == 0
    capsys.readouterr()
    assert "## Reproducibility" in report_file.read_text()

    assert cli_main(["--data-dir", data, "reproduce", vid]) == 0
    out = capsys.readouterr().out
    assert "REPRODUCTION SUCCESSFUL" in out


def test_cli_run_is_resumable_with_budget(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec_dict()))
    data = str(tmp_path / "data")

    assert cli_main(["--data-dir", data, "run", str(spec_file),
                     "--max-runs", "4"]) == 0
    assert "pending: 2 runs" in capsys.readouterr().out
    assert cli_main(["--data-dir", data, "run", str(spec_file)]) == 0
    out = capsys.readouterr().out
    assert "batch completed: 6/6" in out and "(2 executed this call)" in out


def test_cli_validate_only_and_bad_spec(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec_dict()))
    data = str(tmp_path / "data")
    assert cli_main(["--data-dir", data, "run", str(spec_file),
                     "--validate-only"]) == 0
    assert "nothing executed" in capsys.readouterr().out

    bad = _spec_dict()
    bad["design"]["replications"] = 1  # one seed is not evidence
    spec_file.write_text(json.dumps(bad))
    assert cli_main(["--data-dir", data, "run", str(spec_file)]) == 1
    assert "invalid experiment spec" in capsys.readouterr().err

    assert cli_main(["--data-dir", data, "run", "missing.json"]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_analyze_refuses_partial(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec_dict()))
    data = str(tmp_path / "data")
    cli_main(["--data-dir", data, "run", str(spec_file), "--max-runs", "3"])
    capsys.readouterr()
    vid = Registry(data).list()[0]["version_id"]
    assert cli_main(["--data-dir", data, "analyze", vid]) == 1
    assert "incomplete" in capsys.readouterr().err
    assert cli_main(["--data-dir", data, "analyze", vid,
                     "--allow-partial"]) == 0


def test_cli_reproduce_fails_on_tamper(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec_dict()))
    data = str(tmp_path / "data")
    cli_main(["--data-dir", data, "run", str(spec_file)])
    capsys.readouterr()
    reg = Registry(data)
    vid = reg.list()[0]["version_id"]
    bid = batch_id_for(reg.load(vid))
    row = reg.load_result_row(bid, "c001_r0001")
    row["state_hash"] = "0" * 64
    reg.save_result_row(bid, "c001_r0001", row)
    assert cli_main(["--data-dir", data, "reproduce", vid, "--full"]) == 1
    assert "REPRODUCTION FAILED" in capsys.readouterr().err
