import os
import ccxt

def create_exchange() -> ccxt.bybit:
    recv_window = int(os.getenv("RECV_WINDOW", "20000"))
    api_url = (os.getenv("BYBIT_API_URL") or "").strip().rstrip(";")  # без лишних символов
    use_testnet = os.getenv("BYBIT_TESTNET") in ("1", "true", "True")

    cfg = {
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
        cfg["proxies"] = {"http": proxy, "https": proxy}

    # ВАЖНО: ccxt ждёт словарь public/private, а не строку
    if api_url:
        cfg["urls"] = {
            "api": {
                "public": api_url,
                "private": api_url,
            }
        }

    ex = ccxt.bybit(cfg)

    # Для тестнета ccxt рекомендует sandbox_mode
    try:
        ex.set_sandbox_mode(use_testnet)
    except Exception:
        pass

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