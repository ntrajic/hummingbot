"""
test_niks_sol_arb.py
====================
Tests the core arbitrage logic of NiksSolArbController WITHOUT needing a running
Hummingbot instance or live exchange connections.

Simulates 13 exchanges with realistic SOL prices where the best gap is
deliberately > 0.01%, then verifies:
  1. _find_best_gap() identifies the correct buy/sell pair
  2. determine_executor_actions() emits a CreateExecutorAction
  3. The ArbitrageExecutorConfig has the right connectors and amount
  4. No action is emitted when gap < min_profitability
  5. No action is emitted when the same connector would be used for both sides
  6. Cooldown (delay_between_executors) blocks a second immediate action

Run with:
    python test_niks_sol_arb.py
"""
import asyncio
import time
from decimal import Decimal
from typing import Dict, Optional
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so we can import the controller without a full Hummingbot env
# ---------------------------------------------------------------------------
import sys, types

def _stub_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

# Only stub what the controller actually imports at module level
for _m in [
    "hummingbot.client.ui.interface_utils",
    "hummingbot.strategy_v2.models.base",
    "hummingbot.strategy_v2.models.executor_actions",
    "hummingbot.strategy_v2.executors.data_types",
    "hummingbot.strategy_v2.executors.arbitrage_executor.data_types",
    "hummingbot.strategy_v2.controllers.controller_base",
]:
    _stub_module(_m)

# Stub format_df_for_printout
sys.modules["hummingbot.client.ui.interface_utils"].format_df_for_printout = lambda df, **kw: df.to_string()

# Stub RunnableStatus
class RunnableStatus:
    TERMINATED = "TERMINATED"
    RUNNING = "RUNNING"
sys.modules["hummingbot.strategy_v2.models.base"].RunnableStatus = RunnableStatus

# Stub executor action types
class CreateExecutorAction:
    def __init__(self, executor_config, controller_id):
        self.executor_config = executor_config
        self.controller_id = controller_id
    def __repr__(self):
        return f"CreateExecutorAction(controller={self.controller_id})"

class StopExecutorAction: pass
class ExecutorAction: pass

_ea = sys.modules["hummingbot.strategy_v2.models.executor_actions"]
_ea.CreateExecutorAction = CreateExecutorAction
_ea.StopExecutorAction = StopExecutorAction
_ea.ExecutorAction = ExecutorAction

# Stub ConnectorPair and ArbitrageExecutorConfig
class ConnectorPair:
    def __init__(self, connector_name, trading_pair):
        self.connector_name = connector_name
        self.trading_pair = trading_pair
    def __repr__(self):
        return f"{self.connector_name}:{self.trading_pair}"

class ArbitrageExecutorConfig:
    def __init__(self, timestamp, buying_market, selling_market, order_amount, min_profitability):
        self.timestamp = timestamp
        self.buying_market = buying_market
        self.selling_market = selling_market
        self.order_amount = order_amount
        self.min_profitability = min_profitability
    def __repr__(self):
        return (f"ArbitrageExecutorConfig(\n"
                f"  buy  = {self.buying_market}\n"
                f"  sell = {self.selling_market}\n"
                f"  amount = {self.order_amount}\n"
                f"  min_profit = {self.min_profitability:.4%})")

sys.modules["hummingbot.strategy_v2.executors.data_types"].ConnectorPair = ConnectorPair
sys.modules["hummingbot.strategy_v2.executors.arbitrage_executor.data_types"].ArbitrageExecutorConfig = ArbitrageExecutorConfig

# Stub ControllerBase and ControllerConfigBase
class ControllerConfigBase:
    id: str = "test-controller-id"

class ControllerBase:
    def __init__(self, config, market_data_provider, actions_queue, **kwargs):
        self.config = config
        self.market_data_provider = market_data_provider
        self.actions_queue = actions_queue
        self.executors_info = []
        self._logger = None
    def logger(self):
        import logging
        return logging.getLogger("NiksSolArbController")

_cb = sys.modules["hummingbot.strategy_v2.controllers.controller_base"]
_cb.ControllerBase = ControllerBase
_cb.ControllerConfigBase = ControllerConfigBase

# Stub MarketDict
from hummingbot.core.data_type.common import PriceType  # real import — this is what we're testing

# ---------------------------------------------------------------------------
# Now import the real controller
# ---------------------------------------------------------------------------
sys.path.insert(0, "/workspaces/hummingbot")
from controllers.generic.niks_sol_arb_controller import (
    NiksSolArbConfig,
    NiksSolArbController,
    SCAN_EXCHANGES,
)

# ---------------------------------------------------------------------------
# Realistic SOL prices (USD) across 13 exchange slots — May 2026 range
# Deliberately set kraken SOL-USDT bid HIGH and mexc SOL-USDC ask LOW
# so the gap is clearly > 0.01%
# ---------------------------------------------------------------------------
MOCK_PRICES: Dict[str, Dict[str, Decimal]] = {
    # connector:pair  ->  bid, ask
    "okx_paper_trade:SOL-USDC":    {"bid": Decimal("148.20"), "ask": Decimal("148.25")},
    "okx_paper_trade:SOL-USDT":    {"bid": Decimal("148.22"), "ask": Decimal("148.27")},
    "kucoin_paper_trade:SOL-USDC": {"bid": Decimal("148.18"), "ask": Decimal("148.23")},
    "kucoin_paper_trade:SOL-USDT": {"bid": Decimal("148.19"), "ask": Decimal("148.24")},
    "gate_io_paper_trade:SOL-USDC":{"bid": Decimal("148.21"), "ask": Decimal("148.26")},
    "gate_io_paper_trade:SOL-USDT":{"bid": Decimal("148.23"), "ask": Decimal("148.28")},
    "bitget_paper_trade:SOL-USDC": {"bid": Decimal("148.17"), "ask": Decimal("148.22")},
    "bitget_paper_trade:SOL-USDT": {"bid": Decimal("148.20"), "ask": Decimal("148.25")},
    "mexc_paper_trade:SOL-USDC":   {"bid": Decimal("148.15"), "ask": Decimal("148.19")},  # cheapest ask
    "mexc_paper_trade:SOL-USDT":   {"bid": Decimal("148.16"), "ask": Decimal("148.21")},
    "htx_paper_trade:SOL-USDT":    {"bid": Decimal("148.22"), "ask": Decimal("148.27")},
    "kraken_paper_trade:SOL-USDC": {"bid": Decimal("148.30"), "ask": Decimal("148.35")},  # highest bid
    "kraken_paper_trade:SOL-USDT": {"bid": Decimal("148.28"), "ask": Decimal("148.33")},
    "bitmart_paper_trade:SOL-USDT":{"bid": Decimal("148.20"), "ask": Decimal("148.25")},
}

# Expected best gap:
#   buy  at mexc_paper_trade:SOL-USDC  ask = 148.19
#   sell at kraken_paper_trade:SOL-USDC bid = 148.30
#   gap  = (148.30 - 148.19) / 148.19 = 0.0742%  >> 0.01%
EXPECTED_BUY_CONNECTOR  = "mexc_paper_trade"
EXPECTED_SELL_CONNECTOR = "kraken_paper_trade"
EXPECTED_GAP_PCT_MIN    = 0.07   # at least 0.07%


# ---------------------------------------------------------------------------
# Mock MarketDataProvider
# ---------------------------------------------------------------------------
class MockMarketDataProvider:
    def get_price_by_type(self, connector: str, pair: str, price_type: PriceType) -> Optional[Decimal]:
        key = f"{connector}:{pair}"
        entry = MOCK_PRICES.get(key)
        if entry is None:
            return None
        return entry["bid"] if price_type == PriceType.BestBid else entry["ask"]

    def time(self) -> float:
        return time.time()

    def quantize_order_amount(self, connector: str, pair: str, amount: Decimal) -> Decimal:
        # Round to 2 decimal places (realistic for SOL)
        return round(amount, 2)


# ---------------------------------------------------------------------------
# Helper to build a controller instance
# ---------------------------------------------------------------------------
def make_controller(last_executor_ts: float = 0.0) -> NiksSolArbController:
    config = NiksSolArbConfig()
    config.id = "test-controller-id"
    mdp = MockMarketDataProvider()
    ctrl = NiksSolArbController(config, market_data_provider=mdp, actions_queue=asyncio.Queue())
    ctrl._last_executor_ts = last_executor_ts
    return ctrl


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_failures = []

def check(name: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}" + (f"  →  {detail}" if detail else ""))
        _failures.append(name)


async def test_prices_loaded():
    print("\n── Test 1: update_processed_data populates _prices ──")
    ctrl = make_controller()
    await ctrl.update_processed_data()
    check("all 14 exchange slots have prices",
          len(ctrl._prices) == len(SCAN_EXCHANGES),
          f"got {len(ctrl._prices)}, expected {len(SCAN_EXCHANGES)}")
    check("prices contain bid > 0",
          all(v["bid"] > 0 for v in ctrl._prices.values()))
    check("prices contain ask > bid",
          all(v["ask"] >= v["bid"] for v in ctrl._prices.values()))


async def test_best_gap_found():
    print("\n── Test 2: _find_best_gap identifies correct buy/sell pair ──")
    ctrl = make_controller()
    await ctrl.update_processed_data()
    b = ctrl._best
    check("_best is not None", b is not None)
    if b is None:
        return
    check(f"buy connector = {EXPECTED_BUY_CONNECTOR}",
          b["buy_connector"] == EXPECTED_BUY_CONNECTOR,
          f"got {b['buy_connector']}")
    check(f"sell connector = {EXPECTED_SELL_CONNECTOR}",
          b["sell_connector"] == EXPECTED_SELL_CONNECTOR,
          f"got {b['sell_connector']}")
    check(f"gap > {EXPECTED_GAP_PCT_MIN}%",
          b["gap_pct"] > EXPECTED_GAP_PCT_MIN,
          f"got {b['gap_pct']:.4f}%")
    check("buy and sell connectors are DIFFERENT",
          b["buy_connector"] != b["sell_connector"])
    print(f"     gap = {b['gap_pct']:.4f}%  |  "
          f"buy {b['buy_connector']} @ {b['buy_ask']}  →  "
          f"sell {b['sell_connector']} @ {b['sell_bid']}")


async def test_executor_action_emitted():
    print("\n── Test 3: determine_executor_actions emits CreateExecutorAction ──")
    ctrl = make_controller()
    await ctrl.update_processed_data()
    actions = ctrl.determine_executor_actions()
    check("exactly 1 action emitted", len(actions) == 1, f"got {len(actions)}")
    if not actions:
        return
    action = actions[0]
    check("action is CreateExecutorAction", isinstance(action, CreateExecutorAction))
    cfg = action.executor_config
    check("executor_config is ArbitrageExecutorConfig", isinstance(cfg, ArbitrageExecutorConfig))
    check(f"buying_market = {EXPECTED_BUY_CONNECTOR}",
          cfg.buying_market.connector_name == EXPECTED_BUY_CONNECTOR,
          f"got {cfg.buying_market.connector_name}")
    check(f"selling_market = {EXPECTED_SELL_CONNECTOR}",
          cfg.selling_market.connector_name == EXPECTED_SELL_CONNECTOR,
          f"got {cfg.selling_market.connector_name}")
    check("order_amount > 0", cfg.order_amount > 0, f"got {cfg.order_amount}")
    check("order_amount ≈ $10 / SOL_price",
          Decimal("0.05") < cfg.order_amount < Decimal("0.10"),
          f"got {cfg.order_amount} SOL  (expected ~0.067 SOL for $10 @ ~148)")
    check("min_profitability = 0.0001", cfg.min_profitability == Decimal("0.0001"))
    print(f"\n     {cfg}")


async def test_no_action_below_min_profit():
    print("\n── Test 4: no action when gap < min_profitability ──")
    ctrl = make_controller()
    # Inject prices where all bids/asks are identical across exchanges → gap = 0
    flat = Decimal("148.20")
    ctrl._prices = {
        f"{c}:{p}": {"connector": c, "pair": p, "bid": flat, "ask": flat + Decimal("0.01")}
        for c, p in SCAN_EXCHANGES
    }
    ctrl._best = ctrl._find_best_gap()
    actions = ctrl.determine_executor_actions()
    check("no action when gap is 0", len(actions) == 0, f"got {len(actions)}")


async def test_no_action_same_connector():
    print("\n── Test 5: _find_best_gap never picks same connector for buy+sell ──")
    ctrl = make_controller()
    # Only one connector has data — gap must be None
    ctrl._prices = {
        "okx_paper_trade:SOL-USDC": {"connector": "okx_paper_trade", "pair": "SOL-USDC",
                                      "bid": Decimal("150.00"), "ask": Decimal("148.00")},
        "okx_paper_trade:SOL-USDT": {"connector": "okx_paper_trade", "pair": "SOL-USDT",
                                      "bid": Decimal("151.00"), "ask": Decimal("147.00")},
    }
    ctrl._best = ctrl._find_best_gap()
    check("_best is None when only one connector has data", ctrl._best is None,
          f"got {ctrl._best}")


async def test_cooldown_blocks_second_action():
    print("\n── Test 6: cooldown blocks second action within delay_between_executors ──")
    ctrl = make_controller()
    await ctrl.update_processed_data()
    # First action — should succeed
    actions1 = ctrl.determine_executor_actions()
    check("first action emitted", len(actions1) == 1)
    # Second immediate call — _last_executor_ts was just set, cooldown active
    actions2 = ctrl.determine_executor_actions()
    check("second action blocked by cooldown", len(actions2) == 0, f"got {len(actions2)}")


async def test_status_output():
    print("\n── Test 7: to_format_status produces readable output ──")
    ctrl = make_controller()
    await ctrl.update_processed_data()
    lines = ctrl.to_format_status()
    check("status has at least 2 lines", len(lines) >= 2)
    check("status contains GAP line", any("GAP" in l or "gap" in l.lower() for l in lines),
          f"lines: {lines}")
    print("\n     Status output preview:")
    for line in lines:
        for row in line.split("\n"):
            print(f"     {row}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    print("=" * 60)
    print("  NiksSolArbController — Arbitrage Logic Test Suite")
    print("=" * 60)

    await test_prices_loaded()
    await test_best_gap_found()
    await test_executor_action_emitted()
    await test_no_action_below_min_profit()
    await test_no_action_same_connector()
    await test_cooldown_blocks_second_action()
    await test_status_output()

    print("\n" + "=" * 60)
    if _failures:
        print(f"  \033[91mFAILED: {len(_failures)} test(s)\033[0m")
        for f in _failures:
            print(f"    • {f}")
        sys.exit(1)
    else:
        print(f"  \033[92mAll tests passed ✓\033[0m")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
