# core/indicators.py
from typing import Dict, List
from core.bybit_exchange import create_exchange

def _ema_last(vals: List[float], period: int) -> float:
    a = 2.0 / (period + 1)
    ema = vals[0]
    for v in vals[1:]:
        ema = a * v + (1 - a) * ema
    return ema

def _rsi_last(vals: List[float], period: int = 14) -> float:
    gains = []
    losses = []
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        if ch >= 0:
            gains.append(ch)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-ch)

    if len(gains) < period:
        return 50.0

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _bb_last(vals: List[float], period: int = 20) -> Dict[str, float]:
    if len(vals) < period:
        m = sum(vals)/len(vals)
        return {"mid": m, "up": m, "dn": m, "width": 0.0}
    s = vals[-period:]
    mid = sum(s)/period
    var = sum((x-mid)**2 for x in s)/period
    sd = var**0.5
    up = mid + 2*sd
    dn = mid - 2*sd
    width = (up-dn)/mid if mid else 0.0
    return {"mid": mid, "up": up, "dn": dn, "width": width}

def compute_snapshot(symbol: str, timeframe: str = "5m", limit: int = 200) -> Dict[str, float]:
    """Лёгкий снимок индикаторов для логов/верификации: EMA, MACD, RSI, BB."""
    ex = create_exchange()
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    closes = [c[4] for c in ohlcv]
    if len(closes) < 60:
        return {}
    ema12 = _ema_last(closes, 12)
    ema26 = _ema_last(closes, 26)
    macd = ema12 - ema26
    # грубый MACD signal(9) — достаточно для визуальной верификации
    macd_series = []
    for i in range(26, len(closes)):
        ema12_i = _ema_last(closes[:i+1], 12)
        ema26_i = _ema_last(closes[:i+1], 26)
        macd_series.append(ema12_i - ema26_i)
    macd_signal = _ema_last(macd_series, 9) if macd_series else 0.0
    rsi = _rsi_last(closes, 14)
    bb = _bb_last(closes, 20)
    return {
        "ema12": round(ema12, 6),
        "ema26": round(ema26, 6),
        "macd": round(macd, 6),
        "macd_signal": round(macd_signal, 6),
        "rsi14": round(rsi, 3),
        "bb_mid": round(bb["mid"], 6),
        "bb_up": round(bb["up"], 6),
        "bb_dn": round(bb["dn"], 6),
        "bb_width": round(bb["width"], 6),
        "close": closes[-1],
    }
