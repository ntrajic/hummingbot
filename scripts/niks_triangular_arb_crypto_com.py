"""
niks_triangular_arb_crypto_com.py
==================================
Triangular arbitrage on crypto.com using three pairs on a single exchange:

  Route A (forward):  USD  → buy SOL_USD  → SOL → sell SOL_USDT → USDT → sell USDT_USD → USD
  Route B (reverse):  USD  → buy USDT_USD → USDT → buy SOL_USDT  → SOL → sell SOL_USD  → USD

All three legs execute as MARKET orders on crypto_com_paper_trade.
No second exchange, no transfers, no rate-oracle dependency.

Start:
  start --script niks_triangular_arb_crypto_com.py --conf conf_niks_triangular_arb_crypto_com.yml

Pairs used (crypto.com instrument names → hummingbot pairs):
  SOL_USD  → SOL-USD
  SOL_USDT → SOL-USDT
  USDT_USD → USDT-USD
"""
import os
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

CONNECTOR = "crypto_com_paper_trade"
PAIRS = ["SOL-USD", "SOL-USDT", "USDT-USD"]

# Minimum profit after fees to fire (0.2% = covers taker fees with margin)
MIN_PROFIT = Decimal("0.002")

# USD notional per triangle cycle
TRADE_AMOUNT_USD = Decimal("10")

# Seconds between consecutive triangles
COOLDOWN = 30


class NiksTriangularArbConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []

    def update_markets(self, markets: Dict) -> Dict:
        markets[CONNECTOR] = set(PAIRS)
        return markets


class NiksTriangularArb(StrategyV2Base):
    """
    Each tick:
      1. Fetch best bid/ask for SOL-USD, SOL-USDT, USDT-USD from crypto_com_paper_trade.
      2. Compute profit for Route A and Route B.
      3. If best route profit >= MIN_PROFIT, place all three market orders sequentially.
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: NiksTriangularArbConfig):
        super().__init__(connectors, config)
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0
        self._last_prices: Dict[str, Dict[str, Decimal]] = {}
        self._last_profit_a: Decimal = Decimal("0")
        self._last_profit_b: Decimal = Decimal("0")

    def create_actions_proposal(self):
        return []

    def stop_actions_proposal(self):
        return []

    # ── Main tick ─────────────────────────────────────────────────────────────

    def on_tick(self):
        prices = self._fetch_prices()
        if not prices:
            return
        self._last_prices = prices

        profit_a, profit_b = self._compute_profits(prices)
        self._last_profit_a = profit_a
        self._last_profit_b = profit_b

        now = time.time()
        if now - self._last_trade_ts < COOLDOWN:
            return

        if profit_a >= MIN_PROFIT:
            self._execute_route_a(prices)
            self._last_trade_ts = now
        elif profit_b >= MIN_PROFIT:
            self._execute_route_b(prices)
            self._last_trade_ts = now

    # ── Price fetching ────────────────────────────────────────────────────────

    def _fetch_prices(self) -> Optional[Dict[str, Dict[str, Decimal]]]:
        prices = {}
        try:
            for pair in PAIRS:
                bid = self.market_data_provider.get_price_by_type(CONNECTOR, pair, PriceType.BestBid)
                ask = self.market_data_provider.get_price_by_type(CONNECTOR, pair, PriceType.BestAsk)
                if not bid or not ask or bid <= 0 or ask <= 0:
                    return None
                prices[pair] = {"bid": bid, "ask": ask}
        except Exception as e:
            self.logger().debug(f"Price fetch error: {e}")
            return None
        return prices

    # ── Profit calculation ────────────────────────────────────────────────────

    def _compute_profits(self, p: Dict) -> Tuple[Decimal, Decimal]:
        """
        Route A: spend 1 USD
          step1: buy SOL with USD  → get SOL = 1 / ask(SOL-USD)
          step2: sell SOL for USDT → get USDT = SOL * bid(SOL-USDT)
          step3: sell USDT for USD → get USD = USDT * bid(USDT-USD)
          profit_a = final_usd - 1

        Route B: spend 1 USD
          step1: buy USDT with USD  → get USDT = 1 / ask(USDT-USD)
          step2: buy SOL with USDT  → get SOL = USDT / ask(SOL-USDT)
          step3: sell SOL for USD   → get USD = SOL * bid(SOL-USD)
          profit_b = final_usd - 1
        """
        sol_ask_usd  = p["SOL-USD"]["ask"]
        sol_bid_usd  = p["SOL-USD"]["bid"]
        sol_ask_usdt = p["SOL-USDT"]["ask"]
        sol_bid_usdt = p["SOL-USDT"]["bid"]
        usdt_ask_usd = p["USDT-USD"]["ask"]
        usdt_bid_usd = p["USDT-USD"]["bid"]

        # Route A
        sol_a    = Decimal("1") / sol_ask_usd
        usdt_a   = sol_a * sol_bid_usdt
        final_a  = usdt_a * usdt_bid_usd
        profit_a = final_a - Decimal("1")

        # Route B
        usdt_b   = Decimal("1") / usdt_ask_usd
        sol_b    = usdt_b / sol_ask_usdt
        final_b  = sol_b * sol_bid_usd
        profit_b = final_b - Decimal("1")

        return profit_a, profit_b

    # ── Order execution ───────────────────────────────────────────────────────

    def _execute_route_a(self, p: Dict):
        """
        Route A: USD → SOL → USDT → USD
          1. BUY  SOL-USD  (spend USD, get SOL)
          2. SELL SOL-USDT (spend SOL, get USDT)
          3. SELL USDT-USD (spend USDT, get USD)
        """
        sol_amount  = round(TRADE_AMOUNT_USD / p["SOL-USD"]["ask"], 4)
        usdt_amount = round(sol_amount * p["SOL-USDT"]["bid"], 4)

        self._trade_count += 1
        n = self._trade_count
        profit_pct = float(self._last_profit_a * 100)

        self.logger().info(
            f"[TRI #{n} Route-A] profit={profit_pct:.4f}% | "
            f"BUY {sol_amount} SOL @ {p['SOL-USD']['ask']} USD | "
            f"SELL {sol_amount} SOL @ {p['SOL-USDT']['bid']} USDT | "
            f"SELL {usdt_amount} USDT @ {p['USDT-USD']['bid']} USD"
        )
        try:
            self.buy(CONNECTOR,  "SOL-USD",  sol_amount,  OrderType.MARKET, p["SOL-USD"]["ask"])
            self.sell(CONNECTOR, "SOL-USDT", sol_amount,  OrderType.MARKET, p["SOL-USDT"]["bid"])
            self.sell(CONNECTOR, "USDT-USD", usdt_amount, OrderType.MARKET, p["USDT-USD"]["bid"])
        except Exception as e:
            self.logger().error(f"[TRI #{n} Route-A] Order failed: {e}")

    def _execute_route_b(self, p: Dict):
        """
        Route B: USD → USDT → SOL → USD
          1. BUY  USDT-USD (spend USD, get USDT)  — i.e. SELL USD for USDT
          2. BUY  SOL-USDT (spend USDT, get SOL)
          3. SELL SOL-USD  (spend SOL, get USD)
        """
        usdt_amount = round(TRADE_AMOUNT_USD / p["USDT-USD"]["ask"], 4)
        sol_amount  = round(usdt_amount / p["SOL-USDT"]["ask"], 4)

        self._trade_count += 1
        n = self._trade_count
        profit_pct = float(self._last_profit_b * 100)

        self.logger().info(
            f"[TRI #{n} Route-B] profit={profit_pct:.4f}% | "
            f"BUY {usdt_amount} USDT @ {p['USDT-USD']['ask']} USD | "
            f"BUY {sol_amount} SOL @ {p['SOL-USDT']['ask']} USDT | "
            f"SELL {sol_amount} SOL @ {p['SOL-USD']['bid']} USD"
        )
        try:
            self.buy(CONNECTOR,  "USDT-USD", usdt_amount, OrderType.MARKET, p["USDT-USD"]["ask"])
            self.buy(CONNECTOR,  "SOL-USDT", sol_amount,  OrderType.MARKET, p["SOL-USDT"]["ask"])
            self.sell(CONNECTOR, "SOL-USD",  sol_amount,  OrderType.MARKET, p["SOL-USD"]["bid"])
        except Exception as e:
            self.logger().error(f"[TRI #{n} Route-B] Order failed: {e}")

    # ── Status display ────────────────────────────────────────────────────────

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Connectors not ready."

        lines = ["", "  Balances:"]
        lines += ["    " + l for l in self.get_balance_df().to_string(index=False).split("\n")]

        lines += ["", f"  Triangular arb cycles executed: {self._trade_count}"]
        lines += [f"  Min profit threshold: {float(MIN_PROFIT*100):.2f}%"]

        if self._last_prices:
            p = self._last_prices
            lines += ["", "  Live prices (crypto_com_paper_trade):"]
            for pair in PAIRS:
                if pair in p:
                    lines.append(f"    {pair:12s}  bid={p[pair]['bid']:.6f}  ask={p[pair]['ask']:.6f}")

            lines += [
                "",
                f"  Route A (USD→SOL→USDT→USD): {float(self._last_profit_a*100):+.4f}%",
                f"  Route B (USD→USDT→SOL→USD): {float(self._last_profit_b*100):+.4f}%",
                f"  Threshold: {float(MIN_PROFIT*100):.2f}%  |  "
                f"{'🟢 FIRING' if max(self._last_profit_a, self._last_profit_b) >= MIN_PROFIT else '🔴 below threshold'}",
            ]

        return "\n".join(lines)
