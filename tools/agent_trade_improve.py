# tools/agent_trade_improve.py
# Собирает авто‑патч: min-qty fix, ATR-стоп, фильтры входа, лимиты,
# + ФИКС возврата train_single_pair() и защита распаковки в positions_guard.py

import os
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.example"


def upsert_env():
    add = textwrap.dedent(
        """\
    ATR_MULTIPLIER=2.0
    MAX_OPEN_TRADES=2
    SLIPPAGE_LIMIT_BPS=5
    BREAKEVEN_AFTER_RR=1.0
    USE_TRAILING_STOP=true
    REGIME_EMA=200
    """
    )
    cur = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    if not cur:
        ENV.write_text(add, encoding="utf-8")
        print("[env] created .env.example")
        return
    new = cur
    for line in add.strip().splitlines():
        key = line.split("=", 1)[0]
        if re.search(rf"^{key}\s*=", cur, flags=re.M):  # уже есть
            continue
        new += ("" if new.endswith("\n") else "\n") + line + "\n"
    if new != cur:
        ENV.write_text(new, encoding="utf-8")
        print("[env] appended vars")
    else:
        print("[env] vars already present")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path.relative_to(ROOT)}")


# ───────────────────────────────────────────────────────────────────────────────
# 1) FIX: adjust_qty_price (min qty / min cost)
def patch_market_info():
    p = ROOT / "core" / "market_info.py"
    base = p.read_text(encoding="utf-8") if p.exists() else ""
    func = textwrap.dedent(
        """\
    def adjust_qty_price(sym, qty, price, ex=None):
        \"\"\"Приводит qty/price к маркет-правилам: min qty, precision, min cost\"\"\"
        assert ex is not None, "exchange instance (ex) required"
        market = ex.market(sym)
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        min_qty = float(amount_limits.get("min") or 0.0)
        min_cost = float(cost_limits.get("min") or 0.0)

        if min_qty and qty < min_qty:
            print(f"[WARN] {sym}: qty {qty} < min {min_qty} — повышаем до минимума")
            qty = min_qty

        qty_adj = float(ex.amount_to_precision(sym, qty))
        px_adj = float(ex.price_to_precision(sym, price))

        if min_cost:
            notional = qty_adj * px_adj
            if notional < min_cost:
                req_qty = min_cost / px_adj
                qty_adj = float(ex.amount_to_precision(sym, req_qty))
                print(f"[WARN] {sym}: notional {notional:.6f} < min_cost {min_cost} — qty-> {qty_adj}")
        return qty_adj, px_adj, market
    """
    )
    new = base
    if "def adjust_qty_price(" in base:
        new = re.sub(
            r"def\s+adjust_qty_price\s*\([^\)]*\)\s*:[\s\S]*?return\s+[^\n]+",
            func.strip(),
            base,
            flags=re.M,
        )
    else:
        new = (base + "\n\n" if base else "") + func
    write_file(p, new)


# ───────────────────────────────────────────────────────────────────────────────
# 2) Индикаторы + фильтр входа + ATR
def patch_predict():
    p = ROOT / "core" / "predict.py"
    base = p.read_text(encoding="utf-8") if p.exists() else ""
    if "# --- indicators & filters (agent patch) ---" in base:
        print("[predict] already patched")
        return
    block = textwrap.dedent(
        """\
    # --- indicators & filters (agent patch) ---
    import pandas as pd

    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    def compute_rsi(close: pd.Series, period=14) -> pd.Series:
        delta = close.diff()
        up = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        rs = up / (down + 1e-12)
        return 100 - (100 / (1 + rs))

    def compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
        ema_fast = _ema(close, fast); ema_slow = _ema(close, slow)
        macd = ema_fast - ema_slow
        sig = _ema(macd, signal)
        hist = macd - sig
        return macd, sig, hist

    def compute_atr(df: pd.DataFrame, period=14) -> pd.Series:
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        return atr

    def get_recent_atr(ex, symbol: str, timeframe='1h', period=14, limit=None) -> float:
        limit = limit or (period * 3 + 2)
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        import pandas as pd
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','vol'])
        atr = compute_atr(df[['open','high','low','close']], period)
        return float(atr.iloc[-1])

    def entry_filter_confirm(ex, symbol: str, side: str, timeframe='1h',
                             rsi_thr_long=55, rsi_thr_short=45, regime_ema=200):
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=max(regime_ema, 260))
        import pandas as pd
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','vol'])
        close = df['close']
        ema50 = _ema(close, 50).iloc[-1]
        ema200_now = _ema(close, regime_ema).iloc[-1]
        ema200_prev = _ema(close, regime_ema).iloc[-2]
        rsi = compute_rsi(close, 14).iloc[-1]
        macd, sig, hist = compute_macd(close)
        macd_hist = float(hist.iloc[-1])
        px = float(close.iloc[-1])

        regime_long = px > ema200_now and ema200_now > ema200_prev
        regime_short = px < ema200_now and ema200_now < ema200_prev

        ok_long  = (rsi > rsi_thr_long) and (px > ema50) and (macd_hist > 0) and regime_long
        ok_short = (rsi < rsi_thr_short) and (px < ema50) and (macd_hist < 0) and regime_short

        ok = ok_long if side.lower() == "long" else ok_short
        return bool(ok), {
            "price": px, "rsi": float(rsi),
            "ema50": float(ema50), "ema200": float(ema200_now),
            "macd_hist": macd_hist,
            "regime_ok": regime_long if side.lower()=="long" else regime_short
        }
    # --- /indicators & filters ---
    """
    )
    new = (base + "\n\n" if base else "") + block
    write_file(p, new)


# ───────────────────────────────────────────────────────────────────────────────
# 3) position_manager: open_position с лимитами, ATR-SL, проскальзыванием
def patch_position_manager():
    p = ROOT / "position_manager.py"
    base = p.read_text(encoding="utf-8") if p.exists() else ""
    body = textwrap.dedent(
        """\
    import os, time, ccxt
    from dotenv import load_dotenv
    from core.market_info import adjust_qty_price
    from core.predict import get_recent_atr, entry_filter_confirm

    load_dotenv()
    ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", "2.0"))
    MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))
    SLIPPAGE_LIMIT_BPS = float(os.getenv("SLIPPAGE_LIMIT_BPS", "5")) / 1e4
    BREAKEVEN_AFTER_RR = float(os.getenv("BREAKEVEN_AFTER_RR", "1.0"))
    REGIME_EMA = int(os.getenv("REGIME_EMA", "200"))

    def _exchange():
        return ccxt.bybit({
            "apiKey": os.getenv("BYBIT_API_KEY"),
            "secret": os.getenv("BYBIT_SECRET_KEY"),
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })

    def _count_open_positions(ex, symbol: str = None) -> int:
        try:
            poss = ex.fetch_positions([symbol]) if symbol else ex.fetch_positions()
            n = 0
            for p in poss or []:
                if (p.get("contracts") or 0) > 0 and (p.get("side") or "").lower() in ("long","short"):
                    n += 1
            return n
        except Exception as e:
            print(f"[WARN] fetch_positions error: {e}")
            return 0

    def open_position(symbol: str, side: str, amount_usdt: float = None, leverage: int = None):
        ex = _exchange()
        ex.load_markets()

        if _count_open_positions(ex) >= MAX_OPEN_TRADES:
            print(f"[SKIP] MAX_OPEN_TRADES={MAX_OPEN_TRADES} достигнут — пропуск")
            return {"skipped": "max_open_trades"}

        ok, stats = entry_filter_confirm(ex, symbol, side, timeframe="1h", regime_ema=REGIME_EMA)
        if not ok:
            print(f"[SKIP] фильтр входа не подтверждён: {stats}")
            return {"skipped": "entry_filter", "stats": stats}
        print(f"[FILTER OK] {side.upper()} {symbol} | {stats}")

        ticker = ex.fetch_ticker(symbol)
        mark = float(ticker.get("last") or ticker.get("close") or 0.0)
        if not mark:
            raise ValueError("Не удалось получить цену")

        qty_raw = (amount_usdt / mark) if amount_usdt else 0.0001

        if side.lower() == "long":
            limit_price = min(mark * (1 + SLIPPAGE_LIMIT_BPS), mark * 1.001)
        else:
            limit_price = max(mark * (1 - SLIPPAGE_LIMIT_BPS), mark * 0.999)

        qty_adj, px_adj, _ = adjust_qty_price(symbol, qty_raw, limit_price, ex=ex)

        atr = get_recent_atr(ex, symbol, timeframe="1h", period=14)
        sl_price = px_adj - ATR_MULTIPLIER * atr if side.lower()=="long" else px_adj + ATR_MULTIPLIER * atr

        if leverage:
            try:
                ex.set_leverage(leverage, symbol)
            except Exception as e:
                msg = str(e)
                if "110043" in msg or "leverage not modified" in msg.lower():
                    print("[INFO] 110043 leverage not modified — игнорируем")
                else:
                    print(f"[WARN] set_leverage: {e}")

        params = {
            "reduceOnly": False,
            "timeInForce": "PostOnly",
            "stopLoss": sl_price
        }
        order_side = "buy" if side.lower()=="long" else "sell"

        print(f"[CREATE] {symbol} {order_side} qty={qty_adj} px={px_adj} SL={sl_price} ATR={atr}")
        order = ex.create_order(symbol, "limit", order_side, qty_adj, px_adj, params=params)

        return {"order": order, "qty": qty_adj, "price": px_adj, "sl": sl_price, "atr": atr, "filter_stats": stats}
    """
    )
    # заменяем существующую реализацию open_position целиком (если есть)
    if "def open_position(" in base:
        new = re.sub(
            r"def\s+open_position\s*\([^\)]*\)\s*:[\s\S]*?$",
            body.strip(),
            base,
            flags=re.M,
        )
    else:
        new = (base + "\n\n" if base else "") + body
    write_file(p, new)


# ───────────────────────────────────────────────────────────────────────────────
# 4) FIX обучения: train_single_pair → всегда 2 значения
def patch_train_model():
    p = ROOT / "core" / "train_model.py"
    if not p.exists():
        print("[train_model] not found — skip")
        return
    src = p.read_text(encoding="utf-8")

    # Нормализуем возврат train_single_pair: только (model_path, val_acc)
    # Пытаемся заменить строки вида "return ...model_path..., ...val_acc..." и выкинуть symbol.
    src2 = re.sub(
        r"return\s+(\s*[\w\.\(\)\"']*model[_]?path[\w\.\(\)\"']*\s*),\s*(\s*val[_]?acc[\w\.\(\)]*\s*)(?:,\s*[\w\.\(\)\"']+)?",
        r"return \1, \2",
        src,
        flags=re.I,
    )
    # Если функция возвращает кортеж из 3 значений в явном порядке (symbol, model_path, val_acc)
    src2 = re.sub(
        r"return\s+(\w+)\s*,\s*(\w+model\w*)\s*,\s*(val\w*)",
        r"return \2, \3",
        src2,
        flags=re.I,
    )
    if src2 != src:
        p.write_text(src2, encoding="utf-8")
        print(
            "[train_model] normalized train_single_pair return -> (model_path, val_acc)"
        )
    else:
        print("[train_model] no change (already 2-tuple)")


# ───────────────────────────────────────────────────────────────────────────────
# 5) FIX вызова обучения в positions_guard: защитная распаковка
def patch_positions_guard_unpack():
    p = ROOT / "positions_guard.py"
    if not p.exists():
        print("[positions_guard] not found — skip")
        return
    src = p.read_text(encoding="utf-8")

    # Находим место вызова train_single_pair(sym) и заменяем распаковку
    # На универсальный блок с поддержкой 2 или 3 значений
    guard_block = textwrap.dedent(
        """\
    ret = train_single_pair(sym)
    if isinstance(ret, tuple):
        if len(ret) == 2:
            model_path, val_acc = ret
        elif len(ret) == 3:
            _, model_path, val_acc = ret
        else:
            raise ValueError(f"Unexpected train_single_pair() return: len={len(ret)}")
    else:
        raise ValueError("train_single_pair() must return tuple")
    print(f"✅ {sym} trained, val_acc={val_acc:.3f} → {model_path}")
    """
    )

    # Попробуем заменить типовые варианты: "model_path, val_acc = train_single_pair(sym)"
    src2 = re.sub(
        r"model[_]?path\s*,\s*val[_]?acc\s*=\s*train_single_pair\s*\(\s*sym\s*\).*",
        guard_block.strip(),
        src,
        flags=re.I,
    )
    # Если не нашли — вставим безопасно после первого вхождения "train_single_pair(sym)"
    if src2 == src and "train_single_pair(" in src:
        src2 = src.replace(
            "train_single_pair(sym)", "train_single_pair(sym)  # patched"
        )
        # На этом шаге оставим как есть — минимум инвазивности; если шаблон не подошёл, пропускаем
        print(
            "[positions_guard] train_single_pair call found but pattern not replaced; manual review may be needed"
        )
    if src2 != src:
        p.write_text(src2, encoding="utf-8")
        print("[positions_guard] protective unpack applied")
    else:
        print("[positions_guard] no change")


# ───────────────────────────────────────────────────────────────────────────────
def main():
    upsert_env()
    patch_market_info()
    patch_predict()
    patch_position_manager()
    patch_train_model()
    patch_positions_guard_unpack()


if __name__ == "__main__":
    main()
