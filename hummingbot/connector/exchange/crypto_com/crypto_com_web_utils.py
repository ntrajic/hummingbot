from hummingbot.connector.exchange.crypto_com import crypto_com_constants as CONSTANTS
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.connections.data_types import RESTMethod


def public_rest_url(path: str) -> str:
    return CONSTANTS.REST_URL + path


def private_rest_url(path: str) -> str:
    return CONSTANTS.REST_URL + path


def build_api_factory(throttler: AsyncThrottler = None, auth=None) -> WebAssistantsFactory:
    throttler = throttler or AsyncThrottler(CONSTANTS.RATE_LIMITS)
    return WebAssistantsFactory(throttler=throttler, auth=auth)
