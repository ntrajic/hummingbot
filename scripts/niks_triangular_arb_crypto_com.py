"""
niks_triangular_arb_crypto_com.py
==================================
Triangular arbitrage on crypto.com — paper trade mode.

Prices fetched directly from crypto.com public REST API (no order book needed).
Orders placed on crypto_com_paper_trade (simulated fills, real price data).

Triangle routes on SOL / USD / USDT:
  Route A: USD → buy SOL_USD → SOL → sell SOL_USDT → USDT → sell USDT_USD → USD
  Route B: USD → buy USDT_USD → USDT → buy SOL_USDT → SOL → sell SOL_USD → USD

Start:
  start --script niks_triangular_arb_crypto_com.py --conf conf_niks_triangular_arb_crypto_com.yml
"""
import asyncio
import os
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import aiohttp

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

CONNECTOR = "crypto_com_paper_trade"
PAIRS = ["SOL-USD", "SOL-USDT", "USDT-USD"]

# crypto.com instrument names (underscore format)
INSTRUMENTS = {"SOL-USD": "SOL_USD", "SOL-USDT": "SOL_USDT", "USDT-USD": "USDT_USD"}

TICKER_URL = "https://api.crypto.com/exchange/v1/public/get-tickers"

MIN_PROFIT  = Decimal("0")       # paper trade: fire on any positive profit to validate fills
TRADE_USD   = Decimal("10")      # notional per cycle
COOLDOWN    = 30                 # seconds between cycles


class NiksTriangularArbConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []

    def update_markets(self, markets: Dict) -> Dict:
        markets[CONNECTOR] = set(PAIRS)
        return markets


class NiksTriangularArb(StrategyV2Base):

    def __init__(self, connectors: Dict[str, ConnectorBase], config: NiksTriangularArbConfig):
        super().__init__(connectors, config)
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0
        self._prices: Dict[str, Dict[str, Decimal]] = {}
        self._profit_a: Decimal = Decimal("0")
        self._profit_b: Decimal = Decimal("0")
        self._fetch_task: Optional[asyncio.Task] = None

    def create_actions_proposal(self): return []
    def stop_actions_proposal(self):   return []

    # Override tick to bypass ready_to_trade — we use REST prices, not order book
    def tick(self, timestamp: float):
        self.on_tick()

    # ── Tick ──────────────────────────────────────────────────────────────────

    def on_tick(self):
        # Kick off async price fetch if not already running
        if self._fetch_task is None or self._fetch_task.done():
            self._fetch_task = asyncio.ensure_future(self._fetch_and_trade())

    async def _fetch_and_trade(self):
        prices = await self._fetch_prices_rest()
        if not prices:
            return
        self._prices = prices
        self._profit_a, self._profit_b = self._compute_profits(prices)

        now = time.time()
        if now - self._last_trade_ts < COOLDOWN:
            return

        if self._profit_a >= MIN_PROFIT:
            self._execute_route_a(prices)
            self._last_trade_ts = now
        elif self._profit_b >= MIN_PROFIT:
            self._execute_route_b(prices)
            self._last_trade_ts = now

    # ── REST price fetch ──────────────────────────────────────────────────────

    async def _fetch_prices_rest(self) -> Optional[Dict[str, Dict[str, Decimal]]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(TICKER_URL, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    data = await resp.json()
            tickers = {t["i"]: t for t in data.get("result", {}).get("data", [])}
            prices = {}
            for pair, instrument in INSTRUMENTS.items():
                t = tickers.get(instrument)
                if not t:
                    return None
                bid = Decimal(str(t["b"]))
                ask = Decimal(str(t["k"]))
                if bid <= 0 or ask <= 0:
                    return None
                prices[pair] = {"bid": bid, "ask": ask}
            return prices
        except Exception as e:
            self.logger().debug(f"Price fetch error: {e}")
            return None

    # ── Profit calculation ────────────────────────────────────────────────────

    def _compute_profits(self, p: Dict) -> Tuple[Decimal, Decimal]:
        # Route A: 1 USD → SOL → USDT → USD
        sol_a   = Decimal("1") / p["SOL-USD"]["ask"]
        usdt_a  = sol_a * p["SOL-USDT"]["bid"]
        final_a = usdt_a * p["USDT-USD"]["bid"]

        # Route B: 1 USD → USDT → SOL → USD
        usdt_b  = Decimal("1") / p["USDT-USD"]["ask"]
        sol_b   = usdt_b / p["SOL-USDT"]["ask"]
        final_b = sol_b * p["SOL-USD"]["bid"]

        return final_a - Decimal("1"), final_b - Decimal("1")

    # ── Order execution ───────────────────────────────────────────────────────

    def _execute_route_a(self, p: Dict):
        sol_amt  = round(TRADE_USD / p["SOL-USD"]["ask"], 4)
        usdt_amt = round(sol_amt * p["SOL-USDT"]["bid"], 4)
        self._trade_count += 1
        self.logger().info(
            f"[TRI #{self._trade_count} A] {float(self._profit_a*100):+.4f}% | "
            f"BUY {sol_amt} SOL@{p['SOL-USD']['ask']} USD | "
            f"SELL {sol_amt} SOL@{p['SOL-USDT']['bid']} USDT | "
            f"SELL {usdt_amt} USDT@{p['USDT-USD']['bid']} USD"
        )
        try:
            self.buy( CONNECTOR, "SOL-USD",  sol_amt,  OrderType.MARKET, p["SOL-USD"]["ask"])
            self.sell(CONNECTOR, "SOL-USDT", sol_amt,  OrderType.MARKET, p["SOL-USDT"]["bid"])
            self.sell(CONNECTOR, "USDT-USD", usdt_amt, OrderType.MARKET, p["USDT-USD"]["bid"])
        except Exception as e:
            self.logger().error(f"[TRI #{self._trade_count} A] failed: {e}")

    def _execute_route_b(self, p: Dict):
        usdt_amt = round(TRADE_USD / p["USDT-USD"]["ask"], 4)
        sol_amt  = round(usdt_amt / p["SOL-USDT"]["ask"], 4)
        self._trade_count += 1
        self.logger().info(
            f"[TRI #{self._trade_count} B] {float(self._profit_b*100):+.4f}% | "
            f"BUY {usdt_amt} USDT@{p['USDT-USD']['ask']} USD | "
            f"BUY {sol_amt} SOL@{p['SOL-USDT']['ask']} USDT | "
            f"SELL {sol_amt} SOL@{p['SOL-USD']['bid']} USD"
        )
        try:
            self.buy( CONNECTOR, "USDT-USD", usdt_amt, OrderType.MARKET, p["USDT-USD"]["ask"])
            self.buy( CONNECTOR, "SOL-USDT", sol_amt,  OrderType.MARKET, p["SOL-USDT"]["ask"])
            self.sell(CONNECTOR, "SOL-USD",  sol_amt,  OrderType.MARKET, p["SOL-USD"]["bid"])
        except Exception as e:
            self.logger().error(f"[TRI #{self._trade_count} B] failed: {e}")

    # ── Status ────────────────────────────────────────────────────────────────

    def format_status(self) -> str:
        lines = ["", "  Balances (crypto_com_paper_trade):"]
        try:
            connector = self.connectors[CONNECTOR]
            for asset in ["USD", "SOL", "USDT"]:
                bal = connector.get_balance(asset)
                lines.append(f"    {asset:6s}: {float(bal):.4f}")
        except Exception:
            lines.append("    (not yet available)")
        lines += ["", f"  Cycles executed: {self._trade_count} | Threshold: {float(MIN_PROFIT*100):.2f}%"]
        if self._prices:
            p = self._prices
            lines += ["", "  Live prices (crypto.com REST):"]
            for pair in PAIRS:
                lines.append(f"    {pair:12s}  bid={p[pair]['bid']:.6f}  ask={p[pair]['ask']:.6f}")
            lines += [
                "",
                f"  Route A (USD→SOL→USDT→USD): {float(self._profit_a*100):+.4f}%",
                f"  Route B (USD→USDT→SOL→USD): {float(self._profit_b*100):+.4f}%",
                f"  {'🟢 FIRING' if max(self._profit_a, self._profit_b) >= MIN_PROFIT else '🔴 below threshold'}",
            ]
        else:
            lines += ["", "  Waiting for price data..."]
        return "\n".join(lines)
