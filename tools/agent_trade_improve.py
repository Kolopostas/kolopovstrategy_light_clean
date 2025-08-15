*** a/.gitignore
--- b/.gitignore
@@
-logs/
+# Игнорим всё в logs/, кроме нужных файлов
+/logs/*
+!/logs/.gitkeep
+!/logs/trades.csv
+!/logs/cooldown.json

*** /dev/null
--- b/logs/.gitkeep
@@
+(keep)

*** /dev/null
--- b/core/trade_log.py
@@
+import os, csv
+from pathlib import Path
+
+LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv"))
+
+def append_trade_event(row: dict):
+    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
+    write_header = not LOG_PATH.exists()
+    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
+        w = csv.DictWriter(f, fieldnames=[
+            "ts","event","symbol","side","qty","price","sl","tp",
+            "order_id","link_id","mode","extra"
+        ])
+        if write_header:
+            w.writeheader()
+        row.setdefault("extra", "")
+        row.setdefault("tp", "")
+        w.writerow(row)
+        f.flush()

*** a/position_manager.py
--- b/position_manager.py
@@
-from typing import Any, Dict, Optional
+from typing import Any, Dict, Optional
+import time
+from core.trade_log import append_trade_event
@@
 def open_position(
     symbol: str, side: str, price: Optional[float] = None
 ) -> Dict[str, Any]:
@@
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
+    try:
+        o = ex.create_order(sym, type="market", side=order_side, amount=qty, price=None, params=params)
+
+        # LOG: размещён ордер
+        try:
+            append_trade_event({
+                "ts": time.time(),
+                "event": "order_placed",
+                "symbol": sym,
+                "side": order_side,
+                "qty": qty,
+                "price": px,
+                "tp": tp_price,
+                "sl": sl_price,
+                "order_id": o.get("id") or o.get("orderId"),
+                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
+                "mode": "LIVE",
+            })
+        except Exception as _e:
+            print("[WARN] trade-log placed:", _e)
+
+        oid = o.get("id") or o.get("orderId")
+        if oid:
+            o = _wait_fill(ex, sym, oid)
+
+        # LOG: исполнен (мягко)
+        try:
+            append_trade_event({
+                "ts": time.time(),
+                "event": "order_filled",
+                "symbol": sym,
+                "side": order_side,
+                "qty": qty,
+                "price": px,
+                "tp": tp_price,
+                "sl": sl_price,
+                "order_id": o.get("id") or oid,
+                "link_id": o.get("clientOrderId") or o.get("orderLinkId") or (o.get("info", {}) or {}).get("orderLinkId"),
+                "mode": "LIVE",
+            })
+        except Exception as _e:
+            print("[WARN] trade-log filled:", _e)
+
+        return {
+            "status": (o.get("status") or "unknown"),
+            "order": o,
+            "qty": qty,
+            "price": px,
+            "tp": tp_price,
+            "sl": sl_price,
+            "balance": usdt,
+        }
@@
     except Exception as e:
         msg = str(e)
+        # LOG: ошибка
+        try:
+            append_trade_event({
+                "ts": time.time(),
+                "event": "order_error",
+                "symbol": sym,
+                "side": order_side,
+                "qty": qty,
+                "price": px,
+                "tp": tp_price,
+                "sl": sl_price,
+                "order_id": None,
+                "link_id": None,
+                "mode": "LIVE",
+                "extra": msg,
+            })
+        except Exception as _e:
+            print("[WARN] trade-log error:", _e)

*** a/positions_guard.py
--- b/positions_guard.py
@@
-from contextlib import contextmanager
+from contextlib import contextmanager
+import json
+import time
@@
 def main():
@@
     args = parser.parse_args()
@@
+    # --- COOLDOWN (sec) с бэкомпатом по COOLDOWN_MIN ---
+    cooldown_sec = int(os.getenv("COOLDOWN_SEC", "0")) \
+        if os.getenv("COOLDOWN_SEC") is not None \
+        else int(float(os.getenv("COOLDOWN_MIN", "0")) * 60)
+    cooldown_path = os.getenv("COOLDOWN_PATH", "logs/cooldown.json")
+    try:
+        os.makedirs(os.path.dirname(cooldown_path), exist_ok=True)
+        if os.path.exists(cooldown_path):
+            with open(cooldown_path, "r", encoding="utf-8") as f:
+                last_trade_at = json.load(f)
+        else:
+            last_trade_at = {}
+    except Exception:
+        last_trade_at = {}
@@
     with single_instance_lock():
@@
-        for p in pairs:
+        for p in pairs:
             sym = normalize_symbol(p)
             price = get_symbol_price(sym)
+
+            # COOLDOWN по инструменту
+            if cooldown_sec > 0:
+                now = int(time.time())
+                last_ts = int(last_trade_at.get(sym, 0))
+                if now - last_ts < cooldown_sec:
+                    left = cooldown_sec - (now - last_ts)
+                    print(f"🛑 COOLDOWN {sym}: ещё {left}s — пропуск входа.")
+                    continue
@@
-            res = open_position(sym, side=signal)
+            res = open_position(sym, side=signal)
             print("🧾 Результат:", res)
+
+            # если ордер поставился/исполнен — фиксируем время
+            if isinstance(res, dict) and res.get("status") not in ("error", "retryable"):
+                last_trade_at[sym] = int(time.time())
+                try:
+                    with open(cooldown_path, "w", encoding="utf-8") as f:
+                        json.dump(last_trade_at, f)
+                except Exception as _e:
+                    print("[WARN] cooldown save:", _e)



