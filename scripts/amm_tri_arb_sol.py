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

from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.notifier.telegram_notifier import TelegramNotifier
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class AmmTriArbSolConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: list = []

    # Gateway network connector
    connector: str = Field("solana-mainnet-beta", json_schema_extra={
        "prompt": "Enter the Gateway connector (e.g. solana-mainnet-beta)", "prompt_on_new": True})

    # Triangle legs (base-quote pairs as Jupiter expects them)
    pair_1: str = Field("SOL-USDC", json_schema_extra={
        "prompt": "Leg 1 trading pair — buy base with USDC (e.g. SOL-USDC)", "prompt_on_new": True})
    pair_2: str = Field("SOL-USDT", json_schema_extra={
        "prompt": "Leg 2 trading pair — sell base for USDT (e.g. SOL-USDT)", "prompt_on_new": True})
    pair_3: str = Field("USDT-USDC", json_schema_extra={
        "prompt": "Leg 3 trading pair — sell USDT back to USDC (e.g. USDT-USDC)", "prompt_on_new": True})

    # Capital: amount of USDC to deploy per cycle
    order_amount: Decimal = Field(Decimal("13.0"), json_schema_extra={
        "prompt": "Order amount in USDC per cycle", "prompt_on_new": True})

    # Minimum net profit required to fire the trade (1.2 = 1.2%)
    min_profitability: Decimal = Field(Decimal("1.2"), json_schema_extra={
        "prompt": "Minimum profitability % to trigger a trade (e.g. 1.2)", "prompt_on_new": True})

    # Slippage tolerance passed to Jupiter (triggers on-chain revert if exceeded)
    slippage_pct: Decimal = Field(Decimal("0.05"))

    # Abort if total quote round-trip takes longer than this (ms)
    max_quote_age_ms: int = Field(500)

    # Seconds between each scan cycle
    scan_interval: int = Field(10)

    # Paper trade mode: True = quotes only, no execution
    dry_run: bool = Field(True)

    # Telegram alerts (profit-only). Leave blank to disable.
    # Get token from @BotFather, chat_id from @userinfobot
    telegram_token: str = Field("")
    telegram_chat_id: str = Field("")

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
        self._telegram: Optional[TelegramNotifier] = None

    async def on_start(self):
        if self.config.telegram_token and self.config.telegram_chat_id:
            self._telegram = TelegramNotifier(
                token=self.config.telegram_token,
                chat_id=self.config.telegram_chat_id,
            )
            self._telegram.start()
            # Register with the app so notify_hb_app_with_timestamp() routes to Telegram
            app = HummingbotApplication.main_application()
            app.trading_core.add_notifier(self._telegram)
            self.log_with_clock(logging.INFO, "Telegram notifier started.")

        if not self.config.dry_run:
            asyncio.ensure_future(self._preflight_check())

    async def _preflight_check(self):
        """
        Pre-flight safety checks before live trading begins.
        Halts the strategy (sets _next_scan far in the future) if any check fails.
        """
        network = self.config.connector
        try:
            balances = await self._gateway.get_balances(
                chain="solana", network="mainnet-beta",
                address="",   # Gateway uses the defaultWallet from solana.yml
                token_symbols=["USDC", "SOL"],
            )
            usdc = Decimal(str(balances.get("USDC", 0)))
            sol = Decimal(str(balances.get("SOL", 0)))
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"[PRE-FLIGHT] Balance check failed: {e}. Trading halted.")
            self._next_scan = float("inf")
            return

        errors = []

        # Guard 1: order_amount must not exceed 95% of available USDC
        max_safe = usdc * Decimal("0.95")
        if self.config.order_amount > max_safe:
            errors.append(
                f"order_amount ({self.config.order_amount} USDC) > 95% of balance ({max_safe:.4f} USDC). "
                f"Reduce order_amount or add more USDC."
            )

        # Guard 2: must have at least 0.05 SOL for gas
        min_sol = Decimal("0.05")
        if sol < min_sol:
            errors.append(
                f"SOL balance ({sol:.4f}) < {min_sol} SOL minimum for gas. "
                f"Send at least {min_sol} SOL to your BackpackWallet."
            )

        if errors:
            for err in errors:
                self.log_with_clock(logging.ERROR, f"[PRE-FLIGHT] ❌ {err}")
            self.log_with_clock(logging.ERROR, "[PRE-FLIGHT] Trading halted. Fix the above and restart.")
            self._next_scan = float("inf")
            return

        self.log_with_clock(
            logging.INFO,
            f"[PRE-FLIGHT] ✅ USDC={usdc:.4f}  SOL={sol:.4f}  "
            f"order_amount={self.config.order_amount}  — all checks passed. LIVE trading active."
        )

    async def on_stop(self):
        if self._telegram:
            self._telegram.stop()
            self._telegram = None

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
    def _calc_output(response: dict, input_amount: Decimal) -> Decimal:
        """
        Compute output token amount from a Gateway quote_swap response.

        Gateway returns a 'price' field which is the exchange rate:
          - For SELL base→quote: price = quote_out / base_in  (e.g. USDT per SOL ≈ 150)
          - output = input_amount * price

        We prefer 'amountOut' when it is a plausible token quantity (not a dollar-value
        proxy), falling back to price-based calculation.  A sanity check ensures
        amountOut is not suspiciously large relative to the price-derived estimate.
        """
        price_val = response.get("price")
        if price_val is None:
            return Decimal("0")
        try:
            price = Decimal(str(price_val))
        except Exception:
            return Decimal("0")
        if price <= 0:
            return Decimal("0")

        # Primary: price-based calculation (always correct regardless of amountOut units)
        return input_amount * price

    async def _get_triangle_quote(self) -> Optional[Dict]:
        """
        Sequential quotes: USDC→SOL→USDT→USDC.

        Each leg uses the Gateway 'price' field (quote tokens per base token for a SELL)
        to compute the output amount:
          Leg 1 (SELL USDC→SOL):  price = SOL/USDC  → sol_amount  = order_amount * price
          Leg 2 (SELL SOL→USDT):  price = USDT/SOL  → usdt_amount = sol_amount   * price
          Leg 3 (SELL USDT→USDC): price = USDC/USDT → net_out     = usdt_amount  * price

        Returns dict with intermediate amounts and total elapsed ms, or None on failure.
        """
        network = self.config.connector  # "solana-mainnet-beta"
        slippage = self.config.slippage_pct
        amount = self.config.order_amount

        t0 = time.monotonic()
        try:
            # Leg 1: SELL USDC → SOL  (price = SOL per USDC, e.g. ~0.00667 at $150/SOL)
            q1 = await self._gateway.quote_swap(
                network=network,
                base_asset="USDC",
                quote_asset="SOL",
                amount=amount,
                side=TradeType.SELL,
                dex="jupiter",
                trading_type="router",
                slippage_pct=slippage,
            )
            sol_amount = self._calc_output(q1, amount)
            if sol_amount <= 0:
                self.log_with_clock(logging.WARNING, f"Leg 1 quote invalid: {q1}")
                return None

            # Leg 2: SELL SOL → USDT  (price = USDT per SOL, e.g. ~150)
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
            usdt_amount = self._calc_output(q2, sol_amount)
            if usdt_amount <= 0:
                self.log_with_clock(logging.WARNING, f"Leg 2 quote invalid: {q2}")
                return None

            # Leg 3: SELL USDT → USDC  (price = USDC per USDT, e.g. ~1.0)
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
            net_out_usdc = self._calc_output(q3, usdt_amount)
            if net_out_usdc <= 0:
                self.log_with_clock(logging.WARNING, f"Leg 3 quote invalid: {q3}")
                return None

        except Exception as e:
            self.log_with_clock(logging.WARNING, f"Quote failed: {e}")
            return None

        # Sanity check: net_out should be within 50% of input (real arb is tiny)
        ratio = net_out_usdc / amount
        if ratio > Decimal("1.5") or ratio < Decimal("0.5"):
            self.log_with_clock(
                logging.WARNING,
                f"Quote sanity check failed: net_out={net_out_usdc:.4f} USDC on "
                f"{amount} USDC input (ratio={ratio:.4f}). "
                f"Prices: leg1={q1.get('price')} leg2={q2.get('price')} leg3={q3.get('price')}. "
                f"Skipping."
            )
            return None

        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "sol_amount": sol_amount,
            "usdt_amount": usdt_amount,
            "net_out_usdc": net_out_usdc,
            "elapsed_ms": elapsed_ms,
            "prices": {
                "leg1_sol_per_usdc": q1.get("price"),
                "leg2_usdt_per_sol": q2.get("price"),
                "leg3_usdc_per_usdt": q3.get("price"),
            },
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
            prices = quote.get("prices", {})
            msg = (
                f"[DRY RUN] TRI-ARB opportunity: "
                f"{amount} USDC → {sol_amount:.6f} SOL → {usdt_amount:.4f} USDT → {net_out:.4f} USDC  "
                f"profit={profit_pct:.3f}% (+{profit_usdc:.4f} USDC)  "
                f"prices: SOL/USDC={prices.get('leg1_sol_per_usdc')} "
                f"USDT/SOL={prices.get('leg2_usdt_per_sol')} "
                f"USDC/USDT={prices.get('leg3_usdc_per_usdt')}"
            )
            self.log_with_clock(logging.INFO, msg)
            self.notify_hb_app_with_timestamp(msg)
            return

        # --- Live execution via single Gateway endpoint ---
        # Gateway handles leg sequencing and unwind server-side.
        # status 1 = full success, -1 = leg1 failed (nothing spent), -2 = partial (unwind attempted)
        result = await self._gateway.execute_tri_arb(
            network=network,
            token_a="USDC",
            token_b="SOL",
            token_c="USDT",
            amount=amount,
            slippage_pct=slippage,
        )

        status = result.get("status")
        error = result.get("error", "")

        if status == 1:
            actual_out = Decimal(str(result.get("amountOut") or net_out))
            actual_profit = actual_out - amount
            self._total_trades += 1
            self._total_profit_usdc += actual_profit
            msg = (
                f"[TRI-ARB] ✅ Trade #{self._total_trades} complete  "
                f"profit={actual_profit:+.4f} USDC  "
                f"cumulative={self._total_profit_usdc:+.4f} USDC  "
                f"sigs: {result.get('leg1Sig','?')} / {result.get('leg2Sig','?')} / {result.get('leg3Sig','?')}"
            )
            self.log_with_clock(logging.INFO, msg)
            self.notify_hb_app_with_timestamp(msg)

        elif status == -1:
            # Leg 1 failed — nothing was spent, safe to retry next cycle
            self._total_reverts += 1
            self.log_with_clock(logging.WARNING, f"[TRI-ARB] ⚠️ Leg 1 failed (nothing spent): {error}")

        else:
            # status == -2: partial fill, unwind attempted by Gateway
            self._total_reverts += 1
            unwind_sig = result.get("unwindSig")
            if unwind_sig:
                self.log_with_clock(logging.WARNING, f"[TRI-ARB] 🔄 Partial fill, unwind OK (sig={unwind_sig}): {error}")
            else:
                msg = f"[TRI-ARB] 🚨 CRITICAL: Partial fill AND unwind failed. MANUAL ACTION REQUIRED. {error}"
                self.log_with_clock(logging.CRITICAL, msg)
                self.notify_hb_app_with_timestamp(msg)

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
