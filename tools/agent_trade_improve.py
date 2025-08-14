# tools/agent_trade_improve.py
# Добавляет:
#  - core/trade_log.py (append_trade_event)
#  - логирование order_placed / order_filled в position_manager.open_position
#  - безопасный пост-чек без падений

import re, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path.relative_to(ROOT)}")

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

def patch_position_manager_logging():
    p = ROOT / "position_manager.py"
    src = p.read_text(encoding="utf-8")

    # 1) ensure import
    if "from core.trade_log import append_trade_event" not in src:
        src = src.replace(
            "from core.market_info import adjust_qty_price",
            "from core.market_info import adjust_qty_price\nfrom core.trade_log import append_trade_event"
        )

    # 2) после create_order(...) вставить лог order_placed
    #   найдём место создания ордера
    pattern_create = r"(order\s*=\s*ex\.create_order\([^\n]+?\)\s*)"
    if re.search(pattern_create, src):
        src = re.sub(
            pattern_create,
            r"""\\1

# --- trade log: placed ---
try:
    import time
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
        "mode": "LIVE" if os.getenv("MODE","DRY").upper()=="LIVE" else "DRY",
    })
except Exception as _log_e:
    print(f"[WARN] trade-log place: {_log_e}")
# --- /trade log: placed ---
""",
            src,
            flags=re.M,
        )

    # 3) мягкий пост-чек fill + лог order_filled (без падений)
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
            import time
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
                "mode": "LIVE" if os.getenv("MODE","DRY").upper()=="LIVE" else "DRY",
            })
    except Exception as _log_e:
        print(f"[WARN] trade-log filled: {_log_e}")
    # --- /trade log: filled check ---
    """)

    # Добавим пост-чек в конец функции open_position (перед return)
    if "def open_position" in src and "return {" in src:
        src = re.sub(r"(return\s+\{[^\n]+\n\s*\})", guard_block + r"\n\1", src, flags=re.M)

    write(p, src)

def main():
    upsert_trade_log()
    patch_position_manager_logging()

if __name__ == "__main__":
    main()
