from decimal import Decimal
from typing import Any, Dict

from pydantic import ConfigDict, Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "SOL-USD"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.00040"),
    taker_percent_fee_decimal=Decimal("0.00040"),
    buy_percent_fee_deducted_from_returns=True,
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """Return True if the instrument is a tradable spot (CCY_PAIR) instrument."""
    return (
        exchange_info.get("tradable", False) is True
        and exchange_info.get("inst_type", "") == "CCY_PAIR"
    )


class CryptocomConfigMap(BaseConnectorConfigMap):
    connector: str = "cryptocom"
    cryptocom_api_key: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": "Enter your Crypto.com API key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        },
    )
    cryptocom_api_secret: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": "Enter your Crypto.com API secret",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        },
    )
    model_config = ConfigDict(title="cryptocom")


KEYS = CryptocomConfigMap.model_construct()
