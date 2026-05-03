from hummingbot.core.api_throttler.data_types import LinkedLimitWeightPair, RateLimit

# Crypto.com Exchange API v1 (latest, supersedes v2)
REST_URL = "https://api.crypto.com/exchange/v1"
WSS_PRIVATE_URL = "wss://stream.crypto.com/exchange/v1/user"
WSS_PUBLIC_URL = "wss://stream.crypto.com/exchange/v1/market"

# Public endpoints
GET_INSTRUMENTS = "/public/get-instruments"
GET_TICKER = "/public/get-tickers"
GET_ORDER_BOOK = "/public/get-book"

# Private endpoints
PRIVATE_USER_BALANCE = "/private/user-balance"
PRIVATE_CREATE_ORDER = "/private/create-order"
PRIVATE_CANCEL_ORDER = "/private/cancel-order"
PRIVATE_GET_OPEN_ORDERS = "/private/get-open-orders"
PRIVATE_GET_ORDER_DETAIL = "/private/get-order-detail"
PRIVATE_GET_ORDER_HISTORY = "/private/get-order-history"
PRIVATE_GET_TRADES = "/private/get-trades"

# Rate limits (per Crypto.com docs)
RATE_LIMITS = [
    RateLimit(limit_id=GET_TICKER, limit=100, time_interval=1),
    RateLimit(limit_id=GET_ORDER_BOOK, limit=100, time_interval=1),
    RateLimit(limit_id=GET_INSTRUMENTS, limit=100, time_interval=1),
    RateLimit(limit_id=PRIVATE_USER_BALANCE, limit=3, time_interval=0.1),
    RateLimit(limit_id=PRIVATE_CREATE_ORDER, limit=15, time_interval=0.1),
    RateLimit(limit_id=PRIVATE_CANCEL_ORDER, limit=15, time_interval=0.1),
    RateLimit(limit_id=PRIVATE_GET_OPEN_ORDERS, limit=3, time_interval=0.1),
    RateLimit(limit_id=PRIVATE_GET_ORDER_DETAIL, limit=30, time_interval=0.1),
    RateLimit(limit_id=PRIVATE_GET_ORDER_HISTORY, limit=1, time_interval=1),
    RateLimit(limit_id=PRIVATE_GET_TRADES, limit=1, time_interval=1),
]

ORDER_SIDE_BUY = "BUY"
ORDER_SIDE_SELL = "SELL"
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_MARKET = "MARKET"
ORDER_STATUS_ACTIVE = "ACTIVE"
ORDER_STATUS_FILLED = "FILLED"
ORDER_STATUS_CANCELED = "CANCELED"
ORDER_STATUS_REJECTED = "REJECTED"
ORDER_STATUS_EXPIRED = "EXPIRED"
