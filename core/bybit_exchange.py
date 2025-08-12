import os
import ccxt

def create_exchange() -> ccxt.bybit:
    """
    Bybit через ccxt для деривативов (USDT perpetual).
    """
    proxy = os.getenv("PROXY_URL")
    recv_window = int(os.getenv("RECV_WINDOW", "15000"))

    exchange = ccxt.bybit({
        "apiKey": os.getenv("BYBIT_API_KEY"),
        "secret": os.getenv("BYBIT_SECRET_KEY"),
        "enableRateLimit": True,
        "options": {
            "adjustForTimeDifference": True,
            "recvWindow": recv_window,
            "defaultType": "swap",
        },
    })
    if proxy:
        exchange.proxies = {"http": proxy, "https": proxy}

    exchange.load_markets(reload=True)
    return exchange


def normalize_symbol(symbol: str) -> str:
    """
    BTC/USDT -> BTC/USDT:USDT
    """
    s = symbol.upper().replace(" ", "")
    if ":" not in s:
        base, quote = s.split("/")
        s = f"{base}/{quote}:{quote}"
    return s