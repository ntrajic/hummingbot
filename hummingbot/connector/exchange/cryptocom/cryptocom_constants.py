from hummingbot.core.api_throttler.data_types import LinkedLimitWeightPair, RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

DEFAULT_DOMAIN = "com"

HBOT_ORDER_ID_PREFIX = "HBOT"
MAX_ORDER_ID_LEN = 36

# Base URLs
REST_URL = "https://api.crypto.com/exchange/v1"
WSS_MARKET_URL = "wss://stream.crypto.com/exchange/v1/market"
WSS_USER_URL = "wss://stream.crypto.com/exchange/v1/user"

# Public REST endpoints
GET_INSTRUMENTS_PATH = "/public/get-instruments"
GET_BOOK_PATH = "/public/get-book"
GET_TICKERS_PATH = "/public/get-tickers"
GET_TRADES_PATH = "/public/get-trades"
SERVER_TIME_PATH = "/public/get-instruments"  # no dedicated time endpoint; use instruments as health check

# Private REST endpoints
CREATE_ORDER_PATH = "/private/create-order"
CANCEL_ORDER_PATH = "/private/cancel-order"
GET_OPEN_ORDERS_PATH = "/private/get-open-orders"
GET_ORDER_DETAIL_PATH = "/private/get-order-detail"
GET_ORDER_HISTORY_PATH = "/private/get-order-history"
GET_TRADES_HISTORY_PATH = "/private/get-trades"
USER_BALANCE_PATH = "/private/user-balance"

# Rate limit IDs
PUBLIC_RATE_LIMIT = "PUBLIC"
PRIVATE_RATE_LIMIT = "PRIVATE"
ORDER_RATE_LIMIT = "ORDER"

# Rate limits (per Crypto.com docs)
# private/create-order, cancel-order: 15 req per 100ms
# private/get-order-detail: 30 req per 100ms
# All others: 3 req per 100ms
# Public: 100 req/s
RATE_LIMITS = [
    RateLimit(limit_id=PUBLIC_RATE_LIMIT, limit=100, time_interval=1),
    RateLimit(limit_id=PRIVATE_RATE_LIMIT, limit=3, time_interval=0.1),
    RateLimit(limit_id=ORDER_RATE_LIMIT, limit=15, time_interval=0.1),
    # Public endpoints
    RateLimit(limit_id=GET_INSTRUMENTS_PATH, limit=100, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PUBLIC_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_BOOK_PATH, limit=100, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PUBLIC_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_TICKERS_PATH, limit=100, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PUBLIC_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_TRADES_PATH, limit=100, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PUBLIC_RATE_LIMIT, 1)]),
    # Private endpoints
    RateLimit(limit_id=CREATE_ORDER_PATH, limit=15, time_interval=0.1,
              linked_limits=[LinkedLimitWeightPair(ORDER_RATE_LIMIT, 1)]),
    RateLimit(limit_id=CANCEL_ORDER_PATH, limit=15, time_interval=0.1,
              linked_limits=[LinkedLimitWeightPair(ORDER_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_OPEN_ORDERS_PATH, limit=3, time_interval=0.1,
              linked_limits=[LinkedLimitWeightPair(PRIVATE_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_ORDER_DETAIL_PATH, limit=30, time_interval=0.1,
              linked_limits=[LinkedLimitWeightPair(PRIVATE_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_ORDER_HISTORY_PATH, limit=1, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PRIVATE_RATE_LIMIT, 1)]),
    RateLimit(limit_id=GET_TRADES_HISTORY_PATH, limit=1, time_interval=1,
              linked_limits=[LinkedLimitWeightPair(PRIVATE_RATE_LIMIT, 1)]),
    RateLimit(limit_id=USER_BALANCE_PATH, limit=3, time_interval=0.1,
              linked_limits=[LinkedLimitWeightPair(PRIVATE_RATE_LIMIT, 1)]),
]

# Order sides
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

# Order types
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_MARKET = "MARKET"

# Time in force
TIF_GTC = "GOOD_TILL_CANCEL"
TIF_IOC = "IMMEDIATE_OR_CANCEL"
TIF_FOK = "FILL_OR_KILL"

# Order states mapping from Crypto.com status to Hummingbot OrderState
ORDER_STATE = {
    "NEW": OrderState.OPEN,
    "PENDING": OrderState.PENDING_CREATE,
    "ACTIVE": OrderState.OPEN,
    "CANCELED": OrderState.CANCELED,
    "FILLED": OrderState.FILLED,
    "REJECTED": OrderState.FAILED,
    "EXPIRED": OrderState.FAILED,
}

# WebSocket channels
WS_BOOK_CHANNEL = "book"
WS_TRADE_CHANNEL = "trade"
WS_USER_ORDER_CHANNEL = "user.order"
WS_USER_TRADE_CHANNEL = "user.trade"
WS_USER_BALANCE_CHANNEL = "user.balance"

WS_HEARTBEAT_INTERVAL = 30.0
WS_BOOK_DEPTH = 50

# Instrument name separator on Crypto.com (e.g. SOL_USD)
INSTRUMENT_SEPARATOR = "_"
