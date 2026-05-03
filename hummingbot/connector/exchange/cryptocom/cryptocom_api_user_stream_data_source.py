import asyncio
import time
from typing import TYPE_CHECKING, List, Optional

from hummingbot.connector.exchange.cryptocom import cryptocom_constants as CONSTANTS
from hummingbot.connector.exchange.cryptocom.cryptocom_auth import CryptocomAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange


class CryptocomAPIUserStreamDataSource(UserStreamTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        auth: CryptocomAuth,
        trading_pairs: List[str],
        connector: "CryptocomExchange",
        api_factory: WebAssistantsFactory,
    ):
        super().__init__()
        self._auth = auth
        self._trading_pairs = trading_pairs
        self._connector = connector
        self._api_factory = api_factory

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_USER_URL, ping_timeout=CONSTANTS.WS_HEARTBEAT_INTERVAL)
        # Authenticate
        auth_payload = self._auth.get_ws_auth_payload()
        await ws.send(WSJSONRequest(payload=auth_payload))
        # Wait for auth response
        async for ws_response in ws.iter_messages():
            data = ws_response.data
            if isinstance(data, dict) and data.get("method") == "public/auth":
                if data.get("code") == 0:
                    break
                else:
                    raise IOError(f"Crypto.com WebSocket auth failed: {data}")
        return ws

    async def _subscribe_channels(self, ws: WSAssistant):
        channels = [
            CONSTANTS.WS_USER_ORDER_CHANNEL,
            CONSTANTS.WS_USER_TRADE_CHANNEL,
            CONSTANTS.WS_USER_BALANCE_CHANNEL,
        ]
        subscribe_msg = {
            "id": 2,
            "method": "subscribe",
            "params": {"channels": channels},
            "nonce": int(time.time() * 1000),
        }
        await ws.send(WSJSONRequest(payload=subscribe_msg))

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        # Not used — listen_for_user_stream is fully overridden below.
        pass

    async def _get_ws_assistant(self) -> WSAssistant:
        return await self._api_factory.get_ws_assistant()

    async def listen_for_user_stream(self, output: asyncio.Queue):
        """Override to use output queue directly."""
        while True:
            try:
                ws = await self._connected_websocket_assistant()
                await self._subscribe_channels(ws)
                async for ws_response in ws.iter_messages():
                    data = ws_response.data
                    if not isinstance(data, dict):
                        continue
                    if data.get("method") == "public/heartbeat":
                        await ws.send(
                            WSJSONRequest(payload={"id": data["id"], "method": "public/respond-heartbeat"})
                        )
                        continue
                    result = data.get("result", {})
                    channel = result.get("channel", "")
                    if channel in (
                        CONSTANTS.WS_USER_ORDER_CHANNEL,
                        CONSTANTS.WS_USER_TRADE_CHANNEL,
                        CONSTANTS.WS_USER_BALANCE_CHANNEL,
                    ):
                        output.put_nowait(data)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream. Reconnecting in 5s.")
                await asyncio.sleep(5)
