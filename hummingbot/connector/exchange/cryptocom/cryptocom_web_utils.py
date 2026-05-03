from typing import Callable, Optional

import hummingbot.connector.exchange.cryptocom.cryptocom_constants as CONSTANTS
from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


def public_rest_url(path_url: str) -> str:
    return CONSTANTS.REST_URL + path_url


def private_rest_url(path_url: str) -> str:
    return CONSTANTS.REST_URL + path_url


def build_api_factory(
        throttler: Optional[AsyncThrottler] = None,
        time_synchronizer: Optional[TimeSynchronizer] = None,
        auth: Optional[AuthBase] = None,
        time_provider: Optional[Callable] = None,
) -> WebAssistantsFactory:
    throttler = throttler or create_throttler()
    api_factory = WebAssistantsFactory(
        throttler=throttler,
        auth=auth,
    )
    return api_factory


def build_api_factory_without_time_synchronizer_pre_processor(
        throttler: AsyncThrottler,
) -> WebAssistantsFactory:
    return WebAssistantsFactory(throttler=throttler)


def create_throttler() -> AsyncThrottler:
    return AsyncThrottler(CONSTANTS.RATE_LIMITS)
