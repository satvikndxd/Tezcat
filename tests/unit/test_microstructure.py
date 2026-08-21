"""Phase F7: microstructure and TCA metrics.

Two layers of tests:
1. Hand-calculated fixtures on point metrics and on scripted order
   sequences driven directly through the matching engine.
2. End-to-end consistency: the analyzer's embedded replay kernel must land
   on the live engine's exact state hash (lineage proof).
"""

import pytest

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import (
    AgentGroupConfig, AgentType, ExperimentConfig, MarketConfig,
)
from tezcat.engine.ecology import EcologyEngine
from tezcat.events.log import EventLog
from tezcat.market.matching import MatchingEngine
from tezcat.market.order_book import Order, OrderBook
from tezcat.microstructure import (
    MicrostructureAnalyzer, microprice, queue_imbalance, relative_spread,
)


# ---------------------------------------------------------------------------
# Point metrics: hand-calculated fixtures and edge cases
# ---------------------------------------------------------------------------
def test_microprice_weights_toward_thin_side():
    # bid 99 x10, ask 101 x30 -> (30*99 + 10*101)/40 = 99.5 (leans to bid).
    assert microprice(99.0, 10, 101.0, 30) == pytest.approx(99.5)
    assert microprice(99.0, 10, 101.0, 10) == pytest.approx(100.0)


def test_point_metrics_edge_cases():
    assert microprice(None, 0, 101.0, 5) is None      # one-sided
    assert microprice(99.0, 0, 101.0, 0) is None       # zero depth
    assert queue_imbalance(0, 0) is None
    assert queue_imbalance(30, 10) == pytest.approx(0.5)
    assert queue_imbalance(10, 30) == pytest.approx(-0.5)
    assert relative_spread(None, 101.0) is None
    assert relative_spread(99.0, 101.0) == pytest.approx(2.0 / 100.0)


# ---------------------------------------------------------------------------
# Scripted-market fixtures through the real matching engine
# ---------------------------------------------------------------------------
def _scripted_config():
    """Config whose group layout yields agent ids used in the scripts."""
    return ExperimentConfig(
        agents=[AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=3,
                                 cash=1_000_000, inventory=10_000)],
        total_steps=10,
    )


def _scripted_market():
    cfg = _scripted_config()
    book = OrderBook(cfg.market.tick_size)
    portfolios = {f"noise_trader_0_{i}": Portfolio(cash=1_000_000, inventory=10_000)
                  for i in range(3)}
    events = EventLog("run_ms")
    me = MatchingEngine(book, portfolios, cfg.market, events=events)
    return cfg, book, me, events


def _submit(me, oid, agent, side, otype, price, qty, step):
    o = Order(order_id=oid, agent_id=agent, side=side, order_type=otype,
              price=price, quantity=qty, remaining=qty)
    return me.submit(o, step)


def _end_step(events, book, step):
    events.append(step, "step_ended", {
        "last_price": book.mid_price() or 100.0, "sentiment": 0.0,
        "mm_withdrawn": False, "regime": "stable"})


def test_effective_spread_and_signed_volume_hand_calc():
    cfg, book, me, events = _scripted_market()
    a, b, c = "noise_trader_0_0", "noise_trader_0_1", "noise_trader_0_2"
    # Build a 99.0 / 101.0 book -> mid 100.0.
    _submit(me, "b1", a, "buy", "limit", 99.0, 10, 1)
    _submit(me, "s1", b, "sell", "limit", 101.0, 10, 1)
    # Aggressive buy hits the ask at 101: effective spread = 2*(101-100) = 2.
    _submit(me, "m1", c, "buy", "market", None, 4, 1)
    _end_step(events, book, 1)

    out = MicrostructureAnalyzer(_scripted_config(), "run_ms").run(events.events)
    assert out["trades"]["n"] == 1
    assert out["trades"]["mean_effective_spread"] == pytest.approx(2.0)
    assert out["per_step"][0]["signed_volume"] == 4  # buy aggressor: +4
    assert out["per_trade"][0]["sign"] == 1


def test_sell_aggressor_signed_volume_negative():
    cfg, book, me, events = _scripted_market()
    a, b, c = "noise_trader_0_0", "noise_trader_0_1", "noise_trader_0_2"
    _submit(me, "b1", a, "buy", "limit", 99.0, 10, 1)
    _submit(me, "s1", b, "sell", "limit", 101.0, 10, 1)
    _submit(me, "m1", c, "sell", "market", None, 6, 1)  # hits bid at 99
    _end_step(events, book, 1)
    out = MicrostructureAnalyzer(_scripted_config(), "run_ms").run(events.events)
    # Effective spread: 2 * (-1) * (99 - 100) = 2; signed volume -6.
    assert out["trades"]["mean_effective_spread"] == pytest.approx(2.0)
    assert out["per_step"][0]["signed_volume"] == -6


def test_realized_spread_and_impact_with_scripted_future_mid():
    cfg, book, me, events = _scripted_market()
    a, b, c = "noise_trader_0_0", "noise_trader_0_1", "noise_trader_0_2"
    _submit(me, "b1", a, "buy", "limit", 99.0, 20, 1)
    _submit(me, "s1", b, "sell", "limit", 101.0, 20, 1)
    _submit(me, "m1", c, "buy", "market", None, 5, 1)   # trade at 101, mid 100
    _end_step(events, book, 1)
    # Shift the mid up to 101 by improving the bid; steps 2..3 idle.
    _submit(me, "b2", a, "buy", "limit", 100.95, 10, 2)
    _end_step(events, book, 2)
    _end_step(events, book, 3)

    out = MicrostructureAnalyzer(_scripted_config(), "run_ms", horizon=2).run(events.events)
    t = out["per_trade"][0]
    mid_h = (100.95 + 101.0) / 2  # end-of-step-3 mid
    assert t["realized_spread"] == pytest.approx(2 * (101.0 - mid_h))
    assert t["price_impact"] == pytest.approx(mid_h - 100.0)
    # Decomposition: effective = realized + 2 * impact.
    assert t["effective_spread"] == pytest.approx(
        t["realized_spread"] + 2 * t["price_impact"])


def test_queue_position_and_fill_stats():
    cfg, book, me, events = _scripted_market()
    a, b, c = "noise_trader_0_0", "noise_trader_0_1", "noise_trader_0_2"
    _submit(me, "s1", a, "sell", "limit", 101.0, 10, 1)   # first in queue
    _submit(me, "s2", b, "sell", "limit", 101.0, 7, 1)    # 10 shares ahead
    _submit(me, "m1", c, "buy", "market", None, 12, 1)    # fills s1, partial s2
    book_cancel = book.cancel("s2", 2)                     # cancel remainder
    me.release_on_cancel(book_cancel)
    events.append(2, "order_cancelled", {
        "order_id": "s2", "side": "sell", "price": 101.0,
        "remaining": book_cancel.remaining, "reason": "agent"})
    _end_step(events, book, 2)

    out = MicrostructureAnalyzer(_scripted_config(), "run_ms").run(events.events)
    fills = out["fills"]
    assert fills["n_limit_orders"] == 2
    assert fills["fill_probability"] == pytest.approx(0.5)  # s1 full, s2 partial
    # s1 fill fraction 1.0; s2 filled 2/7.
    assert fills["mean_fill_fraction"] == pytest.approx((1.0 + 2 / 7) / 2)
    assert fills["mean_queue_ahead_at_rest"] == pytest.approx((0 + 10) / 2)


def test_tca_slippage_and_shortfall_hand_calc():
    cfg, book, me, events = _scripted_market()
    a, b, c = "noise_trader_0_0", "noise_trader_0_1", "noise_trader_0_2"
    _submit(me, "b1", a, "buy", "limit", 99.0, 50, 1)
    _submit(me, "s1", b, "sell", "limit", 101.0, 3, 1)
    # Market buy for 10: fills 3 @ 101 (arrival mid 100), 7 discarded.
    _submit(me, "m1", c, "buy", "market", None, 10, 1)
    _end_step(events, book, 1)  # final mid: book now 99/– -> one-sided? s1 gone

    out = MicrostructureAnalyzer(_scripted_config(), "run_ms").run(events.events)
    row = next(o for o in out["per_order_tca"] if o["order_id"] == "m1")
    assert row["slippage_per_share"] == pytest.approx(1.0)  # paid 101 vs mid 100
    assert row["execution_cost"] == pytest.approx(3.0)
    assert row["fees"] == 0.0
    # Final mid undefined (one-sided book) -> opportunity cost 0, counted.
    assert row["opportunity_cost"] == pytest.approx(0.0)
    assert row["implementation_shortfall"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# End-to-end: lineage to source events on a real run
# ---------------------------------------------------------------------------
def _real_config():
    return ExperimentConfig(
        agents=[
            AgentGroupConfig(agent_type=AgentType.NOISE_TRADER, count=8),
            AgentGroupConfig(agent_type=AgentType.RETAIL_TRADER, count=6),
            AgentGroupConfig(agent_type=AgentType.MARKET_MAKER, count=2),
        ],
        total_steps=150,
    )


def test_analyzer_replay_matches_live_engine_state():
    cfg = _real_config()
    eng = EcologyEngine("run_ms", cfg, 42)
    while not eng.done:
        eng.step()
    out = MicrostructureAnalyzer(cfg, "run_ms").run(eng.events_log.events)
    # Lineage proof: the analyzer's replayed book is the engine's exact book.
    assert out["replay_state_hash"] == eng.state_hash()
    assert out["trades"]["n"] == len(eng.trades)
    assert len(out["per_step"]) == 150


def test_qi_association_is_computable_and_bounded():
    cfg = _real_config()
    eng = EcologyEngine("run_ms", cfg, 42)
    while not eng.done:
        eng.step()
    out = MicrostructureAnalyzer(cfg, "run_ms").run(eng.events_log.events)
    qa = out["qi_association"]
    assert qa["n"] > 50
    assert qa["correlation"] is not None
    assert -1.0 <= qa["correlation"] <= 1.0  # association only, no sign claim


def test_tca_by_agent_type_partitions_all_orders():
    cfg = _real_config()
    eng = EcologyEngine("run_ms", cfg, 42)
    while not eng.done:
        eng.step()
    out = MicrostructureAnalyzer(cfg, "run_ms").run(eng.events_log.events)
    by_type = out["tca"]["by_agent_type"]
    assert sum(t["orders"] for t in by_type.values()) == out["tca"]["n_orders"]
    assert set(by_type) <= {"noise_trader", "retail_trader", "market_maker", "whale"}
    # Shortfall decomposition holds in aggregate.
    assert out["tca"]["total_implementation_shortfall"] == pytest.approx(
        out["tca"]["total_execution_cost"] + out["tca"]["total_opportunity_cost"])
