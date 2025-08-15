diff --git a/.gitignore b/.gitignore
index 7b1d2b3..a1c5d88 100644
--- a/.gitignore
+++ b/.gitignore
@@
 # Logs
-logs/
+# Исключаем только временные файлы логов, но сохраняем trades.csv
+logs/*.log
+!logs/trades.csv
diff --git a/core/position_manager.py b/core/position_manager.py
index 1234567..89abcde 100644
--- a/core/position_manager.py
+++ b/core/position_manager.py
@@
 import os
 import time
 import csv
+import requests
 
 # 🔹 Константа вместо COOLDOWN_MIN
-COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 5))
+COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", 300))
 
 TRADE_LOG_PATH = os.getenv("TRADE_LOG_PATH", "logs/trades.csv")
+GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
+GITHUB_REPO = os.getenv("GITHUB_REPO")  # Формат: "username/repo"
 
 last_trade_time = 0
 
 def open_position(symbol, side, qty, entry_price, tp_price, sl_price):
     global last_trade_time
-    if time.time() - last_trade_time < COOLDOWN_MIN * 60:
-        print(f"⏳ Ожидаем {COOLDOWN_MIN} минут перед следующей сделкой.")
+    if time.time() - last_trade_time < COOLDOWN_SEC:
+        print(f"⏳ Ожидаем {COOLDOWN_SEC} секунд перед следующей сделкой.")
         return
 
     last_trade_time = time.time()
@@
     # ✅ Запись сделки в лог
-    with open(TRADE_LOG_PATH, mode="a", newline="") as file:
-        writer = csv.writer(file)
-        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), symbol, side, qty, entry_price, tp_price, sl_price])
+    with open(TRADE_LOG_PATH, mode="a", newline="") as file:
+        writer = csv.writer(file)
+        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), symbol, side, qty, entry_price, tp_price, sl_price])
+
+    # 🔹 Автозаливка в GitHub
+    if GITHUB_TOKEN and GITHUB_REPO:
+        try:
+            upload_trade_log()
+        except Exception as e:
+            print(f"⚠️ Ошибка загрузки trade log в GitHub: {e}")
+
+
+def upload_trade_log():
+    """Загружает logs/trades.csv в GitHub через API"""
+    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{TRADE_LOG_PATH}"
+    with open(TRADE_LOG_PATH, "rb") as f:
+        content = f.read()
+    import base64
+    encoded = base64.b64encode(content).decode()
+    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
+    data = {
+        "message": "Auto-update trades.csv",
+        "content": encoded
+    }
+    r = requests.put(url, headers=headers, json=data)
+    if r.status_code not in (200, 201):
+        raise Exception(f"GitHub API error {r.status_code}: {r.text}")
+    else:
+        print("✅ trade log отправлен в GitHub")
