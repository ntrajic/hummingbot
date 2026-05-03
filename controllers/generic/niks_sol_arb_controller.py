"""
niks_sol_arb_controller.py

Scans 30 major exchanges for SOL-USDC/USDT, finds the biggest bid/ask gap,
and emits ArbitrageExecutorConfig actions executed on crypto_com_paper_trade.

Strategy modes (min_profitability):
  conservative = 1%  moderate = 2%  aggressive = 3%  custom = any value
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import pandas as pd

from hummingbot.client.ui.interface_utils import format_df_for_printout
from hummingbot.core.data_type.common import MarketDict
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction

# Exchanges confirmed working from this environment (binance geo-blocked, ndax/btc_markets/dexalot/hyperliquid WS failures removed)
SCAN_EXCHANGES: List[Tuple[str, str]] = [
    ("okx_paper_trade",              "SOL-USDC"),
    ("okx_paper_trade",              "SOL-USDT"),
    ("kucoin_paper_trade",           "SOL-USDC"),
    ("kucoin_paper_trade",           "SOL-USDT"),
    ("gate_io_paper_trade",          "SOL-USDC"),
    ("gate_io_paper_trade",          "SOL-USDT"),
    ("bitget_paper_trade",           "SOL-USDC"),
    ("bitget_paper_trade",           "SOL-USDT"),
    ("mexc_paper_trade",             "SOL-USDC"),
    ("mexc_paper_trade",             "SOL-USDT"),
    ("htx_paper_trade",              "SOL-USDT"),
    ("kraken_paper_trade",           "SOL-USDC"),
    ("kraken_paper_trade",           "SOL-USDT"),
    ("coinbase_advanced_trade_paper_trade", "SOL-USDC"),
    ("bitmart_paper_trade",          "SOL-USDT"),
    ("bing_x_paper_trade",           "SOL-USDT"),
    ("bitrue_paper_trade",           "SOL-USDT"),
    ("ascend_ex_paper_trade",        "SOL-USDT"),
    ("derive_paper_trade",           "SOL-USDC"),
    ("backpack_paper_trade",         "SOL-USDC"),
    ("bitstamp_paper_trade",         "SOL-USDC"),
]

EXEC_CONNECTOR = "crypto_com_paper_trade"
EXEC_PAIR = "SOL-USDC"


class NiksSolArbConfig(ControllerConfigBase):
    controller_name: str = "niks_sol_arb_controller"
    strategy_mode: str = "moderate"          # conservative|moderate|aggressive|custom
    custom_min_profitability: Decimal = Decimal("0.02")
    order_amount_usd: Decimal = Decimal("10")
    max_concurrent_executors: int = 1
    delay_between_executors: int = 15        # seconds

    @property
    def min_profitability(self) -> Decimal:
        return {
            "conservative": Decimal("0.01"),
            "moderate":     Decimal("0.02"),
            "aggressive":   Decimal("0.03"),
        }.get(self.strategy_mode, self.custom_min_profitability)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        for connector, pair in SCAN_EXCHANGES:
            markets[connector] = markets.get(connector, set()) | {pair}
        markets[EXEC_CONNECTOR] = markets.get(EXEC_CONNECTOR, set()) | {EXEC_PAIR}
        return markets


class NiksSolArbController(ControllerBase):
    """
    Each tick:
      1. Fetch best bid/ask from all 30 exchanges.
      2. Find (buy_exchange, sell_exchange) with largest gap = (sell_bid - buy_ask) / buy_ask.
      3. If gap >= min_profitability, emit CreateExecutorAction on crypto_com_paper_trade.
    """

    def __init__(self, config: NiksSolArbConfig, *args, **kwargs):
        self.config = config
        super().__init__(config, *args, **kwargs)
        self._last_executor_ts = 0.0
        self._prices: Dict[str, Dict] = {}
        self._best: Optional[Dict] = None

    async def update_processed_data(self):
        prices = {}
        for connector, pair in SCAN_EXCHANGES:
            try:
                bid = self.market_data_provider.get_price_by_type(connector, pair, "best_bid")
                ask = self.market_data_provider.get_price_by_type(connector, pair, "best_ask")
                if bid and ask and bid > 0 and ask > 0:
                    prices[f"{connector}:{pair}"] = {"connector": connector, "pair": pair,
                                                      "bid": bid, "ask": ask}
            except Exception:
                pass
        self._prices = prices
        self._best = self._find_best_gap()

    def _find_best_gap(self) -> Optional[Dict]:
        best, best_gap = None, Decimal("0")
        entries = list(self._prices.values())
        for buy in entries:
            for sell in entries:
                if buy["connector"] == sell["connector"]:
                    continue
                gap = (sell["bid"] - buy["ask"]) / buy["ask"]
                if gap > best_gap:
                    best_gap = gap
                    best = {
                        "buy_connector": buy["connector"], "buy_pair": buy["pair"],
                        "buy_ask": buy["ask"],
                        "sell_connector": sell["connector"], "sell_pair": sell["pair"],
                        "sell_bid": sell["bid"],
                        "gap_pct": float(gap * 100),
                    }
        return best

    def determine_executor_actions(self) -> List[ExecutorAction]:
        if not self._best:
            return []
        gap = Decimal(str(self._best["gap_pct"])) / 100
        if gap < self.config.min_profitability:
            return []
        now = self.market_data_provider.time()
        if now - self._last_executor_ts < self.config.delay_between_executors:
            return []
        active = [e for e in self.executors_info if e.status != RunnableStatus.TERMINATED]
        if len(active) >= self.config.max_concurrent_executors:
            return []
        try:
            sol_price = self._best["buy_ask"]
            amount = self.market_data_provider.quantize_order_amount(
                EXEC_CONNECTOR, EXEC_PAIR, self.config.order_amount_usd / sol_price)
            cfg = ArbitrageExecutorConfig(
                timestamp=now,
                buying_market=ConnectorPair(connector_name=EXEC_CONNECTOR, trading_pair=EXEC_PAIR),
                selling_market=ConnectorPair(connector_name=EXEC_CONNECTOR, trading_pair=EXEC_PAIR),
                order_amount=amount,
                min_profitability=self.config.min_profitability,
            )
            self._last_executor_ts = now
            return [CreateExecutorAction(executor_config=cfg, controller_id=self.config.id)]
        except Exception as e:
            self.logger().error(f"Error creating executor: {e}")
            return []

    def to_format_status(self) -> List[str]:
        lines = [f"Mode: {self.config.strategy_mode} | Min profit: {self.config.min_profitability:.1%} "
                 f"| Exchanges scanned: {len(self._prices)}/30"]
        if self._prices:
            rows = [{"Exchange": v["connector"], "Pair": v["pair"],
                     "Bid": f"{v['bid']:.4f}", "Ask": f"{v['ask']:.4f}"}
                    for v in sorted(self._prices.values(), key=lambda x: x["bid"], reverse=True)]
            lines.append(format_df_for_printout(pd.DataFrame(rows), table_format="psql"))
        if self._best:
            b = self._best
            lines.append(f"\n🎯 BEST GAP: Buy {b['buy_connector']} @ {b['buy_ask']:.4f}"
                         f" → Sell {b['sell_connector']} @ {b['sell_bid']:.4f}"
                         f" | Gap: {b['gap_pct']:.3f}%")
        else:
            lines.append("\nNo profitable gap found yet.")
        return lines
