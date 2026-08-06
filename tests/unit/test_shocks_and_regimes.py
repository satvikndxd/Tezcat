from tezcat.core.config import (
    Regime, RegimePolicy, ShockConfig, ShockTrigger, ShockType,
)
from tezcat.regimes.engine import RegimeEngine
from tezcat.shocks.engine import EnvironmentState, ShockEngine


def _shock(step=5, stype=ShockType.SENTIMENT_SHOCK, **kw):
    return ShockConfig(shock_id=f"s{step}", shock_type=stype,
                       trigger=ShockTrigger(kind="scheduled", step=step),
                       magnitude=kw.pop("magnitude", 0.8), **kw)


def test_scheduled_shock_fires_at_step():
    env = EnvironmentState()
    se = ShockEngine("r", [_shock(step=5)], env)
    assert se.due_shocks(4) == []
    due = se.due_shocks(5)
    assert len(due) == 1 and due[0][1] == "scheduled"


def test_manual_shock_queued():
    env = EnvironmentState()
    se = ShockEngine("r", [], env)
    se.inject_manual("sentiment_shock", "sell", 0.9, 30)
    due = se.due_shocks(17)
    assert len(due) == 1 and due[0][1] == "manual"
    assert se.due_shocks(18) == []  # consumed


def test_sentiment_shock_applies_and_decays():
    env = EnvironmentState()
    se = ShockEngine("r", [], env)
    cfg = _shock(step=1, side="sell")
    se.apply_env_shock(cfg, 1)
    assert env.sentiment == -0.8
    for step in range(2, 400):
        env.tick(step)
    assert env.sentiment == 0.0


def test_mm_withdrawal_expires():
    env = EnvironmentState()
    se = ShockEngine("r", [], env)
    cfg = _shock(step=1, stype=ShockType.MM_WITHDRAWAL, magnitude=1.0, duration=10)
    se.apply_env_shock(cfg, 1)
    assert env.mm_withdrawn
    env.tick(11)
    assert env.mm_withdrawn
    env.tick(12)
    assert not env.mm_withdrawn


def test_whale_program_slices():
    env = EnvironmentState()
    se = ShockEngine("r", [], env)
    cfg = _shock(step=1, stype=ShockType.WHALE_ORDER, magnitude=100, duration=4, side="sell")
    se.start_whale(cfg, 1)
    sl = se.whale_slices(1)
    assert sl[0]["quantity"] == 25
    assert se.whale_slices(1) == []  # no double execution in one step
    sl[0]["program"]["remaining"] -= 25
    assert se.whale_slices(2)[0]["quantity"] == 25


# ---------------------------------------------------------------------------
def _feed(re_, steps, price_fn, spread=0.1, depth=1000, vol=0.001):
    for s in steps:
        re_.observe(s, price_fn(s), spread, depth, vol)


def test_crisis_detection_on_drawdown():
    re_ = RegimeEngine("r", RegimePolicy())
    _feed(re_, range(1, 60), lambda s: 100.0)
    assert re_.current == Regime.STABLE
    # sharp 12% decline
    _feed(re_, range(60, 120), lambda s: 100.0 - min(12.0, (s - 60) * 0.4))
    assert re_.current == Regime.CRISIS
    assert any(e.new_regime == "crisis" for e in re_.events)


def test_recovery_after_stabilization():
    re_ = RegimeEngine("r", RegimePolicy())
    _feed(re_, range(1, 60), lambda s: 100.0)
    _feed(re_, range(60, 120), lambda s: 100.0 - min(12.0, (s - 60) * 0.4), vol=0.01)
    assert re_.current == Regime.CRISIS
    # price stabilizes, volatility drops
    _feed(re_, range(120, 260), lambda s: 88.0 + (s - 120) * 0.02, vol=0.0005)
    assert any(e.new_regime == "recovery" for e in re_.events)


def test_no_flapping_in_calm_market():
    re_ = RegimeEngine("r", RegimePolicy())
    _feed(re_, range(1, 500), lambda s: 100.0 + 0.2 * (s % 3))
    assert re_.events == []
