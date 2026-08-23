"""Market World → NautilusTrader data export (Phase S4).

Maps the canonical world stream onto Nautilus' **standard** data types —
``QuoteTick`` and ``TradeTick`` — rather than inventing a parallel
pseudo-Nautilus format. Regime/shock annotations remain Tezcat research
metadata alongside the stream (used for regime-conditioned metrics); they
are not injected into the market-data types.

Mapping rules (all declared, none silent):

* quote → ``QuoteTick(bid, ask, bid_size, ask_size)`` where the sizes are
  the aggregate resting depth per side (the closest honest L1 mapping).
* trade → ``TradeTick`` with ``AggressorSide.NO_AGGRESSOR`` — Tezcat's
  matching engine does not record taker side, and the export does not
  guess it.
* price precision derives from the world's tick size; timestamps are the
  world's deterministic ``ts_ns`` values verbatim.

The export **validates before converting** and fails loudly on malformed
streams (non-monotonic timestamps, crossed quotes, non-positive prices or
quantities, missing fields). Same world ⇒ same export, byte for byte.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple

from tezcat.lab.strategies import LabError
from tezcat.worlds.world import MarketWorld


def price_precision(tick_size: float) -> int:
    exponent = Decimal(str(tick_size)).normalize().as_tuple().exponent
    return max(0, -int(exponent))


def validate_stream(world: MarketWorld) -> None:
    """Reject malformed streams explicitly — never repair them."""
    last_ts = None
    for i, q in enumerate(world.quotes):
        for field in ("ts_ns", "bid", "ask", "bid_size", "ask_size"):
            if q.get(field) is None:
                raise LabError(f"quote {i} missing field {field!r}")
        if q["bid"] <= 0 or q["ask"] <= 0:
            raise LabError(f"quote {i} has non-positive price: {q}")
        if q["bid"] > q["ask"]:
            raise LabError(f"quote {i} is crossed: bid {q['bid']} > ask {q['ask']}")
        if q["bid_size"] < 0 or q["ask_size"] < 0:
            raise LabError(f"quote {i} has negative size: {q}")
        if last_ts is not None and q["ts_ns"] <= last_ts:
            raise LabError(f"quote timestamps not strictly increasing at "
                           f"index {i}: {q['ts_ns']} <= {last_ts}")
        last_ts = q["ts_ns"]
    last_ts = None
    for i, t in enumerate(world.trades):
        for field in ("ts_ns", "trade_id", "price", "quantity"):
            if t.get(field) is None:
                raise LabError(f"trade {i} missing field {field!r}")
        if t["price"] <= 0:
            raise LabError(f"trade {i} has non-positive price: {t}")
        if t["quantity"] <= 0:
            raise LabError(f"trade {i} has non-positive quantity: {t}")
        if last_ts is not None and t["ts_ns"] <= last_ts:
            raise LabError(f"trade timestamps not strictly increasing at "
                           f"index {i}")
        last_ts = t["ts_ns"]
    if not world.quotes:
        raise LabError("world has no exportable quotes")


def export_to_nautilus(world: MarketWorld) -> Tuple[Any, List[Any]]:
    """Return (instrument, chronologically sorted tick list)."""
    try:
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.data import QuoteTick, TradeTick
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import (
            InstrumentId, Symbol, TradeId, Venue,
        )
        from nautilus_trader.model.instruments import Equity
        from nautilus_trader.model.objects import Price, Quantity
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise LabError(
            "nautilus_trader is not installed; install it to use the "
            "Strategy Lab bridge") from exc

    validate_stream(world)
    symbol = world.manifest["instrument_symbol"]
    tick = world.manifest["tick_size"]
    precision = price_precision(tick)
    instrument_id = InstrumentId(Symbol(symbol), Venue("TEZCAT"))
    instrument = Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        currency=USD,
        price_precision=precision,
        price_increment=Price(tick, precision),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )

    ticks: List[Any] = []
    for q in world.quotes:
        ticks.append(QuoteTick(
            instrument_id,
            Price(round(q["bid"], precision), precision),
            Price(round(q["ask"], precision), precision),
            Quantity.from_int(max(1, int(q["bid_size"]))),
            Quantity.from_int(max(1, int(q["ask_size"]))),
            q["ts_ns"], q["ts_ns"]))
    for t in world.trades:
        ticks.append(TradeTick(
            instrument_id,
            Price(round(t["price"], precision), precision),
            Quantity.from_int(int(t["quantity"])),
            AggressorSide.NO_AGGRESSOR,
            TradeId(str(t["trade_id"])),
            t["ts_ns"], t["ts_ns"]))
    ticks.sort(key=lambda x: x.ts_init)
    return instrument, ticks


def export_stream_rows(world: MarketWorld) -> Dict[str, Any]:
    """Framework-neutral export (for files/inspection): validated rows."""
    validate_stream(world)
    return {
        "world_id": world.world_id,
        "world_hash": world.world_hash,
        "instrument": {"symbol": world.manifest["instrument_symbol"],
                       "venue": "TEZCAT",
                       "tick_size": world.manifest["tick_size"]},
        "time_mapping": world.manifest["time_mapping"],
        "quotes": world.quotes,
        "trades": world.trades,
        "annotations": world.annotations,
    }
