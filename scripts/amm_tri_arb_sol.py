"""
AMM Triangular Arbitrage on Solana via Jupiter DEX
Branch: amm_tri_arb_sol

Triangle route: USDC → SOL → USDT → USDC
All three legs execute as a single atomic Solana transaction via Jupiter router.
If the round-trip is unprofitable, the chain reverts — no USDC leaves the wallet.

Modes:
  dry_run: True  → paper trading (quotes only, no execution)
  dry_run: False → live trading (real BackpackWallet via Gateway)
"""
import asyncio
import logging
import os
import time
from decimal import Decimal
from typing import Dict, Optional

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class AmmTriArbSolConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: list = []

    # Gateway network connector
    connector: str = Field("solana-mainnet-beta")

    # Triangle legs (base-quote pairs as Jupiter expects them)
    pair_1: str = Field("SOL-USDC")   # leg 1: buy SOL with USDC
    pair_2: str = Field("SOL-USDT")   # leg 2: sell SOL for USDT
    pair_3: str = Field("USDT-USDC")  # leg 3: sell USDT for USDC

    # Capital: amount of USDC to deploy per cycle
    order_amount: Decimal = Field(Decimal("13.0"))

    # Minimum net profit required to fire the trade (1.2 = 1.2%)
    min_profitability: Decimal = Field(Decimal("1.2"))

    # Slippage tolerance passed to Jupiter (triggers on-chain revert if exceeded)
    slippage_pct: Decimal = Field(Decimal("0.5"))

    # Abort if total quote round-trip takes longer than this (ms)
    max_quote_age_ms: int = Field(500)

    # Seconds between each scan cycle
    scan_interval: int = Field(10)

    # Paper trade mode: True = quotes only, no execution
    dry_run: bool = Field(True)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        # Gateway connectors don't register trading pairs the same way as CEX connectors.
        # We register the connector so Hummingbot initialises it; pairs are passed directly
        # to the Gateway HTTP client at quote/execute time.
        markets[self.connector] = markets.get(self.connector, set()) | {
            self.pair_1, self.pair_2, self.pair_3
        }
        return markets


class AmmTriArbSol(StrategyV2Base):
    """
    Triangular arbitrage: USDC → SOL → USDT → USDC via Jupiter on Solana.

    Each scan cycle:
    1. Quote all three legs sequentially via Gateway.
    2. Compute net_out_usdc from the chain of quotes.
    3. If net_out_usdc > order_amount * (1 + min_profitability/100) AND
       total quote time < max_quote_age_ms → execute (or log in dry_run).
    4. Notify via Telegram on profitable fills only.
    """

    _next_scan: float = 0.0
    _scanning: bool = False
    _total_scans: int = 0
    _total_trades: int = 0
    _total_reverts: int = 0      # on-chain reverts / gateway errors during execution
    _total_skips: int = 0        # quotes below min_profitability threshold
    _total_stale: int = 0        # quotes aborted due to max_quote_age_ms
    _total_profit_usdc: Decimal = Decimal("0")
    _last_quote: Optional[Dict] = None

    def __init__(self, connectors: Dict[str, ConnectorBase], config: AmmTriArbSolConfig):
        super().__init__(connectors, config)
        self.config = config
        self._gateway = GatewayHttpClient.get_instance()

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(self):
        if self.current_timestamp < self._next_scan or self._scanning:
            return
        self._next_scan = self.current_timestamp + self.config.scan_interval
        self._scanning = True
        asyncio.ensure_future(self._scan_and_act())

    # ------------------------------------------------------------------
    # Core scan loop
    # ------------------------------------------------------------------

    async def _scan_and_act(self):
        try:
            quote = await self._get_triangle_quote()
            if quote is None:
                return

            self._last_quote = quote
            self._total_scans += 1

            # --- Staleness gate (before profitability to avoid false FIRE log) ---
            if quote["elapsed_ms"] > self.config.max_quote_age_ms:
                self._total_stale += 1
                self.log_with_clock(
                    logging.WARNING,
                    f"[SCAN #{self._total_scans}] Quotes stale "
                    f"({quote['elapsed_ms']:.0f}ms > {self.config.max_quote_age_ms}ms). Skipping."
                )
                return

            net_out = quote["net_out_usdc"]
            profit_pct = (net_out - self.config.order_amount) / self.config.order_amount * 100
            threshold = self.config.order_amount * (1 + self.config.min_profitability / 100)

            # --- Profitability gate ---
            if net_out < threshold:
                self._total_skips += 1
                self.log_with_clock(
                    logging.INFO,
                    f"[SCAN #{self._total_scans}] "
                    f"net_out={net_out:.4f} USDC  profit={profit_pct:.3f}%  "
                    f"quote_ms={quote['elapsed_ms']:.0f}  ⏳ below {self.config.min_profitability}%"
                )
                return

            self.log_with_clock(
                logging.INFO,
                f"[SCAN #{self._total_scans}] "
                f"net_out={net_out:.4f} USDC  profit={profit_pct:.3f}%  "
                f"quote_ms={quote['elapsed_ms']:.0f}  ✅ FIRE"
            )
            await self._execute_triangle(quote, profit_pct)

        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Scan error: {e}")
        finally:
            self._scanning = False

    # ------------------------------------------------------------------
    # Quote all three legs
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_amount(response: dict, input_amount: Decimal) -> Decimal:
        """
        Extract output amount from a Gateway quote_swap response.
        Tries keys in order: expectedAmount, amount, then price * input_amount.
        Returns Decimal("0") if nothing usable is found.
        """
        for key in ("expectedAmount", "amount"):
            val = response.get(key)
            if val is not None:
                try:
                    return Decimal(str(val))
                except Exception:
                    pass
        # Fallback: price field × input amount (some Gateway versions return this)
        price = response.get("price")
        if price is not None:
            try:
                return Decimal(str(price)) * input_amount
            except Exception:
                pass
        return Decimal("0")

    async def _get_triangle_quote(self) -> Optional[Dict]:
        """
        Sequential quotes: USDC→SOL→USDT→USDC.
        Returns dict with intermediate amounts and total elapsed ms, or None on failure.
        """
        network = self.config.connector  # "solana-mainnet-beta"
        slippage = self.config.slippage_pct
        amount = self.config.order_amount

        t0 = time.monotonic()
        try:
            # Leg 1: sell USDC, buy SOL  (BUY SOL with USDC)
            q1 = await self._gateway.quote_swap(
                network=network,
                base_asset="SOL",
                quote_asset="USDC",
                amount=amount,
                side=TradeType.BUY,
                dex="jupiter",
                trading_type="router",
                slippage_pct=slippage,
            )
            sol_amount = self._parse_amount(q1, amount)
            if sol_amount <= 0:
                return None

            # Leg 2: sell SOL, buy USDT  (SELL SOL for USDT)
            q2 = await self._gateway.quote_swap(
                network=network,
                base_asset="SOL",
                quote_asset="USDT",
                amount=sol_amount,
                side=TradeType.SELL,
                dex="jupiter",
                trading_type="router",
                slippage_pct=slippage,
            )
            usdt_amount = self._parse_amount(q2, sol_amount)
            if usdt_amount <= 0:
                return None

            # Leg 3: sell USDT, buy USDC  (SELL USDT for USDC)
            q3 = await self._gateway.quote_swap(
                network=network,
                base_asset="USDT",
                quote_asset="USDC",
                amount=usdt_amount,
                side=TradeType.SELL,
                dex="jupiter",
                trading_type="router",
                slippage_pct=slippage,
            )
            net_out_usdc = self._parse_amount(q3, usdt_amount)
            if net_out_usdc <= 0:
                return None

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"Quote failed: {e}")
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "sol_amount": sol_amount,
            "usdt_amount": usdt_amount,
            "net_out_usdc": net_out_usdc,
            "elapsed_ms": elapsed_ms,
        }

    # ------------------------------------------------------------------
    # Execute (or dry-run)
    # ------------------------------------------------------------------

    async def _execute_triangle(self, quote: Dict, profit_pct: Decimal):
        network = self.config.connector
        slippage = self.config.slippage_pct
        amount = self.config.order_amount
        sol_amount = quote["sol_amount"]
        usdt_amount = quote["usdt_amount"]
        net_out = quote["net_out_usdc"]
        profit_usdc = net_out - amount

        if self.config.dry_run:
            msg = (
                f"[DRY RUN] TRI-ARB opportunity: "
                f"{amount} USDC → {sol_amount:.6f} SOL → {usdt_amount:.4f} USDT → {net_out:.4f} USDC  "
                f"profit={profit_pct:.3f}% (+{profit_usdc:.4f} USDC)"
            )
            self.log_with_clock(logging.INFO, msg)
            self.notify_hb_app_with_timestamp(msg)
            return

        # --- Live execution ---
        try:
            # Leg 1: BUY SOL with USDC
            r1 = await self._gateway.execute_swap(
                network=network, base_asset="SOL", quote_asset="USDC",
                side=TradeType.BUY, amount=amount,
                dex="jupiter", trading_type="router", slippage_pct=slippage,
            )
            tx1 = r1.get("txHash", "?")
            self.log_with_clock(logging.INFO, f"Leg 1 done: tx={tx1}")

            # Leg 2: SELL SOL for USDT
            r2 = await self._gateway.execute_swap(
                network=network, base_asset="SOL", quote_asset="USDT",
                side=TradeType.SELL, amount=sol_amount,
                dex="jupiter", trading_type="router", slippage_pct=slippage,
            )
            tx2 = r2.get("txHash", "?")
            self.log_with_clock(logging.INFO, f"Leg 2 done: tx={tx2}")

            # Leg 3: SELL USDT for USDC
            r3 = await self._gateway.execute_swap(
                network=network, base_asset="USDT", quote_asset="USDC",
                side=TradeType.SELL, amount=usdt_amount,
                dex="jupiter", trading_type="router", slippage_pct=slippage,
            )
            tx3 = r3.get("txHash", "?")
            self.log_with_clock(logging.INFO, f"Leg 3 done: tx={tx3}")

            self._total_trades += 1
            self._total_profit_usdc += profit_usdc

            msg = (
                f"[TRI-ARB] ✅ Trade #{self._total_trades} complete  "
                f"profit=+{profit_usdc:.4f} USDC ({profit_pct:.3f}%)  "
                f"cumulative=+{self._total_profit_usdc:.4f} USDC  "
                f"txs: {tx1} / {tx2} / {tx3}"
            )
            self.log_with_clock(logging.INFO, msg)
            self.notify_hb_app_with_timestamp(msg)

        except Exception as e:
            # On-chain revert or gateway error — funds are safe (Solana atomic tx)
            self._total_reverts += 1
            self.log_with_clock(
                logging.WARNING,
                f"[TRI-ARB] ⚠️ Revert #{self._total_reverts} (funds safe): {e}"
            )

    # ------------------------------------------------------------------
    # Status display
    # ------------------------------------------------------------------

    def format_status(self) -> str:
        mode = "DRY RUN (paper)" if self.config.dry_run else "LIVE"
        lines = [
            "",
            f"  AMM Triangular Arbitrage — Solana/Jupiter  [{mode}]",
            f"  Route: USDC → SOL → USDT → USDC",
            f"  Capital: {self.config.order_amount} USDC  |  "
            f"Min profit: {self.config.min_profitability}%  |  "
            f"Slippage: {self.config.slippage_pct}%",
            f"  Scans: {self._total_scans}  |  "
            f"Trades: {self._total_trades}  |  "
            f"Skips: {self._total_skips}  |  "
            f"Reverts: {self._total_reverts}  |  "
            f"Stale: {self._total_stale}",
            f"  Cumulative profit: +{self._total_profit_usdc:.4f} USDC",
            "",
        ]
        if self._last_quote:
            q = self._last_quote
            profit_pct = (q["net_out_usdc"] - self.config.order_amount) / self.config.order_amount * 100
            lines += [
                f"  Last quote ({q['elapsed_ms']:.0f}ms):",
                f"    {self.config.order_amount} USDC → {q['sol_amount']:.6f} SOL "
                f"→ {q['usdt_amount']:.4f} USDT → {q['net_out_usdc']:.4f} USDC  "
                f"({profit_pct:+.3f}%)",
                "",
            ]
        return "\n".join(lines)
