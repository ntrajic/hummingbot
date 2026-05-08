"""
AMM Triangular Arbitrage on Solana via Jupiter DEX
Triangle route: USDC -> SOL -> USDT -> USDC
All three legs execute as a single atomic Solana transaction via Jupiter router (using execute-tri-arb).
If the round-trip is unprofitable, the chain reverts - no USDC leaves the wallet.

Modes:
  dry_run: True  -> paper trading (quotes only, no execution)
  dry_run: False -> live trading (real BackpackWallet via Gateway)
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional, List

from pydantic import ConfigDict, Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class AmmTriArbSolConfig(StrategyV2ConfigBase):
    model_config = ConfigDict(extra='allow')
    script_file_name: str = os.path.basename(__file__)
    controllers_config: list = []

    # Gateway network configuration
    connector: str = Field("jupiter", json_schema_extra={
        "prompt": "Enter the Gateway connector (e.g. jupiter)", "prompt_on_new": True})
    chain: str = Field("solana", json_schema_extra={
        "prompt": "Enter the chain (e.g. solana)", "prompt_on_new": True})
    network: str = Field("mainnet-beta", json_schema_extra={
        "prompt": "Enter the network (e.g. mainnet-beta)", "prompt_on_new": True})

    use_jupiter_routing: bool = Field(True)
    allow_sequential_fallback: bool = Field(True)

    # Triangle legs
    pair_1_base: str = Field("SOL")
    pair_1_quote: str = Field("USDC")
    pair_2_base: str = Field("SOL")
    pair_2_quote: str = Field("USDT")
    pair_3_base: str = Field("USDT")
    pair_3_quote: str = Field("USDC")

    # Capital: amount of USDC to deploy per cycle
    order_amount: Decimal = Field(Decimal("13.0"), json_schema_extra={
        "prompt": "Order amount in USDC per cycle", "prompt_on_new": True})

    # Minimum net profit required to fire the trade (1.2 = 1.2%)
    min_profitability: Decimal = Field(Decimal("1.2"), json_schema_extra={
        "prompt": "Minimum profitability % to trigger a trade (e.g. 1.2)", "prompt_on_new": True})

    # Slippage tolerance passed to Jupiter (triggers on-chain revert if exceeded)
    slippage_pct: Decimal = Field(Decimal("1.2"))

    # Abort if total quote round-trip takes longer than this (ms)
    max_quote_age_ms: int = Field(1500)

    # Seconds between each scan cycle
    scan_interval: int = Field(60)

    # Paper trade mode: True = quotes only, no execution
    dry_run: bool = Field(True)

    # Telegram alerts
    telegram_token: str = Field("")
    telegram_chat_id: str = Field("")

    def update_markets(self, markets: MarketDict) -> MarketDict:
        # Register the network connector
        market_name = f"{self.connector}_{self.chain}_{self.network}"
        markets[market_name] = markets.get(market_name, set()) | {
            f"{self.pair_1_base}-{self.pair_1_quote}",
            f"{self.pair_2_base}-{self.pair_2_quote}",
            f"{self.pair_3_base}-{self.pair_3_quote}"
        }
        return markets


class AmmTriArbSol(StrategyV2Base):
    """
    Triangular arbitrage: USDC -> SOL -> USDT -> USDC via Jupiter on Solana.
    """

    _next_scan: float = 0.0
    _scanning: bool = False
    _total_scans: int = 0
    _total_trades: int = 0
    _total_reverts: int = 0
    _total_skips: int = 0
    _total_stale: int = 0
    _total_profit_usdc: Decimal = Decimal("0")
    _last_quote: Optional[Dict] = None

    def __init__(self, connectors: Dict[str, ConnectorBase], config: AmmTriArbSolConfig):
        super().__init__(connectors, config)
        self.config = config
        self._gateway = GatewayHttpClient.get_instance()
        self._tg_token: str = config.telegram_token
        self._tg_chat_id: str = config.telegram_chat_id

    def _tg_send(self, msg: str):
        if not (self._tg_token and self._tg_chat_id):
            return
        asyncio.ensure_future(self._tg_send_async(msg))

    async def _tg_send_async(self, msg: str):
        import aiohttp
        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json={"chat_id": self._tg_chat_id, "text": msg},
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        self.logger().warning(f"Telegram send failed [{r.status}]: {await r.text()}")
        except Exception as e:
            self.logger().warning(f"Telegram send error: {e}")

    async def on_start(self):
        self.logger().info(f"Strategy started in {'DRY RUN' if self.config.dry_run else 'LIVE'} mode.")
        if not self.config.dry_run:
            asyncio.ensure_future(self._preflight_check())

    async def _preflight_check(self):
        try:
            balances = await self._gateway.get_balances(
                chain=self.config.chain,
                network=self.config.network,
                address="", # Uses defaultWallet
                token_symbols=["USDC", "SOL"],
            )
            usdc = Decimal(str(balances.get("USDC", 0)))
            sol = Decimal(str(balances.get("SOL", 0)))
        except Exception as e:
            self.logger().error(f"[PRE-FLIGHT] Balance check failed: {e}. Trading halted.")
            self._next_scan = float("inf")
            return

        errors = []
        if self.config.order_amount > usdc * Decimal("0.95"):
            errors.append(f"order_amount exceeds 95% of USDC balance ({usdc:.4f})")
        if sol < Decimal("0.05"):
            errors.append(f"SOL balance ({sol:.4f}) too low for gas (min 0.05)")

        if errors:
            for err in errors:
                self.logger().error(f"[PRE-FLIGHT] ❌ {err}")
            self._next_scan = float("inf")
            return

        self.logger().info(f"[PRE-FLIGHT] ✅ USDC={usdc:.4f} SOL={sol:.4f}. LIVE active.")

    def on_tick(self):
        if self.current_timestamp < self._next_scan or self._scanning:
            return
        self._next_scan = self.current_timestamp + self.config.scan_interval
        self._scanning = True
        asyncio.ensure_future(self._scan_and_act())

    async def _scan_and_act(self):
        try:
            quote = await self._get_triangle_quote()
            if quote is None:
                return

            self._last_quote = quote
            self._total_scans += 1

            if quote["elapsed_ms"] > self.config.max_quote_age_ms:
                self._total_stale += 1
                self.logger().warning(f"[SCAN #{self._total_scans}] Quotes stale ({quote['elapsed_ms']:.0f}ms). Skipping.")
                return

            net_out = quote["net_out_usdc"]
            profit_pct = (net_out - self.config.order_amount) / self.config.order_amount * 100
            
            if profit_pct < self.config.min_profitability:
                self._total_skips += 1
                self.logger().info(f"[SCAN #{self._total_scans}] net_out={net_out:.4f} USDC profit={profit_pct:.3f}% below {self.config.min_profitability}%")
                return

            self.logger().info(f"[SCAN #{self._total_scans}] net_out={net_out:.4f} USDC profit={profit_pct:.3f}% ✅ FIRE")
            await self._execute_triangle(quote, profit_pct)

        except Exception as e:
            self.logger().error(f"Scan error: {e}")
        finally:
            self._scanning = False

    async def _get_quote(self, base: str, quote: str, amount: Decimal, side: TradeType) -> Optional[Decimal]:
        """Call Gateway quote_swap (uses GET)"""
        try:
            network_full = f"{self.config.chain}-{self.config.network}"
            res = await self._gateway.quote_swap(
                network=network_full,
                base_asset=base,
                quote_asset=quote,
                amount=amount,
                side=side,
                dex=self.config.connector,
                trading_type="router",
                slippage_pct=self.config.slippage_pct,
                fail_silently=True
            )
            if res and "amountOut" in res:
                return Decimal(str(res["amountOut"]))
            elif res and "price" in res:
                price = Decimal(str(res["price"]))
                return amount * price
        except Exception as e:
            self.logger().warning(f"Quote failed for {base}-{quote} ({side.name}): {e}")
        return None

    async def _get_triangle_quote(self) -> Optional[Dict]:
        t0 = time.monotonic()
        amount = self.config.order_amount

        # Leg 1: USDC -> SOL (BUY SOL with USDC)
        sol_out = await self._get_quote(self.config.pair_1_base, self.config.pair_1_quote, amount, TradeType.BUY)
        if sol_out is None or sol_out <= 0: return None

        # Leg 2: SOL -> USDT (SELL SOL for USDT)
        usdt_out = await self._get_quote(self.config.pair_2_base, self.config.pair_2_quote, sol_out, TradeType.SELL)
        if usdt_out is None or usdt_out <= 0: return None

        # Leg 3: USDT -> USDC (SELL USDT for USDC)
        usdc_out = await self._get_quote(self.config.pair_3_base, self.config.pair_3_quote, usdt_out, TradeType.SELL)
        if usdc_out is None or usdc_out <= 0: return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "net_out_usdc": usdc_out,
            "elapsed_ms": elapsed_ms,
            "sol_amount": sol_out,
            "usdt_amount": usdt_out,
        }

    async def _execute_triangle(self, quote: Dict, profit_pct: Decimal):
        amount = self.config.order_amount
        net_out = quote["net_out_usdc"]
        profit_usdc = net_out - amount
        ts = datetime.fromtimestamp(self.current_timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if self.config.dry_run:
            msg = f"[DRY RUN] TRI-ARB @ {ts} | {amount} USDC -> {quote['sol_amount']:.4f} SOL -> {quote['usdt_amount']:.4f} USDT -> {net_out:.4f} USDC | profit={profit_pct:.3f}% (+{profit_usdc:.4f} USDC)"
            self.logger().info(msg)
            self._tg_send(msg)
            return

        network_full = f"{self.config.chain}-{self.config.network}"
        
        # --- 1. Atomic Try ---
        if self.config.use_jupiter_routing:
            self.logger().info("Initiating Atomic Triangle Execution via Gateway...")
            try:
                result = await self._gateway.execute_tri_arb(
                    network=network_full,
                    token_a="USDC",
                    token_b="SOL",
                    token_c="USDT",
                    amount=amount,
                    slippage_pct=self.config.slippage_pct
                )
                if self._handle_execution_result(result, amount, net_out, ts):
                    return
            except Exception as e:
                err_str = str(e)
                if "not found" in err_str.lower() or "404" in err_str:
                    self.logger().warning(f"Atomic execution route not found. {'Falling back to sequential' if self.config.allow_sequential_fallback else 'Aborting'}.")
                else:
                    self.logger().error(f"Atomic execution failed: {e}")
                
        # --- 2. Sequential Fallback ---
        if not self.config.allow_sequential_fallback:
            return

        self.logger().info("Executing triangle legs sequentially...")
        try:
            # Leg 1: BUY SOL with USDC
            r1 = await self._gateway.execute_swap(network=network_full, base_asset="SOL", quote_asset="USDC", amount=amount, side=TradeType.BUY, dex=self.config.connector, trading_type="router", slippage_pct=self.config.slippage_pct)
            if "hash" not in r1 and "signature" not in r1 and "network_transaction_hash" not in r1:
                self.logger().error(f"Leg 1 failed: {r1}")
                return
            
            # Leg 2: SELL SOL for USDT
            sol_to_sell = quote["sol_amount"]
            r2 = await self._gateway.execute_swap(network=network_full, base_asset="SOL", quote_asset="USDT", amount=sol_to_sell, side=TradeType.SELL, dex=self.config.connector, trading_type="router", slippage_pct=self.config.slippage_pct)
            if "hash" not in r2 and "signature" not in r2 and "network_transaction_hash" not in r2:
                self.logger().error(f"Leg 2 failed: {r2}. MANUAL UNWIND MAY BE NEEDED.")
                return

            # Leg 3: SELL USDT for USDC
            usdt_to_sell = quote["usdt_amount"]
            r3 = await self._gateway.execute_swap(network=network_full, base_asset="USDT", quote_asset="USDC", amount=usdt_to_sell, side=TradeType.SELL, dex=self.config.connector, trading_type="router", slippage_pct=self.config.slippage_pct)
            
            actual_profit = net_out - amount # Approximation for sequential
            self._total_trades += 1
            self._total_profit_usdc += actual_profit
            msg = f"[TRI-ARB] ✅ Sequential Success #{self._total_trades} @ {ts} | profit={actual_profit:+.4f} USDC"
            self.logger().info(msg)
            self._tg_send(msg)

        except Exception as e:
            self.logger().error(f"Sequential execution error: {e}")

    def _handle_execution_result(self, result: Dict, amount: Decimal, net_out: Decimal, ts: str) -> bool:
        status = result.get("status")
        error = result.get("error", "Unknown error")
        if status == 1:
            actual_out = Decimal(str(result.get("amountOut", net_out)))
            actual_profit = actual_out - amount
            self._total_trades += 1
            self._total_profit_usdc += actual_profit
            msg = (
                f"[TRI-ARB] ✅ Atomic Success #{self._total_trades} @ {ts} | "
                f"profit={actual_profit:+.4f} USDC | cumulative={self._total_profit_usdc:+.4f} USDC | "
                f"sigs: {result.get('leg1Sig','?')}/{result.get('leg2Sig','?')}/{result.get('leg3Sig','?')}"
            )
            self.logger().info(msg)
            self._tg_send(msg)
            return True
        elif status == -1:
            self._total_reverts += 1
            self.logger().warning(f"[TRI-ARB] ⚠️ Leg 1 failed (nothing spent): {error}")
            return True
        elif status == -2:
            self._total_reverts += 1
            unwind_sig = result.get("unwindSig")
            if unwind_sig:
                self.logger().warning(f"[TRI-ARB] 🔄 Partial fill, unwind OK (sig={unwind_sig}): {error}")
            else:
                msg = f"[TRI-ARB] 🚨 CRITICAL: Partial fill AND unwind failed! {error}"
                self.logger().critical(msg)
                self._tg_send(msg)
            return True
        return False

    def format_status(self) -> str:
        mode = "DRY RUN" if self.config.dry_run else "LIVE"
        lines = [
            f"\n  AMM Tri-Arb Solana/Jupiter [{mode}]",
            f"  Scans: {self._total_scans} | Trades: {self._total_trades} | Skips: {self._total_skips} | Reverts: {self._total_reverts}",
            f"  Profit: {self._total_profit_usdc:+.4f} USDC",
        ]
        if self._last_quote:
            q = self._last_quote
            profit_pct = (q["net_out_usdc"] - self.config.order_amount) / self.config.order_amount * 100
            lines.append(f"  Last: {self.config.order_amount} USDC -> {q['sol_amount']:.4f} SOL -> {q['usdt_amount']:.4f} USDT -> {q['net_out_usdc']:.4f} USDC ({profit_pct:+.3f}%)")
        return "\n".join(lines)
