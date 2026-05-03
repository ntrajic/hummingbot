"""
niks_sol_arb_controller.py

Scans working paper-trade exchanges for SOL-USDC/USDT, finds the biggest bid/ask gap,
and emits ArbitrageExecutorConfig actions.

Fixes applied:
  1. Removed broken exchanges: bitstamp, bybit, binance, backpack, derive, bing_x, bitrue, ascend_ex
  2. buying_market and selling_market are now always DIFFERENT connectors
  3. min_profitability lowered to 0.0001 (0.01%) so any real spread triggers an order
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import pandas as pd

from hummingbot.client.ui.interface_utils import format_df_for_printout
from hummingbot.core.data_type.common import MarketDict, PriceType
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction

# Only exchanges confirmed working in this environment (no geo-blocks, no broken connectors)
SCAN_EXCHANGES: List[Tuple[str, str]] = [
    ("okx_paper_trade",                    "SOL-USDC"),
    ("okx_paper_trade",                    "SOL-USDT"),
    ("kucoin_paper_trade",                 "SOL-USDC"),
    ("kucoin_paper_trade",                 "SOL-USDT"),
    ("gate_io_paper_trade",                "SOL-USDC"),
    ("gate_io_paper_trade",                "SOL-USDT"),
    ("bitget_paper_trade",                 "SOL-USDC"),
    ("bitget_paper_trade",                 "SOL-USDT"),
    ("mexc_paper_trade",                   "SOL-USDC"),
    ("mexc_paper_trade",                   "SOL-USDT"),
    ("htx_paper_trade",                    "SOL-USDT"),
    ("kraken_paper_trade",                 "SOL-USDC"),
    ("kraken_paper_trade",                 "SOL-USDT"),
    ("bitmart_paper_trade",                "SOL-USDT"),
]


class NiksSolArbConfig(ControllerConfigBase):
    controller_name: str = "niks_sol_arb_controller"
    strategy_mode: str = "aggressive"
    custom_min_profitability: Decimal = Decimal("0.0001")
    order_amount_usd: Decimal = Decimal("10")
    max_concurrent_executors: int = 2
    delay_between_executors: int = 10        # seconds

    @property
    def min_profitability(self) -> Decimal:
        return {
            "conservative": Decimal("0.001"),
            "moderate":     Decimal("0.0005"),
            "aggressive":   Decimal("0.0001"),
        }.get(self.strategy_mode, self.custom_min_profitability)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        for connector, pair in SCAN_EXCHANGES:
            markets[connector] = markets.get(connector, set()) | {pair}
        return markets


class NiksSolArbController(ControllerBase):
    """
    Each tick:
      1. Fetch best bid/ask from all working paper-trade exchanges.
      2. Find (buy_exchange, sell_exchange) with largest gap = (sell_bid - buy_ask) / buy_ask.
         buy_exchange and sell_exchange MUST be different connectors.
      3. If gap >= min_profitability, emit CreateExecutorAction.
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
                bid = self.market_data_provider.get_price_by_type(connector, pair, PriceType.BestBid)
                ask = self.market_data_provider.get_price_by_type(connector, pair, PriceType.BestAsk)
                if bid and ask and bid > 0 and ask > 0:
                    prices[f"{connector}:{pair}"] = {
                        "connector": connector,
                        "pair": pair,
                        "bid": bid,
                        "ask": ask,
                    }
            except Exception as e:
                self.logger().warning(f"Price fetch failed for {connector}:{pair} — {e}")
        prev_count = len(self._prices)
        self._prices = prices
        self._best = self._find_best_gap()
        # Log every time price count changes, or every 30s via tick counter
        if not hasattr(self, "_tick"):
            self._tick = 0
        self._tick += 1
        if len(prices) != prev_count or self._tick % 30 == 1:
            if self._best:
                b = self._best
                self.logger().info(
                    f"[ARB SCAN] {len(prices)}/{len(SCAN_EXCHANGES)} feeds live | "
                    f"BEST GAP: buy {b['buy_connector']} @ {b['buy_ask']:.4f} → "
                    f"sell {b['sell_connector']} @ {b['sell_bid']:.4f} | "
                    f"gap={b['gap_pct']:.4f}%"
                )
            else:
                self.logger().info(
                    f"[ARB SCAN] {len(prices)}/{len(SCAN_EXCHANGES)} feeds live | no gap found"
                )

    def _find_best_gap(self) -> Optional[Dict]:
        best, best_gap = None, Decimal("0")
        entries = list(self._prices.values())
        for buy in entries:
            for sell in entries:
                # connectors must differ — ArbitrageExecutor requires two distinct markets
                if buy["connector"] == sell["connector"]:
                    continue
                # quote assets must match — avoids rate oracle dependency (USDC-USDT conversion)
                if buy["pair"].split("-")[1] != sell["pair"].split("-")[1]:
                    continue
                gap = (sell["bid"] - buy["ask"]) / buy["ask"]
                if gap > best_gap:
                    best_gap = gap
                    best = {
                        "buy_connector": buy["connector"],
                        "buy_pair":      buy["pair"],
                        "buy_ask":       buy["ask"],
                        "sell_connector": sell["connector"],
                        "sell_pair":      sell["pair"],
                        "sell_bid":       sell["bid"],
                        "gap_pct":        float(gap * 100),
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
                self._best["buy_connector"],
                self._best["buy_pair"],
                self.config.order_amount_usd / sol_price,
            )
            cfg = ArbitrageExecutorConfig(
                timestamp=now,
                buying_market=ConnectorPair(
                    connector_name=self._best["buy_connector"],
                    trading_pair=self._best["buy_pair"],
                ),
                selling_market=ConnectorPair(
                    connector_name=self._best["sell_connector"],
                    trading_pair=self._best["sell_pair"],
                ),
                order_amount=amount,
                min_profitability=self.config.min_profitability,
            )
            self._last_executor_ts = now
            self.logger().info(
                f"[ARB ORDER] Creating executor: buy {self._best['buy_connector']} "
                f"({self._best['buy_pair']}) @ {self._best['buy_ask']:.4f} → "
                f"sell {self._best['sell_connector']} ({self._best['sell_pair']}) "
                f"@ {self._best['sell_bid']:.4f} | amount={amount} SOL | gap={self._best['gap_pct']:.4f}%"
            )
            return [CreateExecutorAction(executor_config=cfg, controller_id=self.config.id)]
        except Exception as e:
            self.logger().error(f"Error creating executor: {e}")
            return []

    def to_format_status(self) -> List[str]:
        lines = [
            f"Mode: {self.config.strategy_mode} | Min profit: {self.config.min_profitability:.4%} "
            f"| Exchanges with data: {len(self._prices)}/{len(SCAN_EXCHANGES)}"
        ]
        if self._prices:
            rows = [
                {"Exchange": v["connector"], "Pair": v["pair"],
                 "Bid": f"{v['bid']:.4f}", "Ask": f"{v['ask']:.4f}"}
                for v in sorted(self._prices.values(), key=lambda x: x["bid"], reverse=True)
            ]
            lines.append(format_df_for_printout(pd.DataFrame(rows), table_format="psql"))
        if self._best:
            b = self._best
            lines.append(
                f"\n🎯 BEST GAP: Buy {b['buy_connector']} ({b['buy_pair']}) @ {b['buy_ask']:.4f}"
                f" → Sell {b['sell_connector']} ({b['sell_pair']}) @ {b['sell_bid']:.4f}"
                f" | Gap: {b['gap_pct']:.4f}%"
            )
        else:
            lines.append("\nNo gap found yet (waiting for order book data).")
        return lines
