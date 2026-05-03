import asyncio
import json
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from bidict import bidict

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.cryptocom import (
    cryptocom_constants as CONSTANTS,
    cryptocom_utils,
    cryptocom_web_utils as web_utils,
)
from hummingbot.connector.exchange.cryptocom.cryptocom_api_order_book_data_source import (
    CryptocomAPIOrderBookDataSource,
)
from hummingbot.connector.exchange.cryptocom.cryptocom_api_user_stream_data_source import (
    CryptocomAPIUserStreamDataSource,
)
from hummingbot.connector.exchange.cryptocom.cryptocom_auth import CryptocomAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import DeductedFromReturnsTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

_REQUEST_ID = 0


def _next_id() -> int:
    global _REQUEST_ID
    _REQUEST_ID += 1
    return _REQUEST_ID


class CryptocomExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils

    def __init__(
        self,
        cryptocom_api_key: str,
        cryptocom_api_secret: str,
        trading_pairs: Optional[List[str]] = None,
        trading_required: bool = True,
        balance_asset_limit: Optional[Dict[str, Dict[str, Decimal]]] = None,
        rate_limits_share_pct: Decimal = Decimal("100"),
    ):
        self.api_key = cryptocom_api_key
        self.secret_key = cryptocom_api_secret
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        super().__init__(balance_asset_limit, rate_limits_share_pct)

    @property
    def authenticator(self) -> CryptocomAuth:
        return CryptocomAuth(api_key=self.api_key, secret_key=self.secret_key)

    @property
    def name(self) -> str:
        return "cryptocom"

    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self) -> str:
        return CONSTANTS.DEFAULT_DOMAIN

    @property
    def client_order_id_max_length(self) -> int:
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self) -> str:
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self) -> str:
        return CONSTANTS.GET_INSTRUMENTS_PATH

    @property
    def trading_pairs_request_path(self) -> str:
        return CONSTANTS.GET_INSTRUMENTS_PATH

    @property
    def check_network_request_path(self) -> str:
        return CONSTANTS.GET_INSTRUMENTS_PATH

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

    # ---- Internal helpers ----

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception) -> bool:
        return "INVALID_NONCE" in str(request_exception) or "40102" in str(request_exception)

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        return "40401" in str(status_update_exception) or "NOT_FOUND" in str(status_update_exception)

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        return "40401" in str(cancelation_exception) or "NOT_FOUND" in str(cancelation_exception)

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler,
            auth=self._auth,
        )

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return CryptocomAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return CryptocomAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
        )

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = s_decimal_NaN,
        is_maker: Optional[bool] = None,
    ) -> TradeFeeBase:
        is_maker = is_maker or (order_type is OrderType.LIMIT)
        return DeductedFromReturnsTradeFee(percent=self.estimate_fee_pct(is_maker))

    # ---- REST API helpers ----

    async def _api_post_private(self, path_url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sign and POST a Crypto.com private REST request via the web assistant (throttled + auth)."""
        method_name = path_url.lstrip("/")
        body = {"id": _next_id(), "method": method_name, "params": params if params else {}}
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        result = await rest_assistant.execute_request(
            url=web_utils.private_rest_url(path_url),
            method=RESTMethod.POST,
            data=body,
            throttler_limit_id=path_url,
            is_auth_required=True,
        )
        if result.get("code", 0) != 0:
            raise IOError(f"Crypto.com API error {result.get('code')}: {result.get('message', '')}")
        return result.get("result", {})

    # ---- Trading operations ----

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
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        side = CONSTANTS.SIDE_BUY if trade_type is TradeType.BUY else CONSTANTS.SIDE_SELL
        params: Dict[str, Any] = {
            "instrument_name": symbol,
            "side": side,
            "type": CONSTANTS.ORDER_TYPE_LIMIT if order_type.is_limit_type() else CONSTANTS.ORDER_TYPE_MARKET,
            "quantity": f"{amount:f}",
            "client_oid": order_id,
        }
        if order_type.is_limit_type():
            params["price"] = f"{price:f}"
            params["time_in_force"] = CONSTANTS.TIF_GTC

        result = await self._api_post_private(CONSTANTS.CREATE_ORDER_PATH, params)
        exchange_order_id = str(result.get("order_id", ""))
        return exchange_order_id, time.time()

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        params: Dict[str, Any] = {}
        if tracked_order.exchange_order_id:
            params["order_id"] = tracked_order.exchange_order_id
        else:
            params["client_oid"] = order_id
        try:
            await self._api_post_private(CONSTANTS.CANCEL_ORDER_PATH, params)
            return True
        except IOError as e:
            if self._is_order_not_found_during_cancelation_error(e):
                return False
            raise

    # ---- Trading rules ----

    async def _format_trading_rules(self, exchange_info_dict: Dict[str, Any]) -> List[TradingRule]:
        instruments = exchange_info_dict.get("result", {}).get("data", [])
        rules = []
        for inst in filter(cryptocom_utils.is_exchange_information_valid, instruments):
            try:
                base = inst["base_ccy"]
                quote = inst["quote_ccy"]
                trading_pair = combine_to_hb_trading_pair(base, quote)
                min_price_inc = Decimal(inst.get("price_tick_size", "0.01"))
                min_amount_inc = Decimal(inst.get("qty_tick_size", "0.0001"))
                rules.append(
                    TradingRule(
                        trading_pair=trading_pair,
                        min_order_size=min_amount_inc,
                        min_price_increment=min_price_inc,
                        min_base_amount_increment=min_amount_inc,
                        min_notional_size=Decimal("1"),
                    )
                )
            except Exception:
                self.logger().exception(f"Error parsing trading rule for {inst}. Skipping.")
        return rules

    async def _update_trading_fees(self):
        pass  # fees are static from utils.py

    # ---- Order status polling ----

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        params: Dict[str, Any] = {}
        if tracked_order.exchange_order_id:
            params["order_id"] = tracked_order.exchange_order_id
        else:
            params["client_oid"] = tracked_order.client_order_id

        result = await self._api_post_private(CONSTANTS.GET_ORDER_DETAIL_PATH, params)
        new_state = CONSTANTS.ORDER_STATE.get(result.get("status", ""), None)
        return OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(result.get("order_id", "")),
            trading_pair=tracked_order.trading_pair,
            update_timestamp=float(result.get("update_time", 0)) * 1e-3,
            new_state=new_state,
        )

    # ---- Balance updates ----

    async def _update_balances(self):
        try:
            result = await self._api_post_private(CONSTANTS.USER_BALANCE_PATH, {})
            self.logger().warning(f"[CRYPTOCOM BALANCE RAW] {json.dumps(result)[:2000]}")
        except Exception as e:
            self.logger().error(f"[CRYPTOCOM BALANCE ERROR] {e}", exc_info=True)
            return
        accounts = result.get("data", [])
        if not accounts:
            self.logger().warning(f"Crypto.com balance response had no 'data': {result}")
            return
        new_balances = {}
        new_available = {}
        for b in accounts[0].get("position_balances", []):
            asset = b["instrument_name"]
            new_balances[asset] = Decimal(str(b.get("quantity", "0")))
            new_available[asset] = Decimal(str(b.get("max_withdrawal_balance", "0")))
        self._account_balances.clear()
        self._account_available_balances.clear()
        self._account_balances.update(new_balances)
        self._account_available_balances.update(new_available)

    # ---- User stream event listener ----

    async def _user_stream_event_listener(self):
        async for event_message in self._iter_user_event_queue():
            try:
                result = event_message.get("result", {})
                channel = result.get("channel", "")
                data_list = result.get("data", [])

                if channel == CONSTANTS.WS_USER_ORDER_CHANNEL:
                    for order_data in data_list:
                        self._process_order_message(order_data)
                elif channel == CONSTANTS.WS_USER_TRADE_CHANNEL:
                    for trade_data in data_list:
                        self._process_trade_message(trade_data)
                elif channel == CONSTANTS.WS_USER_BALANCE_CHANNEL:
                    for balance_data in data_list:
                        self._process_balance_message_ws(balance_data)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream listener.")
                await self._sleep(5.0)

    def _process_order_message(self, order: Dict[str, Any]):
        client_order_id = str(order.get("client_oid", ""))
        tracked_order = self._order_tracker.all_updatable_orders.get(client_order_id)
        if not tracked_order:
            return
        new_state = CONSTANTS.ORDER_STATE.get(order.get("status", ""), None)
        if new_state is None:
            return
        order_update = OrderUpdate(
            client_order_id=client_order_id,
            exchange_order_id=str(order.get("order_id", "")),
            trading_pair=tracked_order.trading_pair,
            update_timestamp=float(order.get("update_time", 0)) * 1e-3,
            new_state=new_state,
        )
        self._order_tracker.process_order_update(order_update)

    def _process_trade_message(self, trade: Dict[str, Any]):
        client_order_id = str(trade.get("client_oid", ""))
        tracked_order = self._order_tracker.all_fillable_orders.get(client_order_id)
        if not tracked_order:
            return
        fee_amount = Decimal(str(trade.get("fees", "0"))).copy_abs()
        fee_token = trade.get("fee_instrument_name", tracked_order.quote_asset)
        fee = TradeFeeBase.new_spot_fee(
            fee_schema=self.trade_fee_schema(),
            trade_type=tracked_order.trade_type,
            percent_token=fee_token,
            flat_fees=[TokenAmount(amount=fee_amount, token=fee_token)],
        )
        fill_price = Decimal(str(trade.get("traded_price", "0")))
        fill_amount = Decimal(str(trade.get("traded_quantity", "0")))
        trade_update = TradeUpdate(
            trade_id=str(trade.get("trade_id", "")),
            client_order_id=client_order_id,
            exchange_order_id=str(trade.get("order_id", "")),
            trading_pair=tracked_order.trading_pair,
            fee=fee,
            fill_base_amount=fill_amount,
            fill_quote_amount=fill_price * fill_amount,
            fill_price=fill_price,
            fill_timestamp=float(trade.get("create_time", 0)) * 1e-3,
        )
        self._order_tracker.process_trade_update(trade_update)

    def _process_balance_message_ws(self, balance_data: Dict[str, Any]):
        for b in balance_data.get("position_balances", []):
            asset = b["instrument_name"]
            self._account_balances[asset] = Decimal(str(b.get("quantity", "0")))
            self._account_available_balances[asset] = Decimal(str(b.get("max_withdrawal_balance", "0")))

    # ---- Symbol mapping ----

    async def _initialize_trading_pair_symbol_map(self):
        try:
            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            data = await rest_assistant.execute_request(
                url=web_utils.public_rest_url(CONSTANTS.GET_INSTRUMENTS_PATH),
                method=RESTMethod.GET,
                throttler_limit_id=CONSTANTS.GET_INSTRUMENTS_PATH,
            )
            self._set_trading_pair_symbol_map(
                await self._build_trading_pair_symbol_map(data)
            )
        except Exception:
            self.logger().exception("Error initializing trading pair symbol map.")

    async def _build_trading_pair_symbol_map(self, exchange_info: Dict[str, Any]) -> bidict:
        mapping = bidict()
        instruments = exchange_info.get("result", {}).get("data", [])
        for inst in instruments:
            if not cryptocom_utils.is_exchange_information_valid(inst):
                continue
            symbol = inst["symbol"]
            base = inst["base_ccy"]
            quote = inst["quote_ccy"]
            hb_pair = combine_to_hb_trading_pair(base, quote)
            mapping[symbol] = hb_pair
        return mapping

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []
        if order.exchange_order_id is None:
            return trade_updates
        try:
            result = await self._api_post_private(
                CONSTANTS.GET_TRADES_HISTORY_PATH,
                {"order_id": order.exchange_order_id},
            )
            for trade in result.get("data", []):
                fee_amount = Decimal(str(trade.get("fees", "0"))).copy_abs()
                fee_token = trade.get("fee_instrument_name", order.quote_asset)
                fee = TradeFeeBase.new_spot_fee(
                    fee_schema=self.trade_fee_schema(),
                    trade_type=order.trade_type,
                    percent_token=fee_token,
                    flat_fees=[TokenAmount(amount=fee_amount, token=fee_token)],
                )
                fill_price = Decimal(str(trade.get("traded_price", "0")))
                fill_amount = Decimal(str(trade.get("traded_quantity", "0")))
                trade_updates.append(TradeUpdate(
                    trade_id=str(trade.get("trade_id", "")),
                    client_order_id=order.client_order_id,
                    exchange_order_id=str(trade.get("order_id", order.exchange_order_id)),
                    trading_pair=order.trading_pair,
                    fee=fee,
                    fill_base_amount=fill_amount,
                    fill_quote_amount=fill_price * fill_amount,
                    fill_price=fill_price,
                    fill_timestamp=float(trade.get("create_time", 0)) * 1e-3,
                ))
        except Exception:
            self.logger().warning(f"Failed to fetch trade updates for order {order.client_order_id}.")
        return trade_updates

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        mapping = bidict()
        instruments = exchange_info.get("result", {}).get("data", [])
        for inst in instruments:
            if not cryptocom_utils.is_exchange_information_valid(inst):
                continue
            try:
                mapping[inst["symbol"]] = combine_to_hb_trading_pair(inst["base_ccy"], inst["quote_ccy"])
            except Exception:
                pass
        self._set_trading_pair_symbol_map(mapping)

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        data = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(CONSTANTS.GET_TICKERS_PATH),
            params={"instrument_name": symbol},
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.GET_TICKERS_PATH,
        )
        ticker = data.get("result", {}).get("data", [{}])[0]
        return float(ticker.get("a", 0))
