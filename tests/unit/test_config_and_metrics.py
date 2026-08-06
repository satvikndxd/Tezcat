import math

import pytest
from pydantic import ValidationError

from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MetricsPolicy,
    ShockConfig, ShockTrigger, ShockType, config_hash,
)
from tezcat.metrics.engine import MetricsEngine


def _minimal_config(**kw):
    base = dict(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=5)],
        total_steps=100,
    )
    base.update(kw)
    return ExperimentConfig(**base)


def test_config_roundtrip_and_hash_stability():
    cfg = _minimal_config()
    payload = cfg.model_dump(mode="json")
    cfg2 = ExperimentConfig.model_validate(payload)
    assert config_hash(cfg) == config_hash(cfg2)


def test_hash_changes_when_config_changes():
    a = _minimal_config()
    b = _minimal_config(total_steps=200)
    assert config_hash(a) != config_hash(b)


def test_empty_population_rejected():
    with pytest.raises(ValidationError):
        _minimal_config(agents=[])


def test_shock_beyond_total_steps_rejected():
    with pytest.raises(ValidationError):
        _minimal_config(shocks=[ShockConfig(
            shock_id="late", shock_type=ShockType.WHALE_ORDER,
            trigger=ShockTrigger(kind="scheduled", step=500), magnitude=100)])


def test_duplicate_shock_id_rejected():
    shock = dict(shock_type=ShockType.SENTIMENT_SHOCK,
                 trigger=ShockTrigger(kind="scheduled", step=10), magnitude=0.5)
    with pytest.raises(ValidationError):
        _minimal_config(shocks=[ShockConfig(shock_id="x", **shock),
                                ShockConfig(shock_id="x", **shock)])


def test_scheduled_trigger_requires_step():
    with pytest.raises(ValidationError):
        ShockTrigger(kind="scheduled")


# ---------------------------------------------------------------------------
def test_metrics_log_return_and_volatility():
    m = MetricsEngine("r", MetricsPolicy(vol_window=3), initial_price=100.0)
    m.on_step(1, 101.0, 0.1, 50, 50, 5, 1, "stable")
    row = m.step_metrics[-1]
    assert abs(row["log_return"] - math.log(101 / 100)) < 1e-4
    m.on_step(2, 100.0, 0.1, 50, 50, 5, 1, "stable")
    m.on_step(3, 102.0, 0.1, 50, 50, 5, 1, "stable")
    assert m.rolling_volatility > 0


def test_metrics_drawdown():
    m = MetricsEngine("r", MetricsPolicy(), initial_price=100.0)
    m.on_step(1, 110.0, 0.1, 50, 50, 0, 0, "stable")
    m.on_step(2, 99.0, 0.1, 50, 50, 0, 0, "stable")
    report = m.build_report("r", [], 99.0, 0, [], {"stable": 2})
    assert abs(report["max_drawdown"] - (110 - 99) / 110) < 1e-9


def test_report_totals():
    m = MetricsEngine("r", MetricsPolicy(), initial_price=100.0)
    m.on_step(1, 100.0, 0.2, 50, 50, 7, 2, "stable")
    m.on_step(2, 100.0, 0.4, 50, 50, 3, 1, "stable")
    report = m.build_report("r", [], 100.0, 1, [], {"stable": 2})
    assert report["total_volume"] == 10
    assert report["total_trades"] == 3
    assert abs(report["average_spread"] - 0.3) < 1e-9
    assert report["total_return"] == 0.0
