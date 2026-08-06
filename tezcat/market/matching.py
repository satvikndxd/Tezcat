"""Matching engine: price-time priority execution with settlement.

Rules
-----
1. A buy matches while its price >= best ask; a sell while its price <= best bid.
2. Same-price orders execute in arrival (time) order.
3. Execution price is always the *resting* order's price.
4. Partial fills are allowed; a non-marketable limit remainder rests in the book.
5. Market orders execute until filled, unaffordable, or the book is empty; any
   remainder is discarded (status "cancelled").
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from tezcat.agents.portfolio import Portfolio
from tezcat.core.config import MarketConfig
from tezcat.market.order_book import Order, OrderBook

_trade_counter = itertools.count(1)


@dataclass
class Trade:
    trade_id: str
    step: int
    price: float
    quantity: int
    buy_agent_id: str
    sell_agent_id: str
    buy_order_id: str
    sell_order_id: str


class OrderRejected(Exception):
    pass


class MatchingEngine:
    def __init__(self, book: OrderBook, portfolios: Dict[str, Portfolio], config: MarketConfig):
        self.book = book
        self.portfolios = portfolios
        self.config = config

    # ------------------------------------------------------------------
    def validate(self, order: Order) -> Optional[str]:
        """Return a rejection reason or None if the order is acceptable."""
        pf = self.portfolios.get(order.agent_id)
        if pf is None:
            return "unknown agent"
        if order.quantity < self.config.min_order_size:
            return "quantity below minimum"
        if order.quantity > self.config.max_order_size:
            return "quantity above maximum"
        if order.order_type == "limit":
            if order.price is None or self.book.snap(order.price) < self.book.tick_size:
                return "invalid limit price"
        if order.side == "sell" and not self.config.allow_short:
            if not pf.can_sell(order.quantity):
                return "insufficient inventory"
        if order.side == "buy" and not self.config.allow_negative_cash:
            if order.order_type == "limit":
                if not pf.can_buy(self.book.snap(order.price), order.quantity):
                    return "insufficient cash"
            else:
                ref = self.book.best_ask or self.book.mid_price()
                if ref is not None and pf.available_cash < ref * self.config.min_order_size:
                    return "insufficient cash"
        return None

    # ------------------------------------------------------------------
    def submit(self, order: Order, step: int) -> Tuple[List[Trade], Order]:
        """Validate, match, and (for limit remainders) rest the order."""
        reason = self.validate(order)
        if reason is not None:
            order.status = "rejected"
            order.updated_step = step
            return [], order

        pf = self.portfolios[order.agent_id]
        if order.order_type == "limit":
            order.price = self.book.snap(order.price)

        # Reserve resources up-front so concurrent intents can't overspend.
        if order.side == "sell":
            pf.reserve_inventory(order.remaining)
        elif order.order_type == "limit":
            pf.reserve_cash(order.price * order.remaining)

        trades = self._match(order, step)

        if order.remaining > 0:
            if order.order_type == "limit":
                order.status = "partial" if trades else "new"
                order.created_step = step
                order.updated_step = step
                order.seq = self.book.next_seq()
                self.book.add_limit(order)
            else:
                # Unfilled market remainder is discarded; release reservations.
                if order.side == "sell":
                    pf.release_inventory(order.remaining)
                order.status = "partial" if trades else "cancelled"
                order.updated_step = step
        else:
            order.status = "filled"
            order.updated_step = step
        return trades, order

    # ------------------------------------------------------------------
    def release_on_cancel(self, order: Order) -> None:
        """Release reservations tied to a resting order's remaining quantity."""
        pf = self.portfolios.get(order.agent_id)
        if pf is None:
            return
        if order.side == "sell":
            pf.release_inventory(order.remaining)
        else:
            pf.release_cash((order.price or 0.0) * order.remaining)

    # ------------------------------------------------------------------
    def _crosses(self, order: Order, resting_price: float) -> bool:
        if order.order_type == "market":
            return True
        if order.side == "buy":
            return order.price >= resting_price - 1e-12
        return order.price <= resting_price + 1e-12

    def _match(self, order: Order, step: int) -> List[Trade]:
        trades: List[Trade] = []
        opposite = self.book.asks if order.side == "buy" else self.book.bids
        pf = self.portfolios[order.agent_id]

        while order.remaining > 0:
            resting = opposite.peek_best_order()
            if resting is None:
                break
            exec_price = resting.price
            if not self._crosses(order, exec_price):
                break

            qty = min(order.remaining, resting.remaining)

            # Market buys are capped by the buyer's available cash.
            if order.side == "buy" and order.order_type == "market" and not self.config.allow_negative_cash:
                if exec_price <= 0:
                    break
                affordable = int(pf.available_cash // exec_price)
                if affordable <= 0:
                    break
                qty = min(qty, affordable)

            buyer_id = order.agent_id if order.side == "buy" else resting.agent_id
            seller_id = resting.agent_id if order.side == "buy" else order.agent_id
            buy_order = order if order.side == "buy" else resting
            sell_order = resting if order.side == "buy" else order

            self._settle(buy_order, sell_order, exec_price, qty)

            order.remaining -= qty
            resting.remaining -= qty
            order.updated_step = step
            resting.updated_step = step

            trades.append(
                Trade(
                    trade_id=f"trd_{next(_trade_counter):08d}",
                    step=step,
                    price=exec_price,
                    quantity=qty,
                    buy_agent_id=buyer_id,
                    sell_agent_id=seller_id,
                    buy_order_id=buy_order.order_id,
                    sell_order_id=sell_order.order_id,
                )
            )

            if resting.remaining == 0:
                resting.status = "filled"
                self.book.cancel(resting.order_id, step, status="filled")
            else:
                resting.status = "partial"
        return trades

    def _settle(self, buy_order: Order, sell_order: Order, price: float, qty: int) -> None:
        buyer = self.portfolios[buy_order.agent_id]
        seller = self.portfolios[sell_order.agent_id]

        # Release the buyer's reservation at the *reserved* (limit) price.
        if buy_order.order_type == "limit":
            buyer.release_cash((buy_order.price or price) * qty)
        seller.release_inventory(qty)

        buyer.apply_buy(price, qty)
        seller.apply_sell(price, qty)
