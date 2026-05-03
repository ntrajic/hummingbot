from decimal import Decimal
from typing import Any, Dict

from pydantic import ConfigDict, Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap
from hummingbot.connector.exchange.crypto_com import crypto_com_constants as CONSTANTS
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "SOL-USDC"

# 0% taker fee for SOL/USDC as granted; maker also 0 for simplicity
DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0"),
    taker_percent_fee_decimal=Decimal("0"),
)


class CryptoComConfigMap(BaseConnectorConfigMap):
    connector: str = "crypto_com"
    crypto_com_api_key: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your Crypto.com Exchange API key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    crypto_com_secret_key: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your Crypto.com Exchange secret key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    model_config = ConfigDict(title="crypto_com")


KEYS = CryptoComConfigMap.model_construct()


# ── Helpers used by the exchange connector ────────────────────────────────────

def trading_pair_to_instrument(trading_pair: str) -> str:
    return trading_pair.replace("-", "_")


def instrument_to_trading_pair(instrument: str) -> str:
    return instrument.replace("_", "-")


def order_type_to_str(order_type: OrderType) -> str:
    return CONSTANTS.ORDER_TYPE_LIMIT if order_type == OrderType.LIMIT else CONSTANTS.ORDER_TYPE_MARKET


def trade_type_to_side(trade_type: TradeType) -> str:
    return CONSTANTS.ORDER_SIDE_BUY if trade_type == TradeType.BUY else CONSTANTS.ORDER_SIDE_SELL
