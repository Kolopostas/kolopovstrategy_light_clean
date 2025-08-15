diff --git a/core/github_uploader.py b/core/github_uploader.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/core/github_uploader.py
@@
+import base64
+import os
+from datetime import datetime
+from typing import Optional
+
+import requests
+
+
+def _should_upload() -> bool:
+    """
+    Загружать только в онлайн-среде:
+    - Railway (есть переменные RAILWAY_*)
+    - или явно включено ONLINE_LOGS=1
+    """
+    if os.getenv("ONLINE_LOGS", "") == "1":
+        return True
+    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_STATIC_URL"):
+        return True
+    return False
+
+
+def upload_trades_to_github(file_path: str = "logs/trades.csv") -> None:
+    """Отправляет файл в GitHub через Contents API (без git push)."""
+    if not _should_upload():
+        return
+
+    token = os.getenv("GITHUB_TOKEN")
+    repo = os.getenv("GITHUB_REPO")  # формат: username/repository
+    branch = os.getenv("GITHUB_BRANCH", "main")
+
+    if not token or not repo:
+        # онлайн‑логирование выключено/не настроено — тихо выходим
+        return
+
+    if not os.path.exists(file_path):
+        print(f"⚠️ {file_path} не найден — пропуск upload")
+        return
+
+    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
+    headers = {"Authorization": f"token {token}"}
+
+    # читаем локальный файл
+    with open(file_path, "rb") as f:
+        content = f.read()
+    encoded = base64.b64encode(content).decode("utf-8")
+
+    # узнаём sha текущей версии (если уже существует)
+    r = requests.get(url, headers=headers, timeout=20)
+    sha: Optional[str] = r.json().get("sha") if r.status_code == 200 else None
+
+    data = {
+        "message": f"update(trades.csv) {datetime.utcnow().isoformat()}",
+        "content": encoded,
+        "branch": branch,
+    }
+    if sha:
+        data["sha"] = sha
+
+    r = requests.put(url, headers=headers, json=data, timeout=30)
+    if r.status_code in (200, 201):
+        print("✅ trades.csv загружен в GitHub")
+    else:
+        print(f"❌ upload error {r.status_code}: {r.text}")
diff --git a/position_manager.py b/position_manager.py
index 2222222..3333333 100644
--- a/position_manager.py
+++ b/position_manager.py
@@
-from typing import Any, Dict, Optional
+from typing import Any, Dict, Optional
+import time
+from core.trade_log import append_trade_event
+from core.github_uploader import upload_trades_to_github
@@
-def open_position(
-    symbol: str, side: str, price: Optional[float] = None
-) -> Dict[str, Any]:
-    """
-    Открывает MARKET ордер с TP/SL. Игнорирует 110043, ловит 10001.
-    Dry-run: если DRY_RUN=1 в окружении — ничего не отправляет.
-    """
-    if os.getenv("DRY_RUN", "").strip() == "1":
-        return {"status": "dry", "reason": "DRY_RUN=1", "symbol": symbol, "side": side}
-
-    ex = create_exchange()
-    sym = normalize_symbol(symbol)
-
-    bal = ex.fetch_balance()
-    usdt = float(bal.get("USDT", {}).get("free", 0.0) or 0.0)
-
-    if price is None:
-        t = ex.fetch_ticker(sym)
-        price = float(t.get("last") or t.get("close") or 0.0)
-
-    risk_fraction = float(os.getenv("RISK_FRACTION", "0.2"))
-    leverage = int(os.getenv("LEVERAGE", "3"))
-    tp_pct = float(os.getenv("TP_PCT", "0.01"))
-    sl_pct = float(os.getenv("SL_PCT", "0.005"))
-
-    qty_raw = _calc_order_qty(usdt, price, risk_fraction, leverage)
-    qty, px, market = adjust_qty_price(sym, qty_raw, price)
-    if qty <= 0:
-        return {
-            "status": "error",
-            "reason": "qty<=0 after adjust",
-            "balance": usdt,
-            "qty_raw": qty_raw,
-        }
-
-    order_side = "buy" if side.lower() == "long" else "sell"
-
-    try:
-        ex.set_leverage(leverage, sym)
-    except Exception as e:
-        if "110043" not in str(e):
-            print("⚠️ set_leverage:", e)
-
-    if order_side == "buy":
-        tp_price = float(ex.price_to_precision(sym, px * (1 + tp_pct)))
-        sl_price = float(ex.price_to_precision(sym, px * (1 - sl_pct)))
-    else:
-        tp_price = float(ex.price_to_precision(sym, px * (1 - tp_pct)))
-        sl_price = float(ex.price_to_precision(sym, px * (1 + sl_pct)))
-
-    params = {"takeProfit": tp_price, "stopLoss": sl_price}
-
-    print(
-        "🔎 DEBUG ORDER:",
-        {
-            "symbol": sym,
-            "side": order_side,
-            "qty_raw": qty_raw,
-            "qty": qty,
-            "entry_price": px,
-            "TP": tp_price,
-            "SL": sl_price,
-            "lev": leverage,
-        },
-    )
-    try:
-        o = ex.create_order(
-            sym, type="market", side=order_side, amount=qty, price=None, params=params
-        )
-        oid = o.get("id") or o.get("orderId")
-        if oid:
-            o = _wait_fill(ex, sym, oid)
-        return {
-            "status": (o.get("status") or "unknown"),
-            "order": o,
-            "qty": qty,
-            "price": px,
-            "tp": tp_price,
-            "sl": sl_price,
-            "balance": usdt,
-        }
-    except Exception as e:
-        msg = str(e)
-        if "10001" in msg:
-            return {
-                "status": "retryable",
-                "reason": "10001 invalid request",
-                "error": msg,
-            }
-        if "110043" in msg:
-            return {
-                "status": "ok_with_warning",
-                "warning": "110043 leverage not modified",
-                "qty": qty,
-            }
-        return {"status": "error", "error": msg, "qty": qty, "price": px}
+def open_position(symbol: str, side: str, price: Optional[float] = None) -> Dict[str, Any]:
+    """
+    Открывает MARKET ордер с TP/SL. Игнорирует 110043, ловит 10001.
+    Dry-run: если DRY_RUN=1 — не отправляет ордера.
+    Логирует: order_placed / order_filled / order_error.
+    """
+    # DRY-RUN
+    if os.getenv("DRY_RUN", "").strip() == "1":
+        return {"status": "dry", "reason": "DRY_RUN=1", "symbol": symbol, "side": side}
+
+    ex = create_exchange()
+    sym = normalize_symbol(symbol)
+
+    # Баланс
+    bal = ex.fetch_balance()
+    usdt = float(bal.get("USDT", {}).get("free", 0.0) or 0.0)
+
+    # Цена
+    if price is None:
+        t = ex.fetch_ticker(sym)
+        price = float(t.get("last") or t.get("close") or 0.0)
+
+    # Риск‑параметры
+    risk_fraction = float(os.getenv("RISK_FRACTION", "0.2"))
+    leverage = int(os.getenv("LEVERAGE", "3"))
+    tp_pct = float(os.getenv("TP_PCT", "0.01"))
+    sl_pct = float(os.getenv("SL_PCT", "0.005"))
+
+    # Количество и прецизия
+    qty_raw = _calc_order_qty(usdt, price, risk_fraction, leverage)
+    qty, px, market = adjust_qty_price(sym, qty_raw, price)
+    if qty <= 0:
+        return {"status": "error", "reason": "qty<=0 after adjust", "balance": usdt, "qty_raw": qty_raw}
+
+    order_side = "buy" if side.lower() == "long" else "sell"
+
+    # Leverage (110043 = ок, игнорим)
+    try:
+        ex.set_leverage(leverage, sym)
+    except Exception as e:
+        if "110043" not in str(e):
+            print("⚠️ set_leverage:", e)
+
+    # TP / SL
+    if order_side == "buy":
+        tp_price = float(ex.price_to_precision(sym, px * (1 + tp_pct)))
+        sl_price = float(ex.price_to_precision(sym, px * (1 - sl_pct)))
+    else:
+        tp_price = float(ex.price_to_precision(sym, px * (1 - tp_pct)))
+        sl_price = float(ex.price_to_precision(sym, px * (1 + sl_pct)))
+
+    params = {"takeProfit": tp_price, "stopLoss": sl_price}
+
+    print("🔎 DEBUG ORDER:", {"symbol": sym, "side": order_side, "qty_raw": qty_raw, "qty": qty, "entry_price": px,
+                              "TP": tp_price, "SL": sl_price, "lev": leverage})
+
+    # Размещение
+    try:
+        o = ex.create_order(sym, type="market", side=order_side, amount=qty, price=None, params=params)
+
+        # LOG: placed
+        try:
+            append_trade_event({
+                "ts": time.time(), "event": "order_placed", "symbol": sym, "side": order_side,
+                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
+                "order_id": o.get("id") or o.get("orderId"),
+                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
+                "mode": "LIVE",
+            })
+        except Exception as _e:
+            print("[WARN] trade-log placed:", _e)
+
+        # Ждём fill
+        oid = o.get("id") or o.get("orderId")
+        if oid:
+            o = _wait_fill(ex, sym, oid)
+
+        # LOG: filled
+        try:
+            append_trade_event({
+                "ts": time.time(), "event": "order_filled", "symbol": sym, "side": order_side,
+                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
+                "order_id": o.get("id") or oid,
+                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
+                "mode": "LIVE",
+            })
+        except Exception as _e:
+            print("[WARN] trade-log filled:", _e)
+
+        # Онлайн‑загрузка лога (Railway/ONLINE_LOGS=1)
+        upload_trades_to_github("logs/trades.csv")
+
+        return {"status": (o.get("status") or "unknown"), "order": o, "qty": qty, "price": px,
+                "tp": tp_price, "sl": sl_price, "balance": usdt}
+
+    except Exception as e:
+        msg = str(e)
+        # LOG: error
+        try:
+            append_trade_event({
+                "ts": time.time(), "event": "order_error", "symbol": sym, "side": order_side,
+                "qty": qty, "price": px, "tp": tp_price, "sl": sl_price,
+                "order_id": None, "link_id": None, "mode": "LIVE", "extra": msg,
+            })
+        except Exception as _e:
+            print("[WARN] trade-log error:", _e)
+
+        # Онлайн‑загрузка лога (Railway/ONLINE_LOGS=1)
+        upload_trades_to_github("logs/trades.csv")
+
+        if "10001" in msg:
+            return {"status": "retryable", "reason": "10001 invalid request", "error": msg}
+        if "110043" in msg:
+            return {"status": "ok_with_warning", "warning": "110043 leverage not modified", "qty": qty}
+        return {"status": "error", "error": msg, "qty": qty, "price": px}
diff --git a/.env.example b/.env.example
index 4444444..5555555 100644
--- a/.env.example
+++ b/.env.example
@@
+# --- Online trade log upload (Railway) ---
+ONLINE_LOGS=1                 # включить онлайн‑выгрузку (или положись на авто‑детект Railway)
+GITHUB_TOKEN=
+GITHUB_REPO=UserName/RepoName
+GITHUB_BRANCH=main
