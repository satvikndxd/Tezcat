"""Point-in-time order-book metrics (Phase F7).

Pure functions with explicit units, edge cases, and interpretation. Every
function returns ``None`` when the quantity is undefined (empty or one-sided
book, zero depth) — never a fabricated number.

Definitions
-----------
microprice
    ``(ask_qty * bid_px + bid_qty * ask_px) / (bid_qty + ask_qty)``.
    Depth-weighted expected next mid: leans toward the *thinner* side's
    price, since the thin queue is likelier to be consumed. Units: price.

queue_imbalance
    ``(bid_qty - ask_qty) / (bid_qty + ask_qty)`` at the best level,
    in [-1, 1]. Positive = bid-heavy book. Dimensionless. This is a state
    variable empirically associated with short-horizon mid moves; any
    association measured here is a within-model observation, not a claim
    about real markets.

relative_spread
    ``(ask - bid) / mid``. Dimensionless (fraction of mid); comparable
    across price levels and configs.
"""

from __future__ import annotations

from typing import Optional


def microprice(bid_px: Optional[float], bid_qty: int,
               ask_px: Optional[float], ask_qty: int) -> Optional[float]:
    if bid_px is None or ask_px is None:
        return None
    total = bid_qty + ask_qty
    if total <= 0:
        return None
    return (ask_qty * bid_px + bid_qty * ask_px) / total


def queue_imbalance(bid_qty: int, ask_qty: int) -> Optional[float]:
    total = bid_qty + ask_qty
    if total <= 0:
        return None
    return (bid_qty - ask_qty) / total


def relative_spread(bid_px: Optional[float], ask_px: Optional[float]) -> Optional[float]:
    if bid_px is None or ask_px is None:
        return None
    mid = (bid_px + ask_px) / 2
    if mid <= 0:
        return None
    return (ask_px - bid_px) / mid
