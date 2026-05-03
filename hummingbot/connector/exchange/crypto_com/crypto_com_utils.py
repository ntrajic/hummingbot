from decimal import Decimal
from typing import Dict, Any

from hummingbot.connector.exchange.crypto_com import crypto_com_constants as CONSTANTS
from hummingbot.core.data_type.common import OrderType, TradeType


def trading_pair_to_instrument(trading_pair: str) -> str:
    """SOL-USDC -> SOL_USDC"""
    return trading_pair.replace("-", "_")


def instrument_to_trading_pair(instrument: str) -> str:
    """SOL_USDC -> SOL-USDC"""
    return instrument.replace("_", "-")


def order_type_to_str(order_type: OrderType) -> str:
    return CONSTANTS.ORDER_TYPE_LIMIT if order_type == OrderType.LIMIT else CONSTANTS.ORDER_TYPE_MARKET


def trade_type_to_side(trade_type: TradeType) -> str:
    return CONSTANTS.ORDER_SIDE_BUY if trade_type == TradeType.BUY else CONSTANTS.ORDER_SIDE_SELL


def parse_order_status(status: str) -> str:
    return {
        CONSTANTS.ORDER_STATUS_ACTIVE: "OPEN",
        CONSTANTS.ORDER_STATUS_FILLED: "FILLED",
        CONSTANTS.ORDER_STATUS_CANCELED: "CANCELED",
        CONSTANTS.ORDER_STATUS_REJECTED: "FAILED",
        CONSTANTS.ORDER_STATUS_EXPIRED: "CANCELED",
    }.get(status, status)


def get_new_client_order_id(is_buy: bool, trading_pair: str) -> str:
    import time
    side = "B" if is_buy else "S"
    pair = trading_pair.replace("-", "")[:6]
    return f"niks_{side}_{pair}_{int(time.time() * 1000)}"
