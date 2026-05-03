import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.cryptocom import (
    cryptocom_constants as CONSTANTS,
    cryptocom_web_utils as web_utils,
)
from hummingbot.connector.exchange.cryptocom.cryptocom_order_book import CryptocomOrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange


class CryptocomAPIOrderBookDataSource(OrderBookTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        trading_pairs: List[str],
        connector: "CryptocomExchange",
        api_factory: WebAssistantsFactory,
    ):
        super().__init__(trading_pairs)
        self._connector = connector
        self._api_factory = api_factory
        self._trade_messages_queue_key = CONSTANTS.WS_TRADE_CHANNEL
        self._diff_messages_queue_key = CONSTANTS.WS_BOOK_CHANNEL
        self._ws_assistant: Optional[WSAssistant] = None

    async def get_last_traded_prices(
        self, trading_pairs: List[str], domain: Optional[str] = None
    ) -> Dict[str, float]:
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        rest_assistant = await self._api_factory.get_rest_assistant()
        data = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(CONSTANTS.GET_BOOK_PATH),
            params={"instrument_name": symbol, "depth": str(CONSTANTS.WS_BOOK_DEPTH)},
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.GET_BOOK_PATH,
        )
        return data

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        snapshot_response = await self._request_order_book_snapshot(trading_pair)
        snapshot_timestamp = time.time()
        snapshot_response["trading_pair"] = trading_pair
        return CryptocomOrderBook.snapshot_message_from_exchange(
            snapshot_response,
            snapshot_timestamp,
        )

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_MARKET_URL, ping_timeout=CONSTANTS.WS_HEARTBEAT_INTERVAL)
        self._ws_assistant = ws
        return ws

    async def _subscribe_channels(self, ws: WSAssistant):
        channels = []
        for trading_pair in self._trading_pairs:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            channels.append(f"{CONSTANTS.WS_BOOK_CHANNEL}.{symbol}.{CONSTANTS.WS_BOOK_DEPTH}")
            channels.append(f"{CONSTANTS.WS_TRADE_CHANNEL}.{symbol}")

        subscribe_msg = {
            "id": 1,
            "method": "subscribe",
            "params": {"channels": channels},
            "nonce": int(time.time() * 1000),
        }
        await ws.send(WSJSONRequest(payload=subscribe_msg))

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        async for ws_response in websocket_assistant.iter_messages():
            data = ws_response.data
            if not isinstance(data, dict):
                continue
            # Heartbeat handling
            if data.get("method") == "public/heartbeat":
                await websocket_assistant.send(
                    WSJSONRequest(payload={"id": data["id"], "method": "public/respond-heartbeat"})
                )
                continue
            result = data.get("result", {})
            channel = result.get("channel", "")
            instrument_name = result.get("instrument_name", "")
            if not instrument_name:
                continue
            try:
                trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(
                    symbol=instrument_name
                )
            except KeyError:
                continue
            result["trading_pair"] = trading_pair
            if channel == CONSTANTS.WS_BOOK_CHANNEL:
                order_book_message = CryptocomOrderBook.diff_message_from_exchange(result)
                self._message_queue[self._diff_messages_queue_key].put_nowait(order_book_message)
            elif channel == CONSTANTS.WS_TRADE_CHANNEL:
                for trade_data in result.get("data", []):
                    single = dict(result)
                    single["data"] = [trade_data]
                    trade_message = CryptocomOrderBook.trade_message_from_exchange(single)
                    self._message_queue[self._trade_messages_queue_key].put_nowait(trade_message)

    async def subscribe_to_trading_pair(self, trading_pair: str) -> bool:
        if self._ws_assistant is None:
            return False
        try:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            channels = [
                f"{CONSTANTS.WS_BOOK_CHANNEL}.{symbol}.{CONSTANTS.WS_BOOK_DEPTH}",
                f"{CONSTANTS.WS_TRADE_CHANNEL}.{symbol}",
            ]
            await self._ws_assistant.send(WSJSONRequest(payload={
                "id": 1, "method": "subscribe",
                "params": {"channels": channels},
                "nonce": int(time.time() * 1000),
            }))
            self.add_trading_pair(trading_pair)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception(f"Error subscribing to {trading_pair}")
            return False

    async def unsubscribe_from_trading_pair(self, trading_pair: str) -> bool:
        if self._ws_assistant is None:
            return False
        try:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            channels = [
                f"{CONSTANTS.WS_BOOK_CHANNEL}.{symbol}.{CONSTANTS.WS_BOOK_DEPTH}",
                f"{CONSTANTS.WS_TRADE_CHANNEL}.{symbol}",
            ]
            await self._ws_assistant.send(WSJSONRequest(payload={
                "id": 1, "method": "unsubscribe",
                "params": {"channels": channels},
                "nonce": int(time.time() * 1000),
            }))
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception(f"Error unsubscribing from {trading_pair}")
            return False

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        message_queue.put_nowait(raw_message)

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        message_queue.put_nowait(raw_message)

    async def _parse_order_book_snapshot_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        message_queue.put_nowait(raw_message)
