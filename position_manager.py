import os
import time
from typing import Any, Dict, Optional

from core.bybit_exchange import create_exchange, normalize_symbol
from core.market_info import adjust_qty_price
from core.trade_log import append_trade_event


def _calc_order_qty(
    balance_usdt: float, price: float, risk_fraction: float, leverage: int
) -> float:
    notional = max(0.0, balance_usdt) * max(0.0, risk_fraction) * max(1, leverage)
    return (notional / price) if price > 1e-12 else 0.0


def _wait_fill(ex, sym: str, order_id: str, timeout_s: int = 8) -> Dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        o = ex.fetch_order(order_id, sym)
        st = (o.get("status") or "").lower()
        if st in ("closed", "canceled", "rejected"):
            return o
        time.sleep(0.5)
    return o


def open_position(
    symbol: str, side: str, price: Optional[float] = None
) -> Dict[str, Any]:
    """
    MARKET-ордер с TP/SL. Игнорирует 110043, ловит 10001.
    Логирует: order_placed / order_filled / order_error.
    DRY_RUN=1 — не отправляет ордера.
    """
    # DRY mode: ничего не отправляем
    if os.getenv("DRY_RUN", "").strip() == "1":
        return {"status": "dry", "reason": "DRY_RUN=1", "symbol": symbol, "side": side}

    ex = create_exchange()
    sym = normalize_symbol(symbol)

    # Баланс
    bal = ex.fetch_balance()
    usdt = float(bal.get("USDT", {}).get("free", 0.0) or 0.0)

    # Цена
    if price is None:
        t = ex.fetch_ticker(sym)
        price = float(t.get("last") or t.get("close") or 0.0)

    # Риск-параметры
    risk_fraction = float(os.getenv("RISK_FRACTION", "0.2"))
    leverage = int(os.getenv("LEVERAGE", "3"))
    tp_pct = float(os.getenv("TP_PCT", "0.01"))
    sl_pct = float(os.getenv("SL_PCT", "0.005"))

    # Размер
    qty_raw = _calc_order_qty(usdt, price, risk_fraction, leverage)
    qty, px, _ = adjust_qty_price(sym, qty_raw, price)
    if qty <= 0:
        return {
            "status": "error",
            "reason": "qty<=0 after adjust",
            "balance": usdt,
            "qty_raw": qty_raw,
        }

    order_side = "buy" if side.lower() == "long" else "sell"

    # Плечо
    try:
        ex.set_leverage(leverage, sym)
    except Exception as e:
        if "110043" not in str(e):
            print("⚠️ set_leverage:", e)

    # TP/SL
    if order_side == "buy":
        tp_price = float(ex.price_to_precision(sym, px * (1 + tp_pct)))
        sl_price = float(ex.price_to_precision(sym, px * (1 - sl_pct)))
    else:
        tp_price = float(ex.price_to_precision(sym, px * (1 - tp_pct)))
        sl_price = float(ex.price_to_precision(sym, px * (1 + sl_pct)))

    params = {"takeProfit": tp_price, "stopLoss": sl_price}

    # Отладка
    print(
        "🔎 DEBUG ORDER:",
        {
            "symbol": sym,
            "side": order_side,
            "qty_raw": qty_raw,
            "qty": qty,
            "entry_price": px,
            "TP": tp_price,
            "SL": sl_price,
            "lev": leverage,
        },
    )

    try:
        # Размещение
        o = ex.create_order(
            sym, type="market", side=order_side, amount=qty, price=None, params=params
        )

        # Лог: размещён
        try:
            append_trade_event(
                {
                    "ts": time.time(),
                    "event": "order_placed",
                    "symbol": sym,
                    "side": order_side,
                    "qty": qty,
                    "price": px,
                    "tp": tp_price,
                    "sl": sl_price,
                    "order_id": o.get("id") or o.get("orderId"),
                    "link_id": o.get("clientOrderId")
                    or o.get("orderLinkId")
                    or (o.get("info", {}) or {}).get("orderLinkId"),
                    "mode": "LIVE",
                }
            )
        except Exception as _e:
            print("[WARN] trade-log placed:", _e)

        # Дождаться исполнения (best-effort)
        oid = o.get("id") or o.get("orderId")
        if oid:
            o = _wait_fill(ex, sym, oid)

        # Лог: исполнен
        try:
            append_trade_event(
                {
                    "ts": time.time(),
                    "event": "order_filled",
                    "symbol": sym,
                    "side": order_side,
                    "qty": qty,
                    "price": px,
                    "tp": tp_price,
                    "sl": sl_price,
                    "order_id": o.get("id") or oid,
                    "link_id": o.get("clientOrderId")
                    or o.get("orderLinkId")
                    or (o.get("info", {}) or {}).get("orderLinkId"),
                    "mode": "LIVE",
                }
            )
        except Exception as _e:
            print("[WARN] trade-log filled:", _e)

        # Успех
        return {
            "status": (o.get("status") or "unknown"),
            "order": o,
            "qty": qty,
            "price": px,
            "tp": tp_price,
            "sl": sl_price,
            "balance": usdt,
        }

    except Exception as e:
        msg = str(e)

        # Лог: ошибка
        try:
            append_trade_event(
                {
                    "ts": time.time(),
                    "event": "order_error",
                    "symbol": sym,
                    "side": order_side,
                    "qty": qty,
                    "price": px,
                    "tp": tp_price,
                    "sl": sl_price,
                    "order_id": None,
                    "link_id": None,
                    "mode": "LIVE",
                    "extra": msg,
                }
            )
        except Exception as _e:
            print("[WARN] trade-log error:", _e)

        # Коды Bybit
        if "10001" in msg:
            return {
                "status": "retryable",
                "reason": "10001 invalid request",
                "error": msg,
            }
        if "110043" in msg:
            return {
                "status": "ok_with_warning",
                "warning": "110043 leverage not modified",
                "qty": qty,
            }
        return {"status": "error", "error": msg, "qty": qty, "price": px}
