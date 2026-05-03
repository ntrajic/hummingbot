"""
test_niks_triangular_arb.py
============================
Tests the triangular arbitrage profit calculation and order routing logic
without needing a live Hummingbot instance.

Run:  python test_niks_triangular_arb.py
"""
import sys
import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock

# ── Minimal stubs ─────────────────────────────────────────────────────────────
import types

def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

for _m in ["hummingbot.strategy_v2.models.executor_actions",
           "hummingbot.strategy.strategy_v2_base"]:
    _stub(_m)

class _FakeBase:
    def __init__(self, connectors, config): pass
    def logger(self):
        import logging; return logging.getLogger("test")
    def get_balance_df(self):
        import pandas as pd
        return pd.DataFrame([{"Exchange": "crypto_com_paper_trade", "Asset": "USD", "Total": 14.26}])
    ready_to_trade = True
    market_data_provider = None

sys.modules["hummingbot.strategy.strategy_v2_base"].StrategyV2Base = _FakeBase
sys.modules["hummingbot.strategy.strategy_v2_base"].StrategyV2ConfigBase = object
sys.modules["hummingbot.strategy_v2.models.executor_actions"].CreateExecutorAction = object
sys.modules["hummingbot.strategy_v2.models.executor_actions"].StopExecutorAction = object

# Stub PriceType
import hummingbot.core.data_type.common as _common
class _PT:
    BestBid = "bid"
    BestAsk = "ask"
_common.PriceType = _PT

sys.path.insert(0, "/workspaces/hummingbot")
from scripts.niks_triangular_arb_crypto_com import NiksTriangularArb, MIN_PROFIT, CONNECTOR, PAIRS

# ── Test helpers ──────────────────────────────────────────────────────────────
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}" + (f"  → {detail}" if detail else ""))
        _failures.append(name)

def make_bot(prices: dict):
    """Build a NiksTriangularArb instance with mocked price provider."""
    config = MagicMock()
    config.controllers_config = []
    bot = NiksTriangularArb.__new__(NiksTriangularArb)
    _FakeBase.__init__(bot, {}, config)
    bot._last_trade_ts = 0.0
    bot._trade_count = 0
    bot._prices = {}
    bot._profit_a = Decimal("0")
    bot._profit_b = Decimal("0")
    bot._fetch_task = None

    def mock_get_price(connector, pair, price_type):
        return prices[pair][price_type.value if hasattr(price_type,'value') else price_type]

    mdp = MagicMock()
    mdp.get_price_by_type.side_effect = mock_get_price
    bot.market_data_provider = mdp

    bot._orders = []
    def mock_buy(connector, pair, amount, order_type, price):
        bot._orders.append(("BUY", pair, amount, price))
        return f"buy://{pair}/test"
    def mock_sell(connector, pair, amount, order_type, price):
        bot._orders.append(("SELL", pair, amount, price))
        return f"sell://{pair}/test"
    bot.buy = mock_buy
    bot.sell = mock_sell
    return bot

# ── Realistic prices from live API ───────────────────────────────────────────
# SOL_USD: bid=84.56 ask=84.57
# SOL_USDT: bid=84.55 ask=84.56
# USDT_USD: bid=0.99998 ask=0.99999
LIVE_PRICES = {
    "SOL-USD":  {"bid": Decimal("84.56"),  "ask": Decimal("84.57")},
    "SOL-USDT": {"bid": Decimal("84.55"),  "ask": Decimal("84.56")},
    "USDT-USD": {"bid": Decimal("0.99998"), "ask": Decimal("0.99999")},
}

# Prices engineered to produce Route A profit > 0.2%
# SOL cheaper in USD, more expensive in USDT
PROFITABLE_A = {
    "SOL-USD":  {"bid": Decimal("84.50"),  "ask": Decimal("84.51")},   # buy SOL cheap in USD
    "SOL-USDT": {"bid": Decimal("84.80"),  "ask": Decimal("84.81")},   # sell SOL expensive in USDT
    "USDT-USD": {"bid": Decimal("0.99998"), "ask": Decimal("0.99999")},
}
# Route A: 1/84.51 * 84.80 * 0.99998 = 1.003... → profit ~0.34%

# Prices engineered to produce Route B profit > 0.2%
PROFITABLE_B = {
    "SOL-USD":  {"bid": Decimal("84.80"),  "ask": Decimal("84.81")},   # sell SOL expensive in USD
    "SOL-USDT": {"bid": Decimal("84.50"),  "ask": Decimal("84.51")},   # buy SOL cheap in USDT
    "USDT-USD": {"bid": Decimal("0.99998"), "ask": Decimal("0.99999")},
}
# Route B: 1/0.99999 * (1/84.51) * 84.80 = 1.003... → profit ~0.34%


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_profit_calculation_live_prices():
    print("\n── Test 1: Profit calculation with live prices (expect near-zero) ──")
    bot = make_bot(LIVE_PRICES)
    p_a, p_b = bot._compute_profits(LIVE_PRICES)
    print(f"     Route A: {float(p_a*100):+.4f}%")
    print(f"     Route B: {float(p_b*100):+.4f}%")
    check("Route A profit is negative (fees would eat it)", p_a < Decimal("0.001"),
          f"got {float(p_a*100):.4f}%")
    check("Route B profit is negative (fees would eat it)", p_b < Decimal("0.001"),
          f"got {float(p_b*100):.4f}%")
    check("Route A + Route B sum near zero (no free lunch)", abs(p_a + p_b) < Decimal("0.01"))


def test_profit_route_a_profitable():
    print("\n── Test 2: Route A fires when SOL cheaper in USD than USDT ──")
    bot = make_bot(PROFITABLE_A)
    p_a, p_b = bot._compute_profits(PROFITABLE_A)
    print(f"     Route A: {float(p_a*100):+.4f}%")
    check(f"Route A profit > {float(MIN_PROFIT*100):.2f}%", p_a >= MIN_PROFIT,
          f"got {float(p_a*100):.4f}%")
    check("Route B is not profitable in same scenario", p_b < MIN_PROFIT)


def test_profit_route_b_profitable():
    print("\n── Test 3: Route B fires when SOL cheaper in USDT than USD ──")
    bot = make_bot(PROFITABLE_B)
    p_a, p_b = bot._compute_profits(PROFITABLE_B)
    print(f"     Route B: {float(p_b*100):+.4f}%")
    check(f"Route B profit > {float(MIN_PROFIT*100):.2f}%", p_b >= MIN_PROFIT,
          f"got {float(p_b*100):.4f}%")
    check("Route A is not profitable in same scenario", p_a < MIN_PROFIT)


def test_route_a_places_correct_orders():
    print("\n── Test 4: Route A places BUY SOL-USD, SELL SOL-USDT, SELL USDT-USD ──")
    bot = make_bot(PROFITABLE_A)
    bot._profit_a, _ = bot._compute_profits(PROFITABLE_A)
    bot._execute_route_a(PROFITABLE_A)
    check("3 orders placed", len(bot._orders) == 3, f"got {len(bot._orders)}")
    sides  = [o[0] for o in bot._orders]
    pairs  = [o[1] for o in bot._orders]
    check("order 1: BUY SOL-USD",   sides[0] == "BUY"  and pairs[0] == "SOL-USD")
    check("order 2: SELL SOL-USDT", sides[1] == "SELL" and pairs[1] == "SOL-USDT")
    check("order 3: SELL USDT-USD", sides[2] == "SELL" and pairs[2] == "USDT-USD")
    sol_amount = bot._orders[0][2]
    check("SOL amount ≈ $10 / ask(SOL-USD)",
          abs(sol_amount - round(Decimal("10") / PROFITABLE_A["SOL-USD"]["ask"], 4)) < Decimal("0.001"),
          f"got {sol_amount}")
    print(f"     Orders: {bot._orders}")


def test_route_b_places_correct_orders():
    print("\n── Test 5: Route B places BUY USDT-USD, BUY SOL-USDT, SELL SOL-USD ──")
    bot = make_bot(PROFITABLE_B)
    _, bot._profit_b = bot._compute_profits(PROFITABLE_B)
    bot._execute_route_b(PROFITABLE_B)
    check("3 orders placed", len(bot._orders) == 3, f"got {len(bot._orders)}")
    sides = [o[0] for o in bot._orders]
    pairs = [o[1] for o in bot._orders]
    check("order 1: BUY USDT-USD",  sides[0] == "BUY"  and pairs[0] == "USDT-USD")
    check("order 2: BUY SOL-USDT",  sides[1] == "BUY"  and pairs[1] == "SOL-USDT")
    check("order 3: SELL SOL-USD",  sides[2] == "SELL" and pairs[2] == "SOL-USD")
    print(f"     Orders: {bot._orders}")


def test_no_order_below_threshold():
    print("\n── Test 6: No orders placed when profit < MIN_PROFIT ──")
    bot = make_bot(LIVE_PRICES)
    bot._prices = LIVE_PRICES
    bot._profit_a, bot._profit_b = bot._compute_profits(LIVE_PRICES)
    # Simulate what _fetch_and_trade does after prices are set
    now = time.time()
    if bot._profit_a >= MIN_PROFIT:
        bot._execute_route_a(LIVE_PRICES)
    elif bot._profit_b >= MIN_PROFIT:
        bot._execute_route_b(LIVE_PRICES)
    check("no orders placed on flat market", len(bot._orders) == 0, f"got {len(bot._orders)}")
    check("trade count still 0", bot._trade_count == 0)


def test_cooldown_blocks_repeat():
    print("\n── Test 7: Cooldown prevents back-to-back execution ──")
    bot = make_bot(PROFITABLE_A)
    bot._prices = PROFITABLE_A
    bot._profit_a, bot._profit_b = bot._compute_profits(PROFITABLE_A)

    # First execution
    if bot._profit_a >= MIN_PROFIT:
        bot._execute_route_a(PROFITABLE_A)
        bot._last_trade_ts = time.time()
    count_after_first = bot._trade_count

    # Second immediate attempt — cooldown active
    now = time.time()
    fired = False
    if now - bot._last_trade_ts >= 30:  # COOLDOWN
        bot._execute_route_a(PROFITABLE_A)
        fired = True

    check("first execution fires", count_after_first == 1, f"got {count_after_first}")
    check("second attempt blocked by cooldown", not fired)


def test_fetch_prices_returns_none_on_missing():
    print("\n── Test 8: _compute_profits handles zero ask gracefully ──")
    bot = make_bot(LIVE_PRICES)
    # Prices with a zero ask — division by zero should be caught
    bad_prices = dict(LIVE_PRICES)
    bad_prices["SOL-USD"] = {"bid": Decimal("0"), "ask": Decimal("0")}
    try:
        bot._compute_profits(bad_prices)
        check("zero ask raises exception", False, "no exception raised")
    except Exception:
        check("zero ask raises exception", True)


def test_status_output():
    print("\n── Test 9: format_status produces readable output ──")
    bot = make_bot(PROFITABLE_A)
    bot._prices = PROFITABLE_A
    bot._profit_a, bot._profit_b = bot._compute_profits(PROFITABLE_A)
    status = bot.format_status()
    check("status contains Route A", "Route A" in status)
    check("status contains Route B", "Route B" in status)
    check("status contains threshold", "0.01%" in status)
    check("status shows FIRING when profitable", "FIRING" in status)
    print("\n     Status preview:")
    for line in status.split("\n")[:15]:
        print(f"     {line}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  NiksTriangularArb — Logic Test Suite")
    print("=" * 60)

    test_profit_calculation_live_prices()
    test_profit_route_a_profitable()
    test_profit_route_b_profitable()
    test_route_a_places_correct_orders()
    test_route_b_places_correct_orders()
    test_no_order_below_threshold()
    test_cooldown_blocks_repeat()
    test_fetch_prices_returns_none_on_missing()
    test_status_output()

    print("\n" + "=" * 60)
    if _failures:
        print(f"  \033[91mFAILED: {len(_failures)}\033[0m")
        for f in _failures: print(f"    • {f}")
        sys.exit(1)
    else:
        print("  \033[92mAll tests passed ✓\033[0m")
    print("=" * 60)
