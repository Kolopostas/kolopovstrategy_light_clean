# tools/agent_trade_improve.py
# PR-сборщик: trade log + цикл по CHECK_INTERVAL для positions_guard.py

import re, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path.relative_to(ROOT)}")

# 1) Добавим core/trade_log.py
def upsert_trade_log():
    p = ROOT / "core" / "trade_log.py"
    content = textwrap.dedent("""\
    import os, csv
    from pathlib import Path

    LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv"))

    def append_trade_event(row: dict):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not LOG_PATH.exists()
        with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ts","event","symbol","side","qty","price","sl","tp",
                "order_id","link_id","mode","extra"
            ])
            if write_header:
                w.writeheader()
            row.setdefault("tp", "")
            row.setdefault("extra", "")
            w.writerow(row)
            f.flush()
    """)
    write(p, content)

# 2) Пропатчим position_manager.py — логируем размещение/исполнение
def patch_position_manager_logging():
    p = ROOT / "position_manager.py"
    if not p.exists():
        print("[skip] position_manager.py not found")
        return
    src = p.read_text(encoding="utf-8")

    # импорт логгера
    if "from core.trade_log import append_trade_event" not in src:
        src = src.replace(
            "from core.market_info import adjust_qty_price",
            "from core.market_info import adjust_qty_price\nfrom core.trade_log import append_trade_event"
        )

    # после create_order(...): лог order_placed
    pattern_create = r"(order\s*=\s*ex\.create_order\([^\n]+?\)\s*)"
    if re.search(pattern_create, src):
        src = re.sub(
            pattern_create,
            r"""\\1

# --- trade log: placed ---
try:
    import time, os
    append_trade_event({
        "ts": time.time(),
        "event": "order_placed",
        "symbol": symbol,
        "side": order_side,
        "qty": qty_adj,
        "price": px_adj,
        "sl": sl_price,
        "tp": locals().get("tp_price", ""),
        "link_id": (order.get("clientOrderId") or order.get("orderLinkId") or order.get("info", {}).get("orderLinkId")),
        "order_id": order.get("id"),
        "mode": "LIVE" if os.getenv("MODE","DRY").upper()=="LIVE" and not os.getenv("DRY_RUN") else "DRY",
    })
except Exception as _log_e:
    print(f"[WARN] trade-log place: {_log_e}")
# --- /trade log: placed ---
""",
            src,
            flags=re.M,
        )

    # мягкая проверка fill + лог order_filled (без падений)
    if "return {" in src:
        guard_block = textwrap.dedent("""\
        # --- trade log: filled check ---
        try:
            filled = False
            link_id = (order.get("clientOrderId") or order.get("orderLinkId") or order.get("info", {}).get("orderLinkId"))
            try:
                oo = ex.fetch_open_orders(symbol, params={"orderLinkId": link_id} if link_id else {})
                filled = (len(oo) == 0)
            except Exception:
                pass
            if filled:
                import time, os
                append_trade_event({
                    "ts": time.time(),
                    "event": "order_filled",
                    "symbol": symbol,
                    "side": order_side,
                    "qty": qty_adj,
                    "price": px_adj,
                    "sl": sl_price,
                    "tp": locals().get("tp_price", ""),
                    "order_id": order.get("id"),
                    "link_id": link_id,
                    "mode": "LIVE" if os.getenv("MODE","DRY").upper()=="LIVE" and not os.getenv("DRY_RUN") else "DRY",
                })
        except Exception as _log_e:
            print(f"[WARN] trade-log filled: {_log_e}")
        # --- /trade log: filled check ---
        """)
        src = re.sub(r"(return\s+\{[^\n]+\n\s*\})", guard_block + r"\n\1", src, flags=re.M)

    write(p, src)

# 3) Добавим цикл по CHECK_INTERVAL без переписывания guard’а:
#    заменим хвост файла:
#    if __name__ == "__main__": main()
#    -> обёртка, которая вызывает main() в цикле (если не --once)
def patch_positions_guard_loop():
    p = ROOT / "positions_guard.py"
    if not p.exists():
        print("[skip] positions_guard.py not found")
        return
    src = p.read_text(encoding="utf-8")

    # импорт time на всякий
    if "import time" not in src:
        src = src.replace("from datetime import datetime, timezone", "from datetime import datetime, timezone\nimport time")

    # если есть уже наш цикл — не патчим повторно
    if "AGENT_LOOP" in src:
        write(p, src)
        return

    # добавим поддержку --once (если нет парсинга — оставим как есть)
    if "--once" not in src:
        src = src.replace(
            "parser = argparse.ArgumentParser()",
            "parser = argparse.ArgumentParser()\n    parser.add_argument('--once', action='store_true', help='Один проход и выход')"
        )

    # заменим нижний блок запуска
    src = re.sub(
        r"\nif __name__ == \"__main__\":\s*\n\s*main\(\)\s*\n\Z",
        textwrap.dedent("""\

        # AGENT_LOOP: цикличный запуск по CHECK_INTERVAL (ENV), либо единичный при --once
        if __name__ == "__main__":
            import sys, os
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
                    print(f"[TICK] took={time.time()-t0:.1f}s | sleep={sleep_for:.1f}s | interval={iv}s")
                    time.sleep(sleep_for)
        """),
        src,
        flags=re.M
    )

    write(p, src)

def main():
    upsert_trade_log()
    patch_position_manager_logging()
    patch_positions_guard_loop()

if __name__ == "__main__":
    main()

