# core/trailing_stop.py
import os
import time
import logging
from typing import Dict, Any, List

logger = logging.getLogger("trailing_stop")

try:
    import ccxt  # type: ignore
except Exception:
    ccxt = None

_RATE_DELAY = float(os.getenv("BYBIT_RATE_LIMIT_DELAY", "0.4"))  # 3 rps ~= 0.33s


# ---------------- helpers ----------------
def _market_id(exchange, unified_symbol: str) -> str:
    """BTC/USDT:USDT -> BTCUSDT (id для Bybit v5)."""
    exchange.load_markets(reload=False)
    m = exchange.market(unified_symbol)
    return m["id"]


def _assert_ok(resp: Dict[str, Any]) -> None:
    """Поднимаем исключение, если Bybit вернул ошибку; 110043 трактуем как OK."""
    rc = resp.get("retCode")
    if rc in (0, "0", None):
        return
    if str(rc) == "110043":  # not modified
        logger.warning("Bybit retCode=110043 (not modified) — считаем как OK")
        return
    raise RuntimeError(f"Bybit error retCode={rc}, retMsg={resp.get('retMsg')}, result={resp.get('result')}")


def _fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int) -> List[List[float]]:
    # ohlcv: [ts, open, high, low, close, volume]
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def _sma(vals: List[float], period: int) -> float:
    n = len(vals)
    if n < period or period <= 0:
        return sum(vals) / max(1, n)
    return sum(vals[-period:]) / float(period)


def compute_atr(
    exchange,
    symbol: str,
    timeframe: str = "5m",
    period: int = 14,
    *,
    limit: int | None = None
) -> tuple[float, float]:
    """
    Возвращает (atr, last_close).

    ATR (Wilder, SMA):
      TR = max(high - low, |high - prev_close|, |low - prev_close|)
      ATR = SMA(TR, period)
    """
    if limit is None:
        limit = max(period + 1, 100)

    ohlcv = _fetch_ohlcv(exchange, symbol, timeframe, limit)
    if len(ohlcv) < period + 1:
        last_close = float(ohlcv[-1][4]) if ohlcv else 0.0
        return 0.0, last_close

    true_ranges: list[float] = []
    for i in range(1, len(ohlcv)):
        high = float(ohlcv[i][2])
        low = float(ohlcv[i][3])
        prev_close = float(ohlcv[i - 1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    atr = _sma(true_ranges, period)
    last_close = float(ohlcv[-1][4])
    return float(atr), last_close


# --------------- core API wrappers ---------------
def set_trailing_stop_ccxt(
    exchange,
    symbol: str,
    activation_price: float,
    callback_rate: float = 1.0,
    *,
    category: str = "linear",
    tpsl_mode: str = "Full",
    position_idx: int = 0,          # 0(one-way), 1(Long), 2(Short)
    trigger_by: str = "LastPrice",
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    POST /v5/position/trading-stop через ccxt (privatePostV5PositionTradingStop).
    ВАЖНО: числовые параметры — строками.
    """
    bybit_symbol = _market_id(exchange, symbol)
    payload = {
        "category": category,
        "symbol": bybit_symbol,
        "tpslMode": tpsl_mode,
        "positionIdx": position_idx,
        "trailingStop": f"{callback_rate}",     # строка!
        "activePrice": f"{activation_price}",   # строка!
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "tpTriggerBy": trigger_by,
        "slTriggerBy": trigger_by,
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = exchange.privatePostV5PositionTradingStop(payload)
            _assert_ok(resp)
            time.sleep(_RATE_DELAY)  # базовая пауза против 10006/429
            return resp
        except Exception as e:
            msg = str(e)
            if "10006" in msg or "rate limit" in msg.lower():
                if attempt >= max_retries:
                    raise
                backoff = min(_RATE_DELAY * (2 ** (attempt - 1)), 2.0)
                logger.warning("[TS][RETRY %d] rate limit, sleep=%.2fs", attempt, backoff)
                time.sleep(backoff)
                continue
            raise


def verify_trailing_state(exchange, symbol: str, *, category: str = "linear") -> Dict[str, Any]:
    """GET /v5/position/list — посмотреть текущие параметры позиции (включая trailingStop/stopLoss)."""
    bybit_symbol = _market_id(exchange, symbol)
    return exchange.privateGetV5PositionList({"category": category, "symbol": bybit_symbol})


def set_stop_loss_only(
    exchange, symbol: str, stop_price: float,
    *, category: str = "linear", position_idx: int = 0, trigger_by: str = "LastPrice"
) -> Dict[str, Any]:
    """
    Переставляет только StopLoss через тот же endpoint /v5/position/trading-stop (Full).
    Удобно для перевода в безубыток.
    """
    bybit_symbol = _market_id(exchange, symbol)
    payload = {
        "category": category,
        "symbol": bybit_symbol,
        "positionIdx": position_idx,
        "tpslMode": "Full",
        "stopLoss": f"{stop_price}",
        "slOrderType": "Market",
        "slTriggerBy": trigger_by,
    }
    resp = exchange.privatePostV5PositionTradingStop(payload)
    _assert_ok(resp)
    time.sleep(_RATE_DELAY)
    return resp


# --------------- high-level: activation logic (ATR/PCT) ---------------
def update_trailing_for_symbol(
    exchange,
    symbol: str,
    entry_price: float,
    side: str,
    *,
    activation_mode: str | None = None,   # atr | pct
    atr_timeframe: str | None = None,
    atr_period: int | None = None,
    atr_k: float | None = None,
    up_pct: float | None = None,
    down_pct: float | None = None,
    callback_rate: float | None = None,
    auto_callback: bool | None = None,
    auto_cb_k: float | None = None,
) -> Dict[str, Any]:
    """
    Ставит трейлинг-стоп с активацией:
      mode="atr":  LONG → entry + K*ATR ; SHORT → entry - K*ATR
      mode="pct":  LONG → entry*(1+up_pct) ; SHORT → entry*(1-down_pct)
    Параметры читаются из .env при отсутствии аргументов.
    """
    activation_mode = (activation_mode or os.getenv("TS_ACTIVATION_MODE", "atr")).lower()
    atr_timeframe = atr_timeframe or os.getenv("ATR_TIMEFRAME", "5m")
    atr_period = int(atr_period or int(os.getenv("ATR_PERIOD", "14")))
    atr_k = float(atr_k or float(os.getenv("TS_ACTIVATION_ATR_K", "1.0")))

    up_pct = float(os.getenv("TS_ACTIVATION_UP_PCT", "0.003")) if up_pct is None else up_pct
    down_pct = float(os.getenv("TS_ACTIVATION_DOWN_PCT", "0.003")) if down_pct is None else down_pct
    min_up_pct = float(os.getenv("TS_ACTIVATION_MIN_UP_PCT", "0.001"))
    min_dn_pct = float(os.getenv("TS_ACTIVATION_MIN_DOWN_PCT", "0.001"))

    callback_rate = float(os.getenv("TS_CALLBACK_RATE", "1.0")) if callback_rate is None else callback_rate
    auto_callback = bool(int(os.getenv("TS_CALLBACK_RATE_AUTO", "0"))) if auto_callback is None else auto_callback
    auto_cb_k = float(os.getenv("TS_CALLBACK_RATE_ATR_K", "0.75")) if auto_cb_k is None else auto_cb_k

    side_l = (side or "").lower()

    if activation_mode == "atr":
        atr, _ = compute_atr(exchange, symbol, atr_timeframe, atr_period)
        if atr > 0:
            offset = atr_k * atr
            if side_l in ("long", "buy"):
                base = entry_price * (1.0 + min_up_pct)
                active = max(entry_price + offset, base)
            else:
                base = entry_price * (1.0 - min_dn_pct)
                active = min(entry_price - offset, base)
            if auto_callback:
                pct = max(atr / entry_price * 100.0 * auto_cb_k, 0.1)
                callback_rate = max(0.1, min(pct, 5.0))  # лимиты Bybit
            logger.info("[TS_ACTIVE][ATR] %s side=%s entry=%.6f atr=%.6f k=%.3f active=%.6f cb=%.3f%%",
                        symbol, side_l, entry_price, atr, atr_k, active, callback_rate)
        else:
            activation_mode = "pct"

    if activation_mode != "atr":
        if side_l in ("long", "buy"):
            active = entry_price * (1.0 + max(min_up_pct, up_pct))
        else:
            active = entry_price * (1.0 - max(min_dn_pct, down_pct))
        logger.info("[TS_ACTIVE][PCT] %s side=%s entry=%.6f up=%.4f down=%.4f active=%.6f cb=%.3f%%",
                    symbol, side_l, entry_price, up_pct, down_pct, active, callback_rate)

    try:
        active_precise = float(exchange.price_to_precision(symbol, active))
    except Exception:
        active_precise = active

    return set_trailing_stop_ccxt(
        exchange=exchange,
        symbol=symbol,
        activation_price=active_precise,
        callback_rate=callback_rate,
        category="linear",
        tpsl_mode="Full",
        position_idx=0,
        trigger_by="LastPrice",
    )

logger = logging.getLogger("trailing_stop")

try:
    import ccxt  # type: ignore
except Exception:
    ccxt = None

_RATE_DELAY = float(os.getenv("BYBIT_RATE_LIMIT_DELAY", "0.4"))

def _market_id(exchange, unified_symbol: str) -> str:
    exchange.load_markets(reload=False)
    return exchange.market(unified_symbol)["id"]

def _assert_ok(resp: Dict[str, Any]) -> None:
    rc = resp.get("retCode")
    if rc in (0, "0", None): return
    if str(rc) == "110043":
        logger.warning("Bybit retCode=110043 (not modified) — treat as OK")
        return
    raise RuntimeError(f"Bybit error retCode={rc}, retMsg={resp.get('retMsg')}")

def _backoff_sleep(attempt: int) -> None:
    backoff = min(_RATE_DELAY * (2 ** (attempt - 1)), 2.0)
    time.sleep(backoff)

def set_trailing_stop_ccxt(exchange, symbol: str, activation_price: float, callback_rate: float,
                           *, category: str = "linear", tpsl_mode: str = "Full",
                           position_idx: int = 0, trigger_by: str = "LastPrice",
                           max_retries: int = 3) -> Dict[str, Any]:
    """POST /v5/position/trading-stop — вешаем трейлинг на открытую позицию."""
    bybit_symbol = _market_id(exchange, symbol)
    payload = {
        "category": category,
        "symbol": bybit_symbol,
        "tpslMode": tpsl_mode,
        "positionIdx": position_idx,
        "trailingStop": f"{callback_rate}",     # % как строка
        "activePrice": f"{activation_price}",   # строка
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "tpTriggerBy": trigger_by,
        "slTriggerBy": trigger_by,
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = exchange.privatePostV5PositionTradingStop(payload)
            _assert_ok(resp)
            time.sleep(_RATE_DELAY)
            return resp
        except Exception as e:
            msg = str(e)
            if "rate limit" in msg.lower() or "10006" in msg:
                if attempt >= max_retries: raise
                _backoff_sleep(attempt); continue
            raise

def move_stop_loss(exchange, symbol: str, new_sl_price: float, *,
                   category: str = "linear", position_idx: int = 0, trigger_by: str = "LastPrice") -> Dict[str, Any]:
    """Переносим SL (breakeven) на /v5/position/trading-stop."""
    bybit_symbol = _market_id(exchange, symbol)
    payload = {
        "category": category,
        "symbol": bybit_symbol,
        "positionIdx": position_idx,
        "stopLoss": f"{new_sl_price}",
        "slOrderType": "Market",
        "slTriggerBy": trigger_by,
    }
    resp = exchange.privatePostV5PositionTradingStop(payload)
    _assert_ok(resp)
    time.sleep(_RATE_DELAY)
    return resp

def compute_trailing_from_atr(entry: float, side: str, atr: float, *,
                              k_activate: float, min_up_pct: float, min_down_pct: float,
                              cb_from_atr_k: float, cb_fixed_pct: float, auto_cb: bool) -> tuple[float, float]:
    """
    Возвращает (activePrice, callback_rate_pct).
    - Long: активируем, когда цена прошла +max(k*ATR, min_up_pct*entry) выше entry.
    - Short: активируем, когда цена прошла -max(k*ATR, min_down_pct*entry) ниже entry.
    - callback_rate либо фиксированный %, либо из ATR: 100 * (cb_from_atr_k * ATR / entry)
    """
    side_l = side.lower()
    if side_l in ("long", "buy"):
        activate = entry + max(k_activate * atr, entry * min_up_pct)
    else:
        activate = entry - max(k_activate * atr, entry * min_down_pct)

    if auto_cb:
        cb = max(0.1, min(5.0, 100.0 * (cb_from_atr_k * atr / entry)))  # 0.1%..5.0%
    else:
        cb = cb_fixed_pct
    return float(activate), float(cb)

def maybe_breakeven(entry: float, side: str, last: float, atr: float,
                    *, be_mode: str, be_atr_k: float, be_trigger_pct: float, be_offset_pct: float) -> float | None:
    """
    Возвратит новую цену SL (для BE) или None.
    - ATR-режим: как только цена ушла на be_atr_k*ATR, переносим SL ~ к entry*(1±offset).
    - pct-режим: триггер по проценту от entry.
    """
    side_l = side.lower()
    if be_mode == "atr":
        need = be_atr_k * atr
        in_profit = (last - entry) if side_l in ("long", "buy") else (entry - last)
        if in_profit >= need:
            if side_l in ("long", "buy"):
                return entry * (1.0 + be_offset_pct)
            else:
                return entry * (1.0 - be_offset_pct)
    else:
        need = entry * be_trigger_pct
        if (side_l in ("long", "buy") and last >= entry + need) or (side_l in ("short", "sell") and last <= entry - need):
            return entry * (1.0 + be_offset_pct) if side_l in ("long", "buy") else entry * (1.0 - be_offset_pct)
    return None
