import asyncio
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from bidict import bidict

from hummingbot.connector.exchange.crypto_com import (
    crypto_com_constants as CONSTANTS,
    crypto_com_utils as utils,
    crypto_com_web_utils as web_utils,
)
from hummingbot.connector.exchange.crypto_com.crypto_com_auth import CryptoComAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


class CryptoComExchange(ExchangePyBase):
    """
    Hummingbot connector for Crypto.com Exchange API v1.
    Supports paper trading via crypto_com_paper_trade connector name.
    """

    def __init__(
        self,
        crypto_com_api_key: str,
        crypto_com_secret_key: str,
        trading_pairs: Optional[List[str]] = None,
        trading_required: bool = True,
        domain: str = "com",
        balance_asset_limit: Optional[Dict[str, Dict[str, Decimal]]] = None,
        rate_limits_share_pct: Decimal = Decimal("100"),
    ):
        self.api_key = crypto_com_api_key
        self.secret_key = crypto_com_secret_key
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs or []
        super().__init__(balance_asset_limit=balance_asset_limit,
                         rate_limits_share_pct=rate_limits_share_pct)

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "crypto_com"

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def authenticator(self) -> CryptoComAuth:
        return CryptoComAuth(api_key=self.api_key, secret_key=self.secret_key)

    @property
    def rate_limits_rules(self) -> List[RateLimit]:
        return CONSTANTS.RATE_LIMITS

    @property
    def client_order_id_max_length(self) -> int:
        return 36

    @property
    def client_order_id_prefix(self) -> str:
        return "niks-"

    @property
    def trading_rules_request_path(self) -> str:
        return CONSTANTS.GET_INSTRUMENTS

    @property
    def trading_pairs_request_path(self) -> str:
        return CONSTANTS.GET_INSTRUMENTS

    @property
    def check_network_request_path(self) -> str:
        return CONSTANTS.GET_TICKER

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return False  # Crypto.com cancel is async

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def supported_order_types(self) -> List[OrderType]:
        return [OrderType.LIMIT, OrderType.MARKET]

    # ── Web assistants ────────────────────────────────────────────────────────

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(throttler=self._throttler, auth=self._auth)

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        # Minimal stub — order book data source not required for paper trading scanner
        from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
        return _NullOrderBookDataSource(trading_pairs=self._trading_pairs, connector=self)

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return _NullUserStreamDataSource()

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        from bidict import bidict
        mapping = bidict()
        for inst in exchange_info.get("data", []):
            symbol = inst.get("symbol", "")
            if symbol:
                mapping[symbol] = utils.instrument_to_trading_pair(symbol)
        self._set_trading_pair_symbol_map(mapping)

    # ── Exception helpers ─────────────────────────────────────────────────────

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception) -> bool:
        return "40102" in str(request_exception)  # INVALID_NONCE

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        return "40401" in str(status_update_exception)  # NOT_FOUND

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        return "40401" in str(cancelation_exception)

    # ── Private REST helper ───────────────────────────────────────────────────

    async def _api_post_signed(self, method: str, params: Optional[Dict] = None) -> Dict:
        """POST a signed private request and return result dict."""
        body = self._auth.build_signed_body(method=method, params=params)
        path = "/" + method
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.private_rest_url(path)
        request = RESTRequest(method=RESTMethod.POST, url=url, data=json.dumps(body), is_auth_required=False)
        async with self._throttler.execute_task(limit_id=path):
            response = await rest_assistant.call(request)
        data = await response.json()
        if data.get("code", -1) != 0:
            raise IOError(f"Crypto.com API error {data.get('code')}: {data.get('message', data)}")
        return data.get("result", data)

    # ── Balance ───────────────────────────────────────────────────────────────

    async def _update_balances(self):
        result = await self._api_post_signed("private/user-balance", params={})
        for account in result.get("data", []):
            for pos in account.get("position_balances", []):
                asset = pos["instrument_name"]
                self._account_available_balances[asset] = Decimal(str(pos.get("max_withdrawal_balance", "0")))
                self._account_balances[asset] = Decimal(str(pos.get("quantity", "0")))

    # ── Trading rules ─────────────────────────────────────────────────────────

    async def _format_trading_rules(self, raw_trading_pairs: Dict) -> List[TradingRule]:
        rules = []
        for inst in raw_trading_pairs.get("data", []):
            if inst.get("tradable") is False:
                continue
            try:
                pair = utils.instrument_to_trading_pair(inst["symbol"])
                rules.append(TradingRule(
                    trading_pair=pair,
                    min_order_size=Decimal(str(inst.get("qty_tick_size", "0.001"))),
                    min_price_increment=Decimal(str(inst.get("price_tick_size", "0.01"))),
                    min_base_amount_increment=Decimal(str(inst.get("qty_tick_size", "0.001"))),
                    min_notional_size=Decimal(str(inst.get("min_quantity", "1"))),
                ))
            except Exception:
                pass
        return rules

    async def _update_trading_rules(self):
        result = await self._api_get(path_url=CONSTANTS.GET_INSTRUMENTS)
        rules = await self._format_trading_rules(result)
        self._trading_rules = {r.trading_pair: r for r in rules}

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        instrument = utils.trading_pair_to_instrument(trading_pair)
        result = await self._api_get(
            path_url=CONSTANTS.GET_TICKER,
            params={"instrument_name": instrument},
        )
        ticker = result.get("data", [{}])[0]
        return float(ticker.get("a", 0))

    # ── Order placement ───────────────────────────────────────────────────────

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> Tuple[str, float]:
        params = {
            "instrument_name": utils.trading_pair_to_instrument(trading_pair),
            "side": utils.trade_type_to_side(trade_type),
            "type": utils.order_type_to_str(order_type),
            "quantity": str(amount),
            "client_oid": order_id,
        }
        if order_type == OrderType.LIMIT:
            params["price"] = str(price)
        result = await self._api_post_signed("private/create-order", params=params)
        exchange_order_id = str(result.get("order_id", ""))
        return exchange_order_id, self.current_timestamp

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        params = {"order_id": tracked_order.exchange_order_id}
        await self._api_post_signed("private/cancel-order", params=params)
        return True

    # ── Order status ──────────────────────────────────────────────────────────

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        params = {"order_id": tracked_order.exchange_order_id}
        result = await self._api_post_signed("private/get-order-detail", params=params)
        order_data = result
        status_map = {
            "ACTIVE": OrderState.OPEN,
            "FILLED": OrderState.FILLED,
            "CANCELED": OrderState.CANCELED,
            "REJECTED": OrderState.FAILED,
            "EXPIRED": OrderState.CANCELED,
        }
        new_state = status_map.get(order_data.get("status", ""), OrderState.OPEN)
        return OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(order_data.get("order_id", "")),
            trading_pair=tracked_order.trading_pair,
            update_timestamp=self.current_timestamp,
            new_state=new_state,
        )

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        # Crypto.com doesn't have a per-order trade endpoint; use get-trades
        return []

    async def _update_trading_fees(self):
        # 0% taker fee granted — hardcode it
        pass

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = Decimal("0"),
        is_maker: Optional[bool] = None,
    ) -> TradeFeeBase:
        # 0% taker fee for SOL/USDC as granted
        return AddedToCostTradeFee(percent=Decimal("0"))

    # ── Symbol map ────────────────────────────────────────────────────────────

    async def _api_get(self, path_url: str, params: Optional[Dict] = None) -> Dict:
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(path_url)
        request = RESTRequest(method=RESTMethod.GET, url=url, params=params, is_auth_required=False)
        async with self._throttler.execute_task(limit_id=path_url):
            response = await rest_assistant.call(request)
        data = await response.json()
        if data.get("code", -1) != 0:
            raise IOError(f"Crypto.com API error {data.get('code')}: {data.get('message', data)}")
        return data.get("result", data)

    async def _user_stream_event_listener(self):
        # Paper trading: no live WS user stream needed
        await asyncio.sleep(float("inf"))


# ── Null stubs for paper-trade mode ──────────────────────────────────────────

class _NullOrderBookDataSource(OrderBookTrackerDataSource):
    def __init__(self, trading_pairs, connector):
        super().__init__(trading_pairs)
        self._connector = connector

    async def get_last_traded_prices(self, trading_pairs, domain=None):
        return {tp: 0.0 for tp in trading_pairs}

    async def _request_order_book_snapshot(self, trading_pair):
        return {}

    async def _subscribe_channels(self, ws):
        pass

    async def _connected_websocket_assistant(self):
        pass

    async def subscribe_to_trading_pair(self, trading_pair: str):
        pass

    async def unsubscribe_from_trading_pair(self, trading_pair: str):
        pass

    async def listen_for_subscriptions(self):
        await asyncio.sleep(float("inf"))

    async def listen_for_order_book_diffs(self, ev_loop, output):
        await asyncio.sleep(float("inf"))

    async def listen_for_order_book_snapshots(self, ev_loop, output):
        await asyncio.sleep(float("inf"))


class _NullUserStreamDataSource(UserStreamTrackerDataSource):
    async def listen_for_user_stream(self, output):
        await asyncio.sleep(float("inf"))
