"""
SOL Multi-Exchange Spread Scanner
- Fetches real SOL order book from 17+ exchanges via public REST APIs (no auth needed)
- Finds the exchange with the largest bid-ask spread (the "culprit")
- Executes the paper trade on cryptocom_paper_trade (real Crypto.com data, simulated execution)
- Your real $14.26 on Crypto.com is never touched
"""
import asyncio
import logging
import os
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import aiohttp
from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

# Public REST endpoints that return best bid/ask for SOL
# Format: (exchange_name, url, bid_path, ask_path)
# bid_path/ask_path: dot-separated keys to navigate the JSON response
EXCHANGE_ENDPOINTS = [
    ("okx",       "https://www.okx.com/api/v5/market/ticker?instId=SOL-USDT",
     "data.0.bidPx", "data.0.askPx"),
    ("kucoin",    "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=SOL-USDT",
     "data.bestBid", "data.bestAsk"),
    ("gate_io",   "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=SOL_USDT",
     "0.highest_bid", "0.lowest_ask"),
    ("kraken",    "https://api.kraken.com/0/public/Ticker?pair=SOLUSDT",
     "result.SOLUSDT.b.0", "result.SOLUSDT.a.0"),
    ("mexc",      "https://api.mexc.com/api/v3/ticker/bookTicker?symbol=SOLUSDT",
     "bidPrice", "askPrice"),
    ("htx",       "https://api.huobi.pro/market/detail/merged?symbol=solusdt",
     "tick.bid.0", "tick.ask.0"),
    ("bitmart",   "https://api-cloud.bitmart.com/spot/v1/ticker?symbol=SOL_USDT",
     "data.tickers.0.best_bid", "data.tickers.0.best_ask"),
    ("hyperliquid", "https://api.hyperliquid.xyz/info",
     "_bid", "_ask"),
    ("ascend_ex", "https://ascendex.com/api/pro/v1/spot/ticker?symbol=SOL/USDT",
     "data.bid.0", "data.ask.0"),
    ("bitrue",    "https://openapi.bitrue.com/api/v1/ticker/bookTicker?symbol=SOLUSDT",
     "bidPrice", "askPrice"),
    ("cryptocom", "https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name=SOL_USD",
     "result.data.0.b", "result.data.0.a"),
]


def _get_nested(obj, path: str):
    """Navigate a dot-separated path through nested dicts/lists."""
    for key in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[int(key)]
            except (IndexError, ValueError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


async def _fetch_spread(session: aiohttp.ClientSession, name: str, url: str,
                        bid_path: str, ask_path: str) -> Optional[Tuple[str, Decimal, Decimal, Decimal]]:
    try:
        # Hyperliquid needs a POST
        if name == "hyperliquid":
            async with session.post(url, json={"type": "allMids"}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json(content_type=None)
            bid = ask = None
            sol_mid = data.get("SOL")
            if sol_mid:
                mid = Decimal(str(sol_mid))
                # Hyperliquid only gives mid; estimate 0.01% spread
                bid = mid * Decimal("0.9999")
                ask = mid * Decimal("1.0001")
        else:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json(content_type=None)
            bid_raw = _get_nested(data, bid_path)
            ask_raw = _get_nested(data, ask_path)
            if bid_raw is None or ask_raw is None:
                return None
            bid = Decimal(str(bid_raw))
            ask = Decimal(str(ask_raw))

        if not bid or not ask or bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / Decimal("2")
        spread_pct = (ask - bid) / mid
        return (name, bid, ask, spread_pct)
    except Exception:
        return None


class SolSpreadScannerConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []

    # Paper trade on Crypto.com — real data, simulated execution
    exchange: str = Field("cryptocom_paper_trade")
    trading_pair: str = Field("SOL-USD")

    order_amount_usd: Decimal = Field(Decimal("14.26"))
    min_spread_pct: Decimal = Field(Decimal("0.002"))
    scan_interval: int = Field(30)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets[self.exchange] = markets.get(self.exchange, set()) | {self.trading_pair}
        return markets


class SolSpreadScanner(StrategyV2Base):

    _next_scan: float = 0.0
    _culprit_count: int = 0
    _total_scans: int = 0
    _last_snapshot: List[Tuple[str, Decimal, Decimal, Decimal]] = []
    _scanning: bool = False

    def __init__(self, connectors: Dict[str, ConnectorBase], config: SolSpreadScannerConfig):
        super().__init__(connectors, config)
        self.config = config

    def on_tick(self):
        if self.current_timestamp < self._next_scan or self._scanning:
            return
        self._next_scan = self.current_timestamp + self.config.scan_interval
        self._scanning = True
        asyncio.ensure_future(self._scan_and_act())

    async def _scan_and_act(self):
        try:
            async with aiohttp.ClientSession() as session:
                tasks = [_fetch_spread(session, n, u, b, a) for n, u, b, a in EXCHANGE_ENDPOINTS]
                results = await asyncio.gather(*tasks)

            snapshot = [r for r in results if r is not None]
            if not snapshot:
                return

            self._last_snapshot = sorted(snapshot, key=lambda x: x[3], reverse=True)
            self._total_scans += 1

            best_name, best_bid, best_ask, best_spread = self._last_snapshot[0]

            if best_spread >= self.config.min_spread_pct:
                self._culprit_count += 1
                msg = (f"CULPRIT: {best_name} spread={best_spread * 100:.3f}% "
                       f"(bid {best_bid:.4f} / ask {best_ask:.4f}) — paper trading on Crypto.com")
                self.log_with_clock(logging.INFO, msg)
                self.notify_hb_app_with_timestamp(msg)
                self._place_paper_orders()
        finally:
            self._scanning = False

    def _place_paper_orders(self):
        """Paper trade on cryptocom_paper_trade — MARKET orders fill immediately."""
        connector = self.connectors[self.config.exchange]
        pair = self.config.trading_pair

        for order in self.get_active_orders(connector_name=self.config.exchange):
            self.cancel(self.config.exchange, pair, order.client_order_id)

        ask = connector.get_price(pair, True)
        if not ask or ask <= 0:
            return

        sol_amount = (self.config.order_amount_usd / ask).quantize(Decimal("0.0001"))
        if sol_amount <= 0:
            return

        # MARKET buy — fills immediately in paper trade
        self.buy(self.config.exchange, pair, sol_amount, OrderType.MARKET, ask)

    def format_status(self) -> str:
        lines = [
            "",
            f"  SOL Spread Scanner — {len(EXCHANGE_ENDPOINTS)} exchanges  "
            f"[paper trade on Crypto.com | ${self.config.order_amount_usd} budget | real $14.26 untouched]",
            f"  Scans: {self._total_scans}  |  Culprits: {self._culprit_count}  "
            f"|  Threshold: {float(self.config.min_spread_pct) * 100:.2f}%",
            "",
            f"  {'Exchange':<22} {'Bid':>10} {'Ask':>10} {'Spread':>9}",
            f"  {'-'*22} {'-'*10} {'-'*10} {'-'*9}",
        ]
        for name, bid, ask, spread_pct in self._last_snapshot[:20]:
            flag = " ◄ CULPRIT" if spread_pct >= self.config.min_spread_pct else ""
            lines.append(
                f"  {name:<22} {float(bid):>10.4f} {float(ask):>10.4f} "
                f"{float(spread_pct) * 100:>8.3f}%{flag}"
            )
        if not self._last_snapshot:
            lines.append("  Scanning...")
        lines.append("")
        return "\n".join(lines)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"[PAPER FILL on Crypto.com] {event.trade_type.name} {event.amount:.4f} SOL @ {event.price:.4f} USD"
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
