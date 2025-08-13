import os
import argparse
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

from core.env_loader import load_and_check_env
from core.bybit_exchange import normalize_symbol
from core.market_info import (
    get_balance, get_symbol_price,
    get_open_orders, cancel_open_orders, has_open_position
)
from core.predict import predict_trend, train_model_for_pair
from position_manager import open_position

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
        except Exception:
            pass

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
                train_model_for_pair(p, timeframe=timeframe, limit=limit, model_dir=model_dir)
            except Exception as e:
                print(f"⚠️ {p}: {e}")

def main():
    load_and_check_env()

    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", type=str)
    parser.add_argument("--threshold", type=float, default=float(os.getenv("CONF_THRESHOLD", "0.65")))
    parser.add_argument("--timeframe", type=str, default=os.getenv("TIMEFRAME", "5m"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TRAIN_LIMIT", "3000")))
    parser.add_argument("--live", action="store_true", help="Разрешить реальные сделки")
    parser.add_argument("--autotrain", action="store_true", help="Обучить недостающие модели перед стартом")
    parser.add_argument("--auto-cancel", action="store_true", help="Автоотмена открытых ордеров перед входом")
    parser.add_argument("--no-pyramid", action="store_true", help="Не входить, если уже есть позиция")
    args = parser.parse_args()

    pairs = [args.pair] if args.pair else [s.strip() for s in os.getenv("PAIRS","").split(",") if s.strip()]
    if not pairs:
        raise ValueError("PAIRS пуст — заполни в .env")

    # предохранители из .env (с дефолтами)
    min_balance = float(os.getenv("MIN_BALANCE_USDT", "5"))  # минимальный баланс для попытки входа
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
                    print("⏸ Пропускаю вход (запусти с --auto-cancel, чтобы чистить хвосты).")
                    continue

            # 2) Пред‑чек: активная позиция
            if args.no_pyramid and has_open_position(sym):
                print(f"🏕 Уже есть позиция по {sym} — пирамидинг выключен (--no-pyramid). Пропуск.")
                continue

            # 3) Прогноз
            pred = predict_trend(sym, timeframe=args.timeframe)
            signal = str(pred.get("signal", "hold")).lower()
            conf = float(pred.get("confidence", 0.0))
            print(f"🔮 {sym} @ {price:.4f} → signal={signal} conf={conf:.2f} proba={pred.get('proba', {})}")

            # 4) Вход
            if dry_run or signal not in ("long", "short") or conf < args.threshold:
                print("⏸ Условия входа не выполнены (или DRY).")
                continue

            res = open_position(sym, side=signal)
            print("🧾 Результат:", res)

if __name__ == "__main__":
    main()
