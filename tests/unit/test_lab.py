"""Strategy Lab bridge (S4): export mapping, faults, strategies, metrics."""

import pytest

pytest.importorskip("nautilus_trader",
                    reason="Strategy Lab is an optional bridge; install "
                           "the 'lab' extra to test it")

from tezcat.experiments.registry import Registry
from tezcat.lab.backtest import derive_metrics, run_backtest
from tezcat.lab.nautilus_export import (
    export_stream_rows, export_to_nautilus, price_precision, validate_stream,
)
from tezcat.lab.strategies import (
    LabError, list_strategies, resolve_params, strategy_hash,
)
from tezcat.worlds import build_world
from tezcat.worlds.world import MarketWorld

from tests.unit.test_worlds import small_version


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    registry = Registry(str(tmp_path_factory.mktemp("lab")))
    registry.register(small_version(experiment_id="exp_lab"))
    return build_world(registry, registry.list()[0]["version_id"],
                       cell="control", replication=0)


def broken_world(world, *, quotes=None, trades=None) -> MarketWorld:
    return MarketWorld(world.manifest,
                       quotes if quotes is not None else world.quotes,
                       trades if trades is not None else world.trades,
                       world.annotations)


class TestExportMapping:
    def test_precision_from_tick(self):
        assert price_precision(0.05) == 2
        assert price_precision(0.001) == 3
        assert price_precision(1.0) == 0

    def test_ticks_match_world_rows(self, world):
        instrument, ticks = export_to_nautilus(world)
        assert str(instrument.id) == "TZC.TEZCAT"
        assert float(instrument.price_increment) == pytest.approx(0.05)
        assert len(ticks) == len(world.quotes) + len(world.trades)
        ts = [t.ts_init for t in ticks]
        assert ts == sorted(ts)
        from nautilus_trader.model.data import QuoteTick, TradeTick
        from nautilus_trader.model.enums import AggressorSide
        quotes = [t for t in ticks if isinstance(t, QuoteTick)]
        trades = [t for t in ticks if isinstance(t, TradeTick)]
        assert float(quotes[0].bid_price) == pytest.approx(world.quotes[0]["bid"])
        assert float(quotes[0].ask_price) == pytest.approx(world.quotes[0]["ask"])
        assert quotes[0].ts_event == world.quotes[0]["ts_ns"]
        assert float(trades[0].price) == pytest.approx(world.trades[0]["price"])
        assert all(t.aggressor_side == AggressorSide.NO_AGGRESSOR
                   for t in trades)

    def test_export_deterministic(self, world):
        rows_a = export_stream_rows(world)
        rows_b = export_stream_rows(world)
        assert rows_a == rows_b
        assert rows_a["world_hash"] == world.world_hash


class TestExportFaults:
    def test_crossed_quote_rejected(self, world):
        bad = dict(world.quotes[0], bid=world.quotes[0]["ask"] + 1)
        with pytest.raises(LabError, match="crossed"):
            validate_stream(broken_world(world, quotes=[bad]))

    def test_duplicate_timestamp_rejected(self, world):
        with pytest.raises(LabError, match="strictly increasing"):
            validate_stream(broken_world(
                world, quotes=[world.quotes[0], world.quotes[0]]))

    def test_missing_field_rejected(self, world):
        bad = {k: v for k, v in world.quotes[0].items() if k != "ask"}
        with pytest.raises(LabError, match="missing field"):
            validate_stream(broken_world(world, quotes=[bad]))

    def test_invalid_quantity_rejected(self, world):
        bad = dict(world.trades[0], quantity=0)
        with pytest.raises(LabError, match="non-positive quantity"):
            validate_stream(broken_world(world, trades=[bad]))

    def test_empty_world_rejected(self, world):
        with pytest.raises(LabError, match="no exportable quotes"):
            validate_stream(broken_world(world, quotes=[]))


class TestStrategyLibrary:
    def test_catalog(self):
        rows = list_strategies()
        assert {r["strategy_id"] for r in rows} == \
               {"buy_hold", "ema_cross", "mean_reversion"}

    def test_param_resolution_strict(self):
        assert resolve_params("ema_cross", {"fast": 5})["fast"] == 5
        with pytest.raises(LabError, match="no parameter"):
            resolve_params("ema_cross", {"speed": 5})
        with pytest.raises(LabError, match="unknown strategy"):
            resolve_params("hodl", {})

    def test_hash_covers_params(self):
        a = strategy_hash("ema_cross", resolve_params("ema_cross"))
        b = strategy_hash("ema_cross", resolve_params("ema_cross", {"fast": 5}))
        assert a != b and len(a) == 64


class TestBacktestMetrics:
    @pytest.fixture(scope="class")
    def result(self, world):
        return run_backtest(world, "buy_hold", {"trade_size": 50},
                            starting_cash=100_000)

    def test_provenance_chain(self, result, world):
        assert result["world"]["world_hash"] == world.world_hash
        assert result["world"]["research_hash"] == \
            world.manifest["research_hash"]
        assert result["strategy"]["strategy_hash"] == strategy_hash(
            "buy_hold", resolve_params("buy_hold", {"trade_size": 50}))
        assert result["environment"]["nautilus_trader_version"]

    def test_buy_hold_fills_and_metrics(self, result):
        m = result["metrics"]
        assert m["n_orders_submitted"] == 2      # entry + close
        assert m["n_fills"] >= 2
        assert m["fill_rate"] == 1.0
        assert m["turnover"] > 0
        assert m["max_drawdown"] >= 0
        assert m["mean_slippage_vs_mid"] is not None
        assert m["final_equity"] == pytest.approx(
            100_000 + m["total_pnl"], abs=1e-6)

    def test_regime_breakdown_partitions_pnl(self, result):
        m = result["metrics"]
        assert sum(v["pnl"] for v in m["regime_breakdown"].values()) == \
            pytest.approx(m["total_pnl"], abs=1e-4)
        assert sum(v["n_fills"] for v in m["regime_breakdown"].values()) == \
            m["n_fills"]

    def test_backtest_deterministic(self, world, result):
        again = run_backtest(world, "buy_hold", {"trade_size": 50},
                             starting_cash=100_000)
        assert again["metrics"] == result["metrics"]

    def test_slippage_sign_convention(self, world):
        # buys fill at/above mid with L1 quotes → non-negative slippage
        fills = [{"ts_ns": world.quotes[10]["ts_ns"], "side": "BUY",
                  "quantity": 10.0,
                  "price": world.quotes[10]["ask"]}]
        m = derive_metrics(world, fills, [{"side": "BUY", "quantity": 10}],
                           100_000)
        expected = (world.quotes[10]["ask"] - world.quotes[10]["bid"]) / 2
        assert m["mean_slippage_vs_mid"] == pytest.approx(expected)
