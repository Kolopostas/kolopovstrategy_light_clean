# tools/agent_trade_improve.py
# Агент: автопатчи (predict/logging/github_upload) + автоформатирование

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path.relative_to(ROOT)}")


def patch_predict_remove_duplicate_block():
    p = ROOT / "core" / "predict.py"
    if not p.exists():
        print("[skip] core/predict.py not found")
        return
    src = p.read_text(encoding="utf-8")
    # удаляем дублирующий блок, который переопределял macd на 3 значения
    new_src, n = re.subn(
        r"\n#\s*---\s*indicators\s*&\s*filters.*\Z", "\n", src, flags=re.S | re.I
    )
    if n:
        write(p, new_src)
        print("[patch] removed duplicate indicators block from core/predict.py")
    else:
        print("[ok] no duplicate indicators block detected in core/predict.py")


def ensure_github_uploader():
    p = ROOT / "core" / "github_uploader.py"
    content = """\
import base64
import os
from datetime import datetime

import requests


def upload_trades_to_github(file_path: str = "logs/trades.csv") -> None:
    \"\"\"Заливает trades.csv в GitHub через Contents API (без git push).\"\"\"
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    branch = os.getenv("GITHUB_BRANCH", "main")

    if not token or not repo:
        print("❌ GITHUB_TOKEN или GITHUB_REPO не заданы — пропуск загрузки")
        return

    if not os.path.exists(file_path):
        print(f"⚠️ Файл {file_path} не найден — пропуск")
        return

    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {"Authorization": f"token {token}"}

    # читаем локальный файл
    with open(file_path, "rb") as f:
        content = f.read()
    encoded = base64.b64encode(content).decode("utf-8")

    # получаем sha, если файл уже есть
    r = requests.get(url, headers=headers, timeout=20)
    sha = r.json().get("sha") if r.status_code == 200 else None

    data = {
        "message": f"update({file_path}) {datetime.utcnow().isoformat()}",
        "content": encoded,
        "branch": branch,
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data, timeout=30)
    if r.status_code in (200, 201):
        print(f"✅ {file_path} загружен в GitHub")
    else:
        print(f"❌ upload error {r.status_code}: {r.text}")
"""
    write(p, content)


def patch_trade_log():
    p = ROOT / "core" / "trade_log.py"
    content = """\
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
        upload_trades_to_github(str(LOG_PATH).replace('\\\\', '/'))
    except Exception as e:
        print(f"[WARN] upload trades.csv: {e}")
"""
    write(p, content)


def patch_position_manager_logging():
    p = ROOT / "position_manager.py"
    if not p.exists():
        print("[skip] position_manager.py not found")
        return
    src = p.read_text(encoding="utf-8")

    # импорт логгера
    if "from core.trade_log import append_trade_event" not in src:
        src = src.replace(
            "from typing import Any, Dict, Optional",
            "from typing import Any, Dict, Optional\nimport time\nfrom core.trade_log import append_trade_event",
        )

    # после create_order(...) добавим лог 'order_placed' и затем 'order_filled'
    if "ex.create_order(" in src and "order_placed" not in src:
        src = re.sub(
            r"o\s*=\s*ex\.create_order\([^)]*\)\n",
            r"""o = ex.create_order(sym, type="market", side=order_side, amount=qty, price=None, params=params)
# --- trade log: placed ---
try:
    append_trade_event({
        "ts": time.time(),
        "event": "order_placed",
        "symbol": sym,
        "side": order_side,
        "qty": qty,
        "price": px,
        "tp": tp_price,
        "sl": sl_price,
        "order_id": o.get("id") or o.get("orderId"),
        "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
        "mode": "LIVE",
    })
except Exception as _e:
    print("[WARN] trade-log placed:", _e)
# --- /trade log: placed ---
""",
            src,
            flags=re.M,
        )

    # перед return — мягкая фиксация filled
    if "order_filled" not in src and "return {" in src:
        src = re.sub(
            r"(return\s+\{)",
            r"""
# --- trade log: filled (best-effort) ---
try:
    _oid = (o.get("id") or o.get("orderId"))
    append_trade_event({
        "ts": time.time(),
        "event": "order_filled",
        "symbol": sym,
        "side": order_side,
        "qty": qty,
        "price": px,
        "tp": tp_price,
        "sl": sl_price,
        "order_id": _oid,
        "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
        "mode": "LIVE",
    })
except Exception as _e:
    print("[WARN] trade-log filled:", _e)
# --- /trade log: filled ---
\g<1>""",
            src,
            flags=re.M,
        )

    write(p, src)


def add_requests_to_requirements():
    req = ROOT / "requirements.txt"
    if not req.exists():
        write(req, "requests>=2.31.0\n")
        return
    txt = req.read_text(encoding="utf-8")
    if "requests" not in txt:
        txt += "\nrequests>=2.31.0\n"
        write(req, txt)


def run_formatters():
    # форматируем весь репозиторий — убираем E401/E501 и ошибки isort/black
    subprocess.run(["python", "-m", "isort", "."], cwd=ROOT, check=False)
    subprocess.run(["python", "-m", "black", "."], cwd=ROOT, check=False)


def main():
    patch_predict_remove_duplicate_block()
    ensure_github_uploader()
    patch_trade_log()
    patch_position_manager_logging()
    add_requests_to_requirements()
    run_formatters()
    print("[done] agent trade improve finished")


if __name__ == "__main__":
    main()
