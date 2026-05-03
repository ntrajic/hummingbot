"""
niks_sol_arb.py  —  SOL arbitrage scanner + direct paper-trade order placement

Scans 14 paper-trade feeds, finds best same-quote gap >= 0.01%,
places simultaneous market buy + sell directly via strategy.buy/sell.
Trades appear in `status` balances and `history` immediately.
"""
import os
import time
from decimal import Decimal
from typing import Dict, List, Optional

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.data_type.common import PriceType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction

# Same exchange list as the controller
SCAN_EXCHANGES = [
    ("okx_paper_trade",     "SOL-USDC"),
    ("okx_paper_trade",     "SOL-USDT"),
    ("kucoin_paper_trade",  "SOL-USDC"),
    ("kucoin_paper_trade",  "SOL-USDT"),
    ("gate_io_paper_trade", "SOL-USDC"),
    ("gate_io_paper_trade", "SOL-USDT"),
    ("bitget_paper_trade",  "SOL-USDC"),
    ("bitget_paper_trade",  "SOL-USDT"),
    ("mexc_paper_trade",    "SOL-USDC"),
    ("mexc_paper_trade",    "SOL-USDT"),
    ("htx_paper_trade",     "SOL-USDT"),
    ("kraken_paper_trade",  "SOL-USDC"),
    ("kraken_paper_trade",  "SOL-USDT"),
    ("bitmart_paper_trade", "SOL-USDT"),
]

MIN_PROFITABILITY = Decimal("0.0001")   # 0.01%
ORDER_AMOUNT_USD  = Decimal("10")
COOLDOWN_SECONDS  = 15


class NiksSolArbScriptConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []   # no controller needed — logic is here

    def update_markets(self, markets):
        for connector, pair in SCAN_EXCHANGES:
            markets[connector] = markets.get(connector, set()) | {pair}
        return markets


class NiksSolArbScript(StrategyV2Base):

    def __init__(self, connectors: Dict[str, ConnectorBase], config: NiksSolArbScriptConfig):
        super().__init__(connectors, config)
        self._last_order_ts: float = 0.0
        self._prices: Dict[str, Dict] = {}
        self._best: Optional[Dict] = None
        self._trade_count: int = 0

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        return []

    def on_tick(self):
        self._scan_prices()
        self._try_place_orders()

    def _scan_prices(self):
        prices = {}
        for connector, pair in SCAN_EXCHANGES:
            try:
                bid = self.market_data_provider.get_price_by_type(connector, pair, PriceType.BestBid)
                ask = self.market_data_provider.get_price_by_type(connector, pair, PriceType.BestAsk)
                if bid and ask and bid > 0 and ask > 0:
                    prices[f"{connector}:{pair}"] = {
                        "connector": connector, "pair": pair, "bid": bid, "ask": ask
                    }
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
                # same quote asset — no rate oracle needed
                if buy["pair"].split("-")[1] != sell["pair"].split("-")[1]:
                    continue
                gap = (sell["bid"] - buy["ask"]) / buy["ask"]
                if gap > best_gap:
                    best_gap = gap
                    best = {
                        "buy_connector":  buy["connector"],
                        "buy_pair":       buy["pair"],
                        "buy_ask":        buy["ask"],
                        "sell_connector": sell["connector"],
                        "sell_pair":      sell["pair"],
                        "sell_bid":       sell["bid"],
                        "gap_pct":        float(gap * 100),
                    }
        return best

    def _try_place_orders(self):
        if not self._best:
            return
        gap = Decimal(str(self._best["gap_pct"])) / 100
        if gap < MIN_PROFITABILITY:
            return
        now = time.time()
        if now - self._last_order_ts < COOLDOWN_SECONDS:
            return

        b = self._best
        sol_price = b["buy_ask"]
        amount = round(ORDER_AMOUNT_USD / sol_price, 4)
        if amount <= 0:
            return

        try:
            buy_id = self.buy(
                connector_name=b["buy_connector"],
                trading_pair=b["buy_pair"],
                amount=amount,
                order_type=OrderType.MARKET,
                price=sol_price,
            )
            sell_id = self.sell(
                connector_name=b["sell_connector"],
                trading_pair=b["sell_pair"],
                amount=amount,
                order_type=OrderType.MARKET,
                price=b["sell_bid"],
            )
            self._last_order_ts = now
            self._trade_count += 1
            self.logger().info(
                f"[ARB #{self._trade_count}] "
                f"BUY  {b['buy_connector']} {b['buy_pair']} @ {b['buy_ask']:.4f} | "
                f"SELL {b['sell_connector']} {b['sell_pair']} @ {b['sell_bid']:.4f} | "
                f"amount={amount} SOL | gap={b['gap_pct']:.4f}% | "
                f"buy_id={buy_id} sell_id={sell_id}"
            )
        except Exception as e:
            self.logger().error(f"[ARB] Order placement failed: {e}")

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        lines = ["", "  Balances:"]
        balance_df = self.get_balance_df()
        lines += ["    " + l for l in balance_df.to_string(index=False).split("\n")]

        lines += ["", f"  Arbitrage trades placed this session: {self._trade_count}"]

        if self._prices:
            lines += ["", f"  Live prices ({len(self._prices)}/{len(SCAN_EXCHANGES)} feeds):"]
            for v in sorted(self._prices.values(), key=lambda x: x["bid"], reverse=True):
                lines.append(f"    {v['connector']:30s} {v['pair']}  bid={v['bid']:.4f}  ask={v['ask']:.4f}")

        if self._best:
            b = self._best
            lines += [
                "",
                f"  🎯 BEST GAP: Buy {b['buy_connector']} ({b['buy_pair']}) @ {b['buy_ask']:.4f}"
                f" → Sell {b['sell_connector']} ({b['sell_pair']}) @ {b['sell_bid']:.4f}"
                f" | Gap: {b['gap_pct']:.4f}%"
            ]
        else:
            lines += ["", "  No gap found."]

        return "\n".join(lines)
