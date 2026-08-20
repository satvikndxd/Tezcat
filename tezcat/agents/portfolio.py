"""Agent portfolio accounting with reservation-based invariants.

Cash for open buy orders and inventory for open sell orders are *reserved* at
order-acceptance time so no agent can commit resources it does not have.

Monetary precision policy
-------------------------
Cash is binary floating point. Prices are tick-snapped and quantities are
integers, so every settlement amount is ``tick_price * int``; float error
enters only through repeated add/subtract accumulation. Two tolerances are
defined and used consistently:

- ``CASH_EPS`` (1e-9): comparison slack for order admission (``can_buy``).
- ``INVARIANT_EPS`` (1e-6): slack for conservation/invariant assertions.

Amounts within these tolerances are considered equal; a violation beyond
``INVARIANT_EPS`` is treated as an accounting bug, not rounding noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CASH_EPS = 1e-9
INVARIANT_EPS = 1e-6


@dataclass
class Portfolio:
    cash: float
    inventory: int
    reserved_cash: float = 0.0
    reserved_inventory: int = 0
    realized_pnl: float = 0.0
    avg_cost: float = 0.0
    initial_cash: float = field(default=0.0)
    initial_inventory: int = field(default=0)

    def __post_init__(self) -> None:
        self.initial_cash = self.cash
        self.initial_inventory = self.inventory

    # -- availability --------------------------------------------------
    @property
    def available_cash(self) -> float:
        return self.cash - self.reserved_cash

    @property
    def available_inventory(self) -> int:
        return self.inventory - self.reserved_inventory

    def can_buy(self, price: float, quantity: int) -> bool:
        return self.available_cash + CASH_EPS >= price * quantity

    def can_sell(self, quantity: int) -> bool:
        return self.available_inventory >= quantity

    # -- reservations --------------------------------------------------
    def reserve_cash(self, amount: float) -> None:
        self.reserved_cash += amount

    def release_cash(self, amount: float) -> None:
        self.reserved_cash = max(0.0, self.reserved_cash - amount)

    def reserve_inventory(self, quantity: int) -> None:
        self.reserved_inventory += quantity

    def release_inventory(self, quantity: int) -> None:
        self.reserved_inventory = max(0, self.reserved_inventory - quantity)

    # -- settlement ----------------------------------------------------
    def apply_buy(self, price: float, quantity: int) -> None:
        cost = price * quantity
        total_cost = self.avg_cost * self.inventory + cost
        self.cash -= cost
        self.inventory += quantity
        self.avg_cost = total_cost / self.inventory if self.inventory > 0 else 0.0

    def apply_sell(self, price: float, quantity: int) -> None:
        self.cash += price * quantity
        self.inventory -= quantity
        self.realized_pnl += (price - self.avg_cost) * quantity
        if self.inventory <= 0:
            self.avg_cost = 0.0

    # -- valuation -----------------------------------------------------
    def unrealized_pnl(self, mark_price: float) -> float:
        return (mark_price - self.avg_cost) * self.inventory if self.inventory > 0 else 0.0

    def equity(self, mark_price: float) -> float:
        return self.cash + self.inventory * mark_price

    def total_pnl(self, mark_price: float, initial_mark: float) -> float:
        return self.equity(mark_price) - (self.initial_cash + self.initial_inventory * initial_mark)
