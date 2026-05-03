from typing import Dict, Optional

from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType


class CryptocomOrderBook(OrderBook):
    @classmethod
    def snapshot_message_from_exchange(
        cls,
        msg: Dict,
        timestamp: float,
        metadata: Optional[Dict] = None,
    ) -> OrderBookMessage:
        """
        Convert a REST snapshot response into an OrderBookMessage.
        msg["data"][0] contains {"bids": [...], "asks": [...]}
        Each level: [price_str, qty_str, num_orders_str]
        """
        if metadata:
            msg = dict(msg, **metadata)
        data = msg.get("data", [{}])[0]
        bids = [[float(b[0]), float(b[1])] for b in data.get("bids", [])]
        asks = [[float(a[0]), float(a[1])] for a in data.get("asks", [])]
        content = {
            "trading_pair": msg["trading_pair"],
            "update_id": data.get("u", int(timestamp * 1e3)),
            "bids": bids,
            "asks": asks,
        }
        return OrderBookMessage(
            OrderBookMessageType.SNAPSHOT,
            content,
            timestamp=timestamp,
        )

    @classmethod
    def diff_message_from_exchange(
        cls,
        msg: Dict,
        timestamp: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> OrderBookMessage:
        """
        Convert a WebSocket book update into an OrderBookMessage.
        Crypto.com sends full snapshots on the book channel (SNAPSHOT mode).
        """
        if metadata:
            msg = dict(msg, **metadata)
        data = msg.get("data", [{}])[0]
        bids = [[float(b[0]), float(b[1])] for b in data.get("bids", [])]
        asks = [[float(a[0]), float(a[1])] for a in data.get("asks", [])]
        ts = timestamp or data.get("t", 0) / 1e3
        content = {
            "trading_pair": msg["trading_pair"],
            "update_id": data.get("u", int(ts * 1e3)),
            "bids": bids,
            "asks": asks,
        }
        return OrderBookMessage(
            OrderBookMessageType.DIFF,
            content,
            timestamp=ts,
        )

    @classmethod
    def trade_message_from_exchange(
        cls,
        msg: Dict,
        metadata: Optional[Dict] = None,
    ) -> OrderBookMessage:
        if metadata:
            msg = dict(msg, **metadata)
        data = msg.get("data", [{}])[0]
        content = {
            "trading_pair": msg["trading_pair"],
            "trade_type": float(1.0) if data.get("s") == "BUY" else float(2.0),
            "trade_id": str(data.get("d", "")),
            "update_id": data.get("t", 0),
            "price": data.get("p", "0"),
            "amount": data.get("q", "0"),
        }
        return OrderBookMessage(
            OrderBookMessageType.TRADE,
            content,
            timestamp=data.get("t", 0) / 1e3,
        )
