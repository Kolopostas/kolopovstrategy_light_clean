import csv
import os
from pathlib import Path

from core.github_uploader import upload_trades_to_github

LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv"))


def append_trade_event(row: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ts",
                "event",
                "symbol",
                "side",
                "qty",
                "price",
                "sl",
                "tp",
                "order_id",
                "link_id",
                "mode",
                "extra",
            ],
        )
        if write_header:
            w.writeheader()
        row.setdefault("extra", "")
        row.setdefault("tp", "")
        w.writerow(row)
        f.flush()

    # сразу пытаемся отправить обновление в GitHub (если заданы переменные)
    try:
        upload_trades_to_github(str(LOG_PATH).replace("\\", "/"))
    except Exception as e:
        print(f"[WARN] upload trades.csv: {e}")
