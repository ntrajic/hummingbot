"""
niks_triangular_arb_crypto_com.py
==================================
Triangular arbitrage on crypto.com — paper trade simulation.

Prices: fetched from crypto.com public REST API every tick.
Orders: simulated in-memory (paper trade connector rejects orders because
        _NullOrderBookDataSource never populates _trading_pairs).

Triangle routes on SOL / USD / USDT:
  Route A: USD → buy SOL_USD → SOL → sell SOL_USDT → USDT → sell USDT_USD → USD
  Route B: USD → buy USDT_USD → USDT → buy SOL_USDT → SOL → sell SOL_USD → USD

`status`  — shows live prices, profit %, simulated balances, trade count
`history` — use `tail -f logs/logs_conf_niks_triangular_arb_crypto_com.log | grep TRI`

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
INSTRUMENTS = {"SOL-USD": "SOL_USD", "SOL-USDT": "SOL_USDT", "USDT-USD": "USDT_USD"}
TICKER_URL = "https://api.crypto.com/exchange/v1/public/get-tickers"

MIN_PROFIT = Decimal("0")     # fire on any positive profit (paper trade demo)
TRADE_USD  = Decimal("10")
COOLDOWN   = 30


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
        self._profit_a = Decimal("0")
        self._profit_b = Decimal("0")
        self._fetch_task: Optional[asyncio.Task] = None
        # Simulated balances (paper trade)
        self._bal = {"USD": Decimal("14.26"), "SOL": Decimal("0"), "USDT": Decimal("0")}
        self._pnl = Decimal("0")   # cumulative USD profit

    def create_actions_proposal(self): return []
    def stop_actions_proposal(self):   return []

    def tick(self, timestamp: float):
        # Bypass ready_to_trade — prices come from REST, not order book
        if self._fetch_task is None or self._fetch_task.done():
            self._fetch_task = asyncio.ensure_future(self._fetch_and_trade())

    async def _fetch_and_trade(self):
        prices = await self._fetch_prices_rest()
        if not prices:
            return
        self._prices = prices
        self._profit_a, self._profit_b = self._compute_profits(prices)

        if time.time() - self._last_trade_ts < COOLDOWN:
            return

        if self._profit_a >= self._profit_b and self._bal["USD"] >= TRADE_USD:
            self._simulate_route_a(prices)
            self._last_trade_ts = time.time()
        elif self._profit_b > self._profit_a and self._bal["USD"] >= TRADE_USD:
            self._simulate_route_b(prices)
            self._last_trade_ts = time.time()

    async def _fetch_prices_rest(self) -> Optional[Dict]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(TICKER_URL, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    data = await r.json()
            tickers = {t["i"]: t for t in data.get("result", {}).get("data", [])}
            prices = {}
            for pair, inst in INSTRUMENTS.items():
                t = tickers.get(inst)
                if not t:
                    return None
                prices[pair] = {"bid": Decimal(str(t["b"])), "ask": Decimal(str(t["k"]))}
            return prices
        except Exception as e:
            self.logger().debug(f"REST fetch error: {e}")
            return None

    def _compute_profits(self, p: Dict) -> Tuple[Decimal, Decimal]:
        sol_a  = Decimal("1") / p["SOL-USD"]["ask"]
        usdt_a = sol_a * p["SOL-USDT"]["bid"]
        pa     = usdt_a * p["USDT-USD"]["bid"] - Decimal("1")

        usdt_b = Decimal("1") / p["USDT-USD"]["ask"]
        sol_b  = usdt_b / p["SOL-USDT"]["ask"]
        pb     = sol_b * p["SOL-USD"]["bid"] - Decimal("1")
        return pa, pb

    def _simulate_route_a(self, p: Dict):
        """USD → SOL → USDT → USD"""
        usd_in   = TRADE_USD
        sol      = usd_in / p["SOL-USD"]["ask"]
        usdt     = sol * p["SOL-USDT"]["bid"]
        usd_out  = usdt * p["USDT-USD"]["bid"]
        profit   = usd_out - usd_in

        self._bal["USD"] += profit
        self._pnl        += profit
        self._trade_count += 1

        self.logger().info(
            f"[TRI #{self._trade_count} A] profit={float(profit):.6f} USD ({float(self._profit_a*100):+.4f}%) | "
            f"USD→{float(sol):.4f}SOL→{float(usdt):.4f}USDT→{float(usd_out):.4f}USD | "
            f"balance USD={float(self._bal['USD']):.4f} | cumPnL={float(self._pnl):.6f}"
        )

    def _simulate_route_b(self, p: Dict):
        """USD → USDT → SOL → USD"""
        usd_in   = TRADE_USD
        usdt     = usd_in / p["USDT-USD"]["ask"]
        sol      = usdt / p["SOL-USDT"]["ask"]
        usd_out  = sol * p["SOL-USD"]["bid"]
        profit   = usd_out - usd_in

        self._bal["USD"] += profit
        self._pnl        += profit
        self._trade_count += 1

        self.logger().info(
            f"[TRI #{self._trade_count} B] profit={float(profit):.6f} USD ({float(self._profit_b*100):+.4f}%) | "
            f"USD→{float(usdt):.4f}USDT→{float(sol):.4f}SOL→{float(usd_out):.4f}USD | "
            f"balance USD={float(self._bal['USD']):.4f} | cumPnL={float(self._pnl):.6f}"
        )

    def format_status(self) -> str:
        lines = [
            "",
            "  Simulated Balances:",
            f"    USD : {float(self._bal['USD']):.4f}",
            f"    Cumulative PnL: {float(self._pnl):+.6f} USD",
            "",
            f"  Cycles: {self._trade_count} | Threshold: {float(MIN_PROFIT*100):.2f}% | Cooldown: {COOLDOWN}s",
        ]
        if self._prices:
            p = self._prices
            lines += ["", "  Live prices (crypto.com REST):"]
            for pair in PAIRS:
                lines.append(f"    {pair:12s}  bid={p[pair]['bid']:.6f}  ask={p[pair]['ask']:.6f}")
            lines += [
                "",
                f"  Route A (USD→SOL→USDT→USD): {float(self._profit_a*100):+.4f}%",
                f"  Route B (USD→USDT→SOL→USD): {float(self._profit_b*100):+.4f}%",
                f"  {'🟢 WILL FIRE' if max(self._profit_a, self._profit_b) > MIN_PROFIT else '🔴 both negative'}",
            ]
        else:
            lines.append("  Fetching prices...")
        return "\n".join(lines)
