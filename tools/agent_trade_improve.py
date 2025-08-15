# tools/agent_trade_improve.py
# Creates/updates project files for: online trade log upload, fixed position_manager,
# CI hard syntax check, and .gitignore for logs.

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_file(relpath: str, content: str) -> None:
    p = ROOT / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"[write] {relpath}")


def append_if_missing(relpath: str, lines_to_add: str) -> None:
    p = ROOT / relpath
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    if lines_to_add not in existing:
        p.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + lines_to_add, encoding="utf-8")
        print(f"[append] {relpath}")
    else:
        print(f"[skip-append] {relpath} already has block")


def main():
    # 1) .gitignore: allow logs/trades.csv (keep only those lines)
    gitignore = """\
__pycache__/
*.pyc
*.pyo
*.pyd
*.swp
*.DS_Store
.venv/
.env
.env.local
.env.*.local

logs/*
!logs/trades.csv
!logs/.gitkeep
"""
    write_file(".gitignore", gitignore)

    # 2) logs/.gitkeep
    write_file("logs/.gitkeep", "(keep)\n")

    # 3) .env.example block for online logs
    env_example_path = ".env.example"
    env_block = """\
# --- Online trade log upload (Railway) ---
ONLINE_LOGS=1
GITHUB_TOKEN=
GITHUB_REPO=UserName/RepoName
GITHUB_BRANCH=main
"""
    append_if_missing(env_example_path, env_block)

    # 4) requirements.txt ensure requests
    req_path = "requirements.txt"
    if (ROOT / req_path).exists():
        append_if_missing(req_path, "requests>=2.31.0")
    else:
        write_file(req_path, "ccxt\nxgboost\npandas\nnumpy\njoblib\nrequests>=2.31.0\n")

    # 5) core/github_uploader.py (ASCII safe)
    github_uploader = """\
import base64
import os
from datetime import datetime
from typing import Optional

import requests


def _should_upload() -> bool:
    \"\"\"Upload only when ONLINE_LOGS=1 or we are on Railway.\"\"\" 
    if os.getenv("ONLINE_LOGS", "") == "1":
        return True
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_STATIC_URL"):
        return True
    return False


def upload_trades_to_github(file_path: str = "logs/trades.csv") -> None:
    \"\"\"Upload file to GitHub via Contents API (no git push needed).\"\"\"
    if not _should_upload():
        return

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")  # username/repository
    branch = os.getenv("GITHUB_BRANCH", "main")
    if not token or not repo:
        return
    if not os.path.exists(file_path):
        print(f"WARN: {file_path} not found, skipping upload")
        return

    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {"Authorization": f"token {token}"}

    with open(file_path, "rb") as f:
        content = f.read()
    encoded = base64.b64encode(content).decode("utf-8")

    r = requests.get(url, headers=headers, timeout=20)
    sha: Optional[str] = r.json().get("sha") if r.status_code == 200 else None

    data = {
        "message": f"update(trades.csv) {datetime.utcnow().isoformat()}",
        "content": encoded,
        "branch": branch,
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data, timeout=30)
    if r.status_code in (200, 201):
        print("OK: trades.csv uploaded to GitHub")
    else:
        print(f"ERR: upload error {r.status_code}: {r.text}")
"""
    write_file("core/github_uploader.py", github_uploader)

    # 6) core/trade_log.py
    trade_log = """\
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

    try:
        upload_trades_to_github(str(LOG_PATH).replace("\\\\", "/"))
    except Exception as e:
        print(f"[WARN] upload trades.csv: {e}")
"""
    write_file("core/trade_log.py", trade_log)

    # 7) position_manager.py (drop-in safe replacement)
    position_manager = """\
import os
import time
from typing import Any, Dict, Optional

from core.bybit_exchange import create_exchange, normalize_symbol
from core.market_info import adjust_qty_price
from core.trade_log import append_trade_event
from core.github_uploader import upload_trades_to_github


def _calc_order_qty(usdt: float, price: float, risk_fraction: float, leverage: int) -> float:
    \"\"\"Conservative position sizing in USDT -> base asset qty.\"\"\"
    size_usdt = max(0.0, usdt) * max(0.0, risk_fraction) * max(1, leverage)
    return max(0.0, size_usdt / max(price, 1e-9))


def _wait_fill(ex, sym: str, order_id: str, timeout_sec: int = 15):
    \"\"\"Best-effort wait for fill; return original object if not resolved.\"\"\"
    import time as _t
    t0 = _t.time()
    try:
        while _t.time() - t0 < timeout_sec:
            o = ex.fetch_order(order_id, sym)
            if (o or {}).get("status") in ("closed", "filled", "canceled"):
                return o
            _t.sleep(1.0)
    except Exception:
        pass
    return {"id": order_id, "status": "unknown"}


def open_position(symbol: str, side: str, price: Optional[float] = None) -> Dict[str, Any]:
    \"\"\"Market order with TP/SL. Ignores 110043, handles 10001. Writes trade log.
    DRY_RUN=1 -> no orders sent.\"\"\"
    if os.getenv("DRY_RUN", "").strip() == "1":
        return {"status": "dry", "reason": "DRY_RUN=1", "symbol": symbol, "side": side}

    ex = create_exchange()
    sym = normalize_symbol(symbol)

    bal = ex.fetch_balance()
    usdt = float(bal.get("USDT", {}).get("free", 0.0) or 0.0)

    if price is None:
        t = ex.fetch_ticker(sym)
        price = float(t.get("last") or t.get("close") or 0.0)

    risk_fraction = float(os.getenv("RISK_FRACTION", "0.2"))
    leverage = int(os.getenv("LEVERAGE", "3"))
    tp_pct = float(os.getenv("TP_PCT", "0.01"))
    sl_pct = float(os.getenv("SL_PCT", "0.005"))

    qty_raw = _calc_order_qty(usdt, price, risk_fraction, leverage)
    qty, px, _market = adjust_qty_price(sym, qty_raw, price)
    if qty <= 0:
        return {"status": "error", "reason": "qty<=0 after adjust", "balance": usdt, "qty_raw": qty_raw}

    order_side = "buy" if side.lower() == "long" else "sell"

    try:
        ex.set_leverage(leverage, sym)
    except Exception as e:
        if "110043" not in str(e):
            print("WARN set_leverage:", e)

    if order_side == "buy":
        tp_price = float(ex.price_to_precision(sym, px * (1 + tp_pct)))
        sl_price = float(ex.price_to_precision(sym, px * (1 - sl_pct)))
    else:
        tp_price = float(ex.price_to_precision(sym, px * (1 - tp_pct)))
        sl_price = float(ex.price_to_precision(sym, px * (1 + sl_pct)))

    params = {"takeProfit": tp_price, "stopLoss": sl_price}
    print("DEBUG ORDER:", {"symbol": sym, "side": order_side, "qty_raw": qty_raw, "qty": qty,
                           "entry_price": px, "TP": tp_price, "SL": sl_price, "lev": leverage})

    try:
        o = ex.create_order(sym, type="market", side=order_side, amount=qty, price=None, params=params)

        try:
            append_trade_event({
                "ts": time.time(), "event": "order_placed", "symbol": sym, "side": order_side,
                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
                "order_id": o.get("id") or o.get("orderId"),
                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
                "mode": "LIVE",
            })
        except Exception as _e:
            print("WARN trade-log placed:", _e)

        oid = o.get("id") or o.get("orderId")
        if oid:
            o = _wait_fill(ex, sym, oid)

        try:
            append_trade_event({
                "ts": time.time(), "event": "order_filled", "symbol": sym, "side": order_side,
                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
                "order_id": o.get("id") or oid,
                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
                "mode": "LIVE",
            })
        except Exception as _e:
            print("WARN trade-log filled:", _e)

        upload_trades_to_github("logs/trades.csv")

        return {"status": (o.get("status") or "unknown"), "order": o, "qty": qty, "price": px,
                "tp": tp_price, "sl": sl_price, "balance": usdt}

    except Exception as e:
        msg = str(e)
        try:
            append_trade_event({
                "ts": time.time(), "event": "order_error", "symbol": sym, "side": order_side,
                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
                "order_id": None, "link_id": None, "mode": "LIVE", "extra": msg,
            })
        except Exception as _e:
            print("WARN trade-log error:", _e)

        upload_trades_to_github("logs/trades.csv")

        if "10001" in msg:
            return {"status": "retryable", "reason": "10001 invalid request", "error": msg}
        if "110043" in msg:
            return {"status": "ok_with_warning", "warning": "110043 leverage not modified", "qty": qty}
        return {"status": "error", "error": msg, "qty": qty, "price": px}
"""
    write_file("position_manager.py", position_manager)

    # 8) .github/workflows/agent-ci.yml (hard syntax/import checks, ASCII hyphens)
    ci_yaml = """\
name: agent-ci
on:
  push:
    branches: [ main ]
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt || true
          pip install isort black flake8 bandit || true

      - name: Syntax check (HARD)
        run: |
          python - <<'PY'
import compileall, sys
ok = compileall.compile_dir('.', quiet=1, force=True)
sys.exit(0 if ok else 1)
PY

      - name: Import smoke test (HARD)
        run: |
          python - <<'PY'
import importlib, sys
mods = [
  "positions_guard",
  "position_manager",
  "core.predict",
  "core.market_info",
]
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        print(f"IMPORT FAIL: {m}: {e}")
        sys.exit(1)
print("imports OK")
PY

      - name: Static checks (non-blocking)
        run: |
          flake8 . || true
          bandit -q -r . || true
          isort --check-only . || true
          black --check . || true

      - name: Create PR with changes
        uses: peter-evans/create-pull-request@v6
        with:
          branch: agent/fixes
          delete-branch: true
          title: "Agent: auto-fixes (logging/upload/ci)"
          commit-message: "agent: auto-fixes"
          body: |
            Autogenerated PR by CI:
            - Fix position_manager (IndentationError)
            - Online logging trades.csv via GitHub API
            - Formatting and checks
"""
    write_file(".github/workflows/agent-ci.yml", ci_yaml)

    print("\nDone. Now commit and push, or run your PR agent to open a PR.")


if __name__ == "__main__":
    main()
