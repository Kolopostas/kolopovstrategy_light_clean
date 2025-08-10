# positions_guard.py
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse

from core.env_loader import load_and_check_env
from core.predict import predict_trend
from core.time_utils import compare_bybit_time
from core.trade_log import log_trade, should_pause_pair
from core.train_model import train_models
from core.market_info import get_instrument_info, adjust_qty_price, get_available_usdt
from core.env_loader import normalize_symbol
from position_manager import make_session, set_leverage, open_position

SLEEP_SEC_PER_PAIR = int(os.getenv("SLEEP_SEC_PER_PAIR", "2"))

# ---------------- Helpers ----------------
def _model_path(symbol: str) -> Path:
    """./models/model_TONUSDT.pkl"""
    return Path("models") / f"model_{symbol.replace('/','').upper()}.pkl"

def _need_retrain(symbol: str, max_age_days: int) -> bool:
    p = _model_path(symbol)
    if not p.exists():
        return True
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return age > timedelta(days=max_age_days)

def _maybe_retrain(pairs, proxy_url: str):
    auto = str(os.getenv("AUTO_RETRAIN", "true")).lower() in ("1", "true", "yes")
    if not auto:
        print("🔧 AUTO_RETRAIN=false — пропускаю автотренировку")
        return
    max_age = int(os.getenv("MODEL_MAX_AGE_DAYS", "1"))
    to_train = [s for s in pairs if _need_retrain(s, max_age)]
    if to_train:
        print(f"🔁 Обучаю модели: {to_train}")
        res = train_models(to_train, proxy_url=proxy_url)
        for pair, path, status in res:
            print(f"   • {pair}: {status} {path if path else ''}")
    else:
        print("✅ Модели актуальны — обучение не требуется")

def _print_header(cfg, pairs):
    mode = "DRY_RUN" if cfg["DRY_RUN"] else "LIVE"
    print("──────── Kolopovstrategy guard ────────")
    print(f"⏱  {datetime.now(timezone.utc).isoformat()}")
    print(f"🛠  MODE: {mode} | DOMAIN: {cfg['DOMAIN']} | LEV={cfg['LEVERAGE']} | AMOUNT={cfg['AMOUNT']}")
    print(f"🎯 Pairs: {', '.join(pairs)}")
    try:
        delta, srv = compare_bybit_time()
        if delta > 1.0:
            print(f"⚠️  Time drift ~{delta:.2f}s vs Bybit; увеличь RECV_WINDOW (сейчас {cfg['RECV_WINDOW']})")
        else:
            print(f"🕒 Bybit time check OK (Δ≈{delta:.2f}s)")
    except Exception as e:
        print("⚠️  Time check failed:", e)
    print("───────────────────────────────────────")

# ---------------- Main cycle ----------------
def run_once(pairs_override=None, skip_train=False):
    cfg = load_and_check_env(required_keys=["BYBIT_API_KEY", "BYBIT_SECRET_KEY"])

    pairs = pairs_override if pairs_override else cfg["PAIRS"]
    if not pairs:
        print("❌ Нет пар для обработки (PAIRS пуст).")
        return

    _print_header(cfg, pairs)
    if not skip_train:
        _maybe_retrain(pairs, proxy_url=cfg["PROXY_URL"])

    session = None if cfg["DRY_RUN"] else make_session(cfg["API_KEY"], cfg["API_SECRET"], cfg["DOMAIN"])

    for pair in pairs:
        try:
            if should_pause_pair(pair):
                print(f"⏸  Пропуск (pause active): {pair}")
                continue

            pred = predict_trend(pair, proxy_url=cfg["PROXY_URL"])
            direction = pred["signal"].lower()
            conf = pred["confidence"]
            lev = pred.get("leverage", cfg["LEVERAGE"])
            last = pred["price"]
            tp = pred["tp"]
            sl = pred["sl"]

            print(f"📈 {pair}: {pred['signal']} conf={conf:.3f} price={last} tp={tp} sl={sl} lev={lev}")

            if cfg["DRY_RUN"]:
                print("🧪 DRY_RUN — сделка НЕ отправлена")
            else:
                # Страховка: не превышаем конфиговый предел плеча
                eff_lev = max(1, min(int(lev), int(cfg["LEVERAGE"])))
                set_leverage(session, pair, eff_lev)

            if cfg["DRY_RUN"]:
                print("🧪 DRY_RUN — сделка НЕ отправлена")
            else:
                eff_lev = max(1, min(int(lev), int(cfg["LEVERAGE"])))
                set_leverage(session, pair, eff_lev)

                # --- Новое: подтянем правила инструмента и подстроим qty ---
                info = get_instrument_info(session, normalize_symbol(pair), category="linear")
                qty_planned = float(cfg["AMOUNT"])
                px = float(last)  # по рынку price не требуется, но для проверки минималок используем last
                qty_adj, _ = adjust_qty_price(info, qty_planned, px)

                # (Дополнительно можно проверить доступную маржу:)
                # avail = get_available_usdt(session)
                # fee = 0.0006  # пример для taker
                # need = (px * qty_adj) / eff_lev * 1.01 + (px * qty_adj) * fee
                # if avail < need:
                #     # уменьшаем qty пропорционально
                #     scale = max(0.1, avail / max(need, 1e-9))
                #     qty_adj, _ = adjust_qty_price(info, qty_adj * scale, px)
                #     print(f"⚠️  Мало USDT: уменьшаю qty → {qty_adj}")

                resp = open_position(
                    session=session,
                    symbol_pair=pair,
                    direction=direction,
                    qty=qty_adj,                       # <<< используем скорректированное qty
                    order_type="Market",
                    recv_window=cfg["RECV_WINDOW"],
                )


                resp = open_position(
                    session=session,
                    symbol_pair=pair,
                    direction=direction,
                    qty=cfg["AMOUNT"],
                    order_type="Market",         # можно сменить на Limit при желанииrecv_window=cfg["RECV_WINDOW"],
                )
                ret = resp.get("response", {})
                code = ret.get("retCode")
                msg = ret.get("retMsg")
                if code and code != 0:
                    print(f"❗ Bybit retCode={code} msg={msg}")
                else:
                    print("📝 Order OK:", {k: ret[k] for k in ret if k in ("retCode","retMsg")})

            log_trade({
                "ts": datetime.now(timezone.utc).isoformat(),
                "pair": pair,
                "signal": pred["signal"],
                "confidence": f"{conf:.4f}",
                "price": f"{last}",
                "tp": f"{tp}",
                "sl": f"{sl}",
                "dry_run": str(cfg["DRY_RUN"]),
            })
        except KeyboardInterrupt:
            print("⛔ Остановлено пользователем")
            return
        except Exception as e:
            print(f"❌ {pair}: ошибка {e}")

        time.sleep(SLEEP_SEC_PER_PAIR)

# ---------------- CLI ----------------
def _parse_args():
    ap = argparse.ArgumentParser(description="Kolopovstrategy position guard")
    ap.add_argument("--pair", help="Запустить только по одной паре (например, TON/USDT)")
    ap.add_argument("--no-train", action="store_true", help="Не обучать модели при запуске")
    return ap.parse_args()

if __name__ == "__main__":
    args = _parse_args()
    override = [args.pair] if args.pair else None
    run_once(pairs_override=override, skip_train=args.no_train)