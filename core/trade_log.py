import csv
import os
import time
from datetime import datetime, timezone
from typing import Dict

LOG_PATH = os.getenv("TRADE_LOG_PATH", "logs/trades.csv")
PAUSE_PATH = os.getenv("PAUSE_FILE_PATH", "logs/pause.csv")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def log_trade(row: Dict[str, str]) -> None:
    exists = os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if not exists:
            w.writeheader()
        w.writerow(row)


def pause_pair(symbol: str, seconds: int) -> None:
    exists = os.path.exists(PAUSE_PATH)
    with open(PAUSE_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["symbol", "until_ts"])
        w.writerow([symbol, int(time.time()) + seconds])


def should_pause_pair(symbol: str) -> bool:
    if not os.path.exists(PAUSE_PATH):
        return False
    now = int(time.time())
    with open(PAUSE_PATH, "r", encoding="utf-8") as f:
        next(f, None)  # skip header
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != 2:
                continue
            s, until_ts = parts[0], int(parts[1])
            if s == symbol and now < until_ts:
                return True
    return False
