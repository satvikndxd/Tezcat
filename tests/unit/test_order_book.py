from tezcat.market.order_book import Order, OrderBook


def _order(oid, side, price, qty, seq=0):
    return Order(order_id=oid, agent_id=f"a_{oid}", side=side, order_type="limit",
                 price=price, quantity=qty, remaining=qty, seq=seq)


def test_empty_book():
    book = OrderBook(0.05)
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.spread is None
    assert book.mid_price() is None
    assert book.bid_depth() == 0


def test_best_bid_ask_and_spread():
    book = OrderBook(0.05)
    book.add_limit(_order("b1", "buy", 99.90, 10))
    book.add_limit(_order("b2", "buy", 99.95, 5))
    book.add_limit(_order("s1", "sell", 100.10, 7))
    book.add_limit(_order("s2", "sell", 100.05, 3))
    assert book.best_bid == 99.95
    assert book.best_ask == 100.05
    assert abs(book.spread - 0.10) < 1e-9
    assert book.mid_price() == 100.0


def test_price_time_priority():
    book = OrderBook(0.05)
    first = _order("b1", "buy", 100.0, 10, seq=1)
    second = _order("b2", "buy", 100.0, 5, seq=2)
    book.add_limit(first)
    book.add_limit(second)
    assert book.bids.peek_best_order() is first  # same price: earliest first
    higher = _order("b3", "buy", 100.05, 2, seq=3)
    book.add_limit(higher)
    assert book.bids.peek_best_order() is higher  # better price wins


def test_cancel():
    book = OrderBook(0.05)
    book.add_limit(_order("b1", "buy", 100.0, 10))
    assert book.cancel("b1", step=1) is not None
    assert book.best_bid is None
    assert book.cancel("missing", step=1) is None


def test_depth_and_levels():
    book = OrderBook(0.05)
    book.add_limit(_order("b1", "buy", 99.95, 10))
    book.add_limit(_order("b2", "buy", 99.90, 20))
    book.add_limit(_order("s1", "sell", 100.0, 5))
    assert book.bid_depth() == 30
    assert book.ask_depth() == 5
    snap = book.level_snapshot(10)
    assert snap["bids"] == [[99.95, 10], [99.90, 20]]
    assert snap["asks"] == [[100.0, 5]]


def test_tick_snapping():
    book = OrderBook(0.05)
    assert book.snap(99.97) == 99.95
    assert book.snap(99.98) == 100.0
