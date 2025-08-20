import argparse
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from core.bybit_exchange import normalize_symbol, create_exchange
from core.env_loader import load_and_check_env
from core.market_info import (
    cancel_open_orders,
    get_balance,
    get_open_orders,
    get_symbol_price,
    has_open_position,
)

from core.predict import predict_trend, train_model_for_pair
from core.trailing_stop import update_trailing_for_symbol, verify_trailing_state, set_stop_loss_only, compute_atr
from position_manager import open_position
from core.indicators import compute_snapshot

_BE_DONE = {}

def _has_trailing(exchange, symbol: str) -> bool:
    try:
        st = verify_trailing_state(exchange, symbol)
        rows = (st.get("result", {}) or {}).get("list") or []
        for r in rows:
            ts = str(r.get("trailingStop") or "").strip()
            if ts not in ("", "0", "0.0", "None"):
                return True
    except Exception:
        pass
    return False

def _maybe_breakeven(exchange, symbol: str, entry_px: float, side: str) -> None:
    """Перевод SL в безубыток при достижении X*ATR (или процента) — по ENV."""
    if os.getenv("ENABLE_BREAKEVEN", "1") != "1":
        return
    sid = (side or "").lower()
    key = (symbol, sid)
    if _BE_DONE.get(key):
        return

    be_mode = os.getenv("BE_MODE", "atr").lower()  # atr | pct
    be_offset_pct = float(os.getenv("BE_OFFSET_PCT", "0.0005"))

    cur = get_symbol_price(symbol)
    should_move = False

    if be_mode == "atr":
        k = float(os.getenv("BE_ATR_K", "0.5"))
        tf = os.getenv("ATR_TIMEFRAME", "5m")
        per = int(os.getenv("ATR_PERIOD", "14"))
        atr, _ = compute_atr(exchange, symbol, tf, per)
        if atr > 0:
            if sid in ("long", "buy"):
                should_move = cur >= entry_px + k * atr
            else:
                should_move = cur <= entry_px - k * atr
    else:
        trig = float(os.getenv("BE_TRIGGER_PCT", "0.004"))
        if sid in ("long", "buy"):
            should_move = cur >= entry_px * (1 + trig)
        else:
            should_move = cur <= entry_px * (1 - trig)

    if not should_move:
        return

    if sid in ("long", "buy"):
        be_price = float(exchange.price_to_precision(symbol, entry_px * (1 + be_offset_pct)))
    else:
        be_price = float(exchange.price_to_precision(symbol, entry_px * (1 - be_offset_pct)))
    print("[BE] move SL to", be_price)
    try:
        set_stop_loss_only(exchange, symbol, be_price)
        _BE_DONE[key] = True
    except Exception as e:
        print("[BE_ERR]", e)

def apply_trailing_after_entry(sym: str, signal: str, res: dict, dry_run: bool) -> None:
    """
    Вешает трейлинг + брейк-ивен сразу после успешного входа.
    Работает *всегда*, потому что все нужные переменные приходят как аргументы.
    """

    if dry_run or not isinstance(res, dict) or res.get("status") in {"error", "retryable"}:
        print("[TS_SKIP]", {"dry_run": dry_run, "status": res.get("status") if isinstance(res, dict) else "?"})
        return

    try:
        entry_px = float(res.get("price") or 0.0)
        if entry_px <= 0:
            # аккуратный фолбэк на текущую цену
            try:
                entry_px = get_symbol_price(sym)
            except Exception:
                ex_tmp = create_exchange()
                tkr = ex_tmp.fetch_ticker(sym)
                entry_px = float(tkr.get("last") or tkr.get("close") or 0.0)

        ex_ts = create_exchange()

        # если трейл уже висит — не дублируем
        if os.getenv("USE_TRAILING_STOP", "1") in ("1", "true", "True"):
            if not _has_trailing(ex_ts, sym):
                print("[TS_CALL]", {"symbol": sym, "entry": entry_px, "side": signal})
                ts_resp = update_trailing_for_symbol(ex_ts, sym, entry_px, signal)
                print("[TS_OK]", ts_resp)
            else:
                print("[TS_SKIP] already has trailing for", sym)

            _maybe_breakeven(ex_ts, sym, entry_px, signal)
        else:
            print("[TS_SKIP] trailing disabled by USE_TRAILING_STOP")
    except Exception as e:
        print("[TS_ERR]", e)



@contextmanager
def single_instance_lock(name: str = "positions_guard.lock"):
    path = os.path.join(tempfile.gettempdir(), name)
    if os.path.exists(path):
        raise RuntimeError(f"Already running: {path}")
    try:
        open(path, "w").close()
        yield
    finally:
        try:
            os.remove(path)
        except Exception as e:
            print(f"[WARN] lock cleanup failed: {e}")


def ensure_models_exist(pairs, timeframe="15m", limit=2000, model_dir="models"):
    os.makedirs(model_dir, exist_ok=True)
    missing = []
    for p in pairs:
        key = normalize_symbol(p).upper().replace("/", "").replace(":USDT", "")
        mpath = os.path.join(model_dir, f"model_{key}.pkl")
        if not os.path.exists(mpath):
            missing.append(p)
    if missing:
        print(f"🧠 Нет моделей для: {missing} — обучаем...")
        for p in missing:
            try:
                train_model_for_pair(
                    p, timeframe=timeframe, limit=limit, model_dir=model_dir
                )
            except Exception as e:
                print(f"⚠️ {p}: {e}")


def main():
    load_and_check_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Один проход и выход")
    parser.add_argument("--pair", type=str)
    parser.add_argument(
        "--threshold", type=float, default=float(os.getenv("CONF_THRESHOLD", "0.65"))
    )
    parser.add_argument("--timeframe", type=str, default=os.getenv("TIMEFRAME", "5m"))
    parser.add_argument(
        "--limit", type=int, default=int(os.getenv("TRAIN_LIMIT", "3000"))
    )
    parser.add_argument("--live", action="store_true", help="Разрешить реальные сделки")
    parser.add_argument(
        "--autotrain",
        action="store_true",
        help="Обучить недостающие модели перед стартом",
    )
    parser.add_argument(
        "--auto-cancel",
        action="store_true",
        help="Автоотмена открытых ордеров перед входом",
    )
    parser.add_argument(
        "--no-pyramid", action="store_true", help="Не входить, если уже есть позиция"
    )
    args = parser.parse_args()

    pairs = (
        [args.pair]
        if args.pair
        else [s.strip() for s in os.getenv("PAIRS", "").split(",") if s.strip()]
    )
    if not pairs:
        raise ValueError("PAIRS пуст — заполни в .env")

    # предохранители из .env (с дефолтами)
    min_balance = float(
        os.getenv("MIN_BALANCE_USDT", "5")
    )  # минимальный баланс для попытки входа
    dry_run = not args.live

    print("──────── Kolopovstrategy guard ────────")
    print("⏱ ", datetime.now(timezone.utc).isoformat())
    print(f"Mode: {'LIVE' if not dry_run else 'DRY'} | Threshold={args.threshold}")
    print("📈 Pairs:", ", ".join(pairs))

    if args.autotrain:
        ensure_models_exist(pairs, timeframe=args.timeframe, limit=args.limit)

    with single_instance_lock():
        print("DEBUG PROXY_URL:", os.getenv("PROXY_URL"))
        usdt = get_balance("USDT")
        print(f"💰 Баланс USDT: {usdt:.2f}")
        if usdt < min_balance:
            print(f"⛔ Баланс ниже минимума ({min_balance} USDT) — торговля пропущена.")
            return

        # DRY_RUN переменная — двойной предохранитель
        if dry_run:
            os.environ["DRY_RUN"] = "1"
        else:
            os.environ.pop("DRY_RUN", None)

        for p in pairs:
            sym = normalize_symbol(p)
            price = get_symbol_price(sym)

            # 1) Пред‑чек: открытые ордера
            opened = get_open_orders(sym)
            if opened:
                print(f"⏳ Есть открытые ордера по {sym}: {len(opened)}")
                if args.auto_cancel:
                    n = cancel_open_orders(sym)
                    print(f"🧹 Отменил {n} ордер(ов).")
                else:
                    print(
                        "⏸ Пропускаю вход (запусти с --auto-cancel, чтобы чистить хвосты)."
                    )
                    continue

            # 2) Пред‑чек: активная позиция
            if args.no_pyramid and has_open_position(sym):
                print(
                    f"🏕 Уже есть позиция по {sym} — пирамидинг выключен (--no-pyramid). Пропуск."
                )
                continue

            # 3) Прогноз
            pred = predict_trend(sym, timeframe=args.timeframe)
            signal = str(pred.get("signal", "hold")).lower()
            conf = float(pred.get("confidence", 0.0))

            if os.getenv("DEBUG_INDICATORS", "0") == "1":
                try:
                    # ЛЕНИВЫЙ импорт (чтобы линтер не ругался, если флаг=0)
                    snap = compute_snapshot(sym, timeframe=args.timeframe, limit=max(args.limit, 200))
                    print("[IND]", sym, snap)
                except Exception as _e:
                    print("[IND_ERR]", _e)
            print(f"🔮 {sym} @ {price:.4f} → signal={signal} conf={conf:.2f} proba={pred.get('proba', {})}")

            # 4) Вход
            if dry_run or signal not in ("long", "short") or conf < args.threshold:
                print("⏸ Условия входа не выполнены (или DRY).")
                continue

            res = open_position(sym, side=signal)
            print("🧾 Результат:", res)
            apply_trailing_after_entry(sym, signal, res, dry_run)


# AGENT_LOOP: цикличный запуск по CHECK_INTERVAL (ENV), либо единичный при --once
if __name__ == "__main__":

    iv = int(os.getenv("CHECK_INTERVAL", os.getenv("CHECK_INTERVAL_SECONDS", "30")))
    if "--once" in sys.argv:
        main()
    else:
        print(f"[CONFIG] CHECK_INTERVAL={iv}s")
        while True:
            t0 = time.time()
            try:
                main()
            except Exception as e:
                print(f"[LOOP ERR] {e}")
            sleep_for = max(0.0, iv - (time.time() - t0))
            print(
                f"[TICK] took={time.time()-t0:.1f}s | sleep={sleep_for:.1f}s | interval={iv}s"
            )
            time.sleep(sleep_for)
