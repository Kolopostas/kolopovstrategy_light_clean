
import os
import ccxt

def create_exchange() -> ccxt.bybit:
    recv_window = int(os.getenv("RECV_WINDOW", "20000"))
    api_url = os.getenv("BYBIT_API_URL")  # например, https://api.bytick.com или https://api.bybit.com
    use_testnet = os.getenv("BYBIT_TESTNET") in ("1", "true", "True")

    config = {
        "apiKey": os.getenv("BYBIT_API_KEY"),
        "secret": os.getenv("BYBIT_SECRET_KEY"),
        "enableRateLimit": True,
        "options": {
            "adjustForTimeDifference": True,
            "recvWindow": recv_window,
            "defaultType": "swap",
            "testnet": use_testnet,
        },
    }

    proxy = os.getenv("PROXY_URL")
    if proxy:
        config["proxies"] = {"http": proxy, "https": proxy}

    if api_url:
        config["urls"] = {"api": api_url}

    ex = ccxt.bybit(config)

    # Полезный дебаг в логи Railway
    print("DEBUG BYBIT:", {
        "api": api_url or "default",
        "recvWindow": recv_window,
        "testnet": use_testnet,
        "hasProxy": bool(proxy),
    })

    ex.load_markets(reload=True)
    return ex

def normalize_symbol(symbol: str) -> str:
    s = symbol.upper().replace(" ", "")
    if ":" not in s:
        base, quote = s.split("/")
        s = f"{base}/{quote}:{quote}"
    return s