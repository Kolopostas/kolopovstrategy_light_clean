import os, csv
from pathlib import Path
from core.log_uploader import push_file_if_needed

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
