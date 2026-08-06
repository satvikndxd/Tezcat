"""Central limit order book with price-time priority.

Price levels are keyed by integer tick counts to avoid floating point issues.
Each level holds a FIFO deque of resting orders (time priority).
"""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


@dataclass
class Order:
    order_id: str
    agent_id: str
    side: str  # "buy" | "sell"
    order_type: str  # "limit" | "market"
    price: Optional[float]  # None for market orders
    quantity: int
    remaining: int
    status: str = "new"  # new | partial | filled | cancelled | rejected | expired
    created_step: int = 0
    updated_step: int = 0
    seq: int = 0  # global arrival sequence for time priority


@dataclass
class BookSide:
    """One side of the book. `is_bid` controls sort direction for best price."""

    is_bid: bool
    levels: Dict[int, Deque[Order]] = field(default_factory=dict)
    sorted_ticks: List[int] = field(default_factory=list)  # ascending

    def add(self, tick: int, order: Order) -> None:
        q = self.levels.get(tick)
        if q is None:
            q = deque()
            self.levels[tick] = q
            bisect.insort(self.sorted_ticks, tick)
        q.append(order)

    def best_tick(self) -> Optional[int]:
        if not self.sorted_ticks:
            return None
        return self.sorted_ticks[-1] if self.is_bid else self.sorted_ticks[0]

    def peek_best_order(self) -> Optional[Order]:
        tick = self.best_tick()
        if tick is None:
            return None
        return self.levels[tick][0]

    def pop_best_if_empty(self) -> None:
        tick = self.best_tick()
        if tick is not None and not self.levels[tick]:
            del self.levels[tick]
            idx = len(self.sorted_ticks) - 1 if self.is_bid else 0
            self.sorted_ticks.pop(idx)

    def remove_order(self, order: Order, tick: int) -> bool:
        q = self.levels.get(tick)
        if q is None:
            return False
        try:
            q.remove(order)
        except ValueError:
            return False
        if not q:
            del self.levels[tick]
            i = bisect.bisect_left(self.sorted_ticks, tick)
            if i < len(self.sorted_ticks) and self.sorted_ticks[i] == tick:
                self.sorted_ticks.pop(i)
        return True

    def depth(self, n_levels: Optional[int] = None) -> int:
        ticks = self.sorted_ticks if not self.is_bid else list(reversed(self.sorted_ticks))
        if n_levels is not None:
            ticks = ticks[:n_levels]
        return sum(o.remaining for t in ticks for o in self.levels[t])

    def level_view(self, n_levels: int) -> List[Tuple[int, int]]:
        """Best-first list of (tick, total remaining quantity)."""
        ticks = list(reversed(self.sorted_ticks)) if self.is_bid else list(self.sorted_ticks)
        out = []
        for t in ticks[:n_levels]:
            out.append((t, sum(o.remaining for o in self.levels[t])))
        return out

    def all_orders(self) -> List[Order]:
        return [o for t in self.sorted_ticks for o in self.levels[t]]


class OrderBook:
    def __init__(self, tick_size: float):
        self.tick_size = tick_size
        self.bids = BookSide(is_bid=True)
        self.asks = BookSide(is_bid=False)
        self._orders: Dict[str, Tuple[Order, int]] = {}  # order_id -> (order, tick)
        self._seq = 0

    # -- price helpers -------------------------------------------------
    def to_tick(self, price: float) -> int:
        return int(round(price / self.tick_size))

    def to_price(self, tick: int) -> float:
        return round(tick * self.tick_size, 10)

    def snap(self, price: float) -> float:
        return self.to_price(self.to_tick(price))

    # -- book operations ----------------------------------------------
    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add_limit(self, order: Order) -> None:
        assert order.order_type == "limit" and order.price is not None
        tick = self.to_tick(order.price)
        order.price = self.to_price(tick)
        side = self.bids if order.side == "buy" else self.asks
        side.add(tick, order)
        self._orders[order.order_id] = (order, tick)

    def cancel(self, order_id: str, step: int, status: str = "cancelled") -> Optional[Order]:
        entry = self._orders.pop(order_id, None)
        if entry is None:
            return None
        order, tick = entry
        side = self.bids if order.side == "buy" else self.asks
        if side.remove_order(order, tick):
            order.status = status
            order.updated_step = step
            return order
        return None

    def remove_filled(self, order: Order) -> None:
        self._orders.pop(order.order_id, None)

    def get_order(self, order_id: str) -> Optional[Order]:
        entry = self._orders.get(order_id)
        return entry[0] if entry else None

    def orders_by_agent(self, agent_id: str) -> List[Order]:
        return [o for o, _ in self._orders.values() if o.agent_id == agent_id]

    def expired_orders(self, current_step: int, max_age: int) -> List[Order]:
        return [o for o, _ in self._orders.values() if current_step - o.created_step >= max_age]

    # -- market state --------------------------------------------------
    @property
    def best_bid(self) -> Optional[float]:
        t = self.bids.best_tick()
        return self.to_price(t) if t is not None else None

    @property
    def best_ask(self) -> Optional[float]:
        t = self.asks.best_tick()
        return self.to_price(t) if t is not None else None

    @property
    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return round(ba - bb, 10)

    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2

    def bid_depth(self, n_levels: Optional[int] = None) -> int:
        return self.bids.depth(n_levels)

    def ask_depth(self, n_levels: Optional[int] = None) -> int:
        return self.asks.depth(n_levels)

    def level_snapshot(self, n_levels: int = 10) -> Dict[str, List[List[float]]]:
        return {
            "bids": [[self.to_price(t), q] for t, q in self.bids.level_view(n_levels)],
            "asks": [[self.to_price(t), q] for t, q in self.asks.level_view(n_levels)],
        }
