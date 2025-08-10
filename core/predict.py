from typing import Dict, Any, Tuple
import pandas as pd

try:
    import ccxt  # type: ignore
except Exception:
    ccxt = None

def compute_ema(series: pd.Series, span: int = 50) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / (avg_loss.replace(0, 1e-9))
    return 100 - (100 / (1 + rs))

def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    return macd, macd_signal

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def fetch_ohlcv_df(symbol: str, timeframe: str = "1h", limit: int = 300, proxies: dict = None) -> pd.DataFrame:
    if ccxt is None:
        raise RuntimeError("ccxt is not installed. Please add it to requirements and install.")
    exchange = ccxt.bybit({
        "enableRateLimit": True,
        "proxies": proxies or {},
        "options": {"defaultType": "swap"},
    })
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    if ":USDT" not in symbol:
        symbol = symbol.replace("/USDT", "USDT:USDT")
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)    
    return df

def suggest_leverage(conf: float) -> int:
    if conf >= 0.85:
        return 10
    if conf >= 0.78:
        return 7
    if conf >= 0.70:
        return 5
    return 3

def predict_trend(symbol: str, proxy_url: str = "") -> Dict[str, Any]:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else {}
    df = fetch_ohlcv_df(symbol, timeframe="1h", limit=200, proxies=proxies)
    price = df["close"]
    ema50 = compute_ema(price, 50)
    macd, macd_sig = compute_macd(price)
    atr14 = compute_atr(df, 14)

    last_close = float(price.iloc[-1])
    last_ema = float(ema50.iloc[-1])
    last_macd = float(macd.iloc[-1])
    last_sig = float(macd_sig.iloc[-1])
    last_atr = float(atr14.iloc[-1] if pd.notna(atr14.iloc[-1]) else 0.0)

    if last_close > last_ema and last_macd > last_sig:
        signal = "LONG"
        confidence = 0.78
        tp = last_close + 1.5 * last_atr if last_atr > 0 else last_close * 1.008
        sl = last_close - 1.0 * last_atr if last_atr > 0 else last_close * 0.994
    else:
        signal = "SHORT"
        confidence = 0.75
        tp = last_close - 1.5 * last_atr if last_atr > 0 else last_close * 0.992
        sl = last_close + 1.0 * last_atr if last_atr > 0 else last_close * 1.006

    return {
        "signal": signal,
        "confidence": float(round(confidence, 4)),
        "price": last_close,
        "tp": float(round(tp, 6)),
        "sl": float(round(sl, 6)),
        "leverage": suggest_leverage(confidence),
    }
