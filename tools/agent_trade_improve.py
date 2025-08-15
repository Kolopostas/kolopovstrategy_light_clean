*** a/.gitignore
--- b/.gitignore
@@
-logs/
+logs/*
+!logs/trades.csv
+!logs/.gitkeep

*** /dev/null
--- b/logs/.gitkeep
@@
+(keep)

*** a/.env.example
--- b/.env.example
@@
+# --- Online trade log upload (Railway) ---
+ONLINE_LOGS=1
+GITHUB_TOKEN=
+GITHUB_REPO=UserName/RepoName
+GITHUB_BRANCH=main

*** a/requirements.txt
--- b/requirements.txt
@@
+requests>=2.31.0

*** /dev/null
--- b/core/github_uploader.py
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
+    Загружаем только в онлайн-среде:
+    - Railway (есть переменные RAILWAY_*)
+    - или явно ONLINE_LOGS=1
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
+        # онлайн‑логирование не настроено — тихо выходим
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

*** a/core/trade_log.py
--- b/core/trade_log.py
@@
-import os, csv
-from pathlib import Path
-
-from core.log_uploader import push_file_if_needed
-
-LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv"))
-
-def append_trade_event(row: dict):
-    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
-    write_header = not LOG_PATH.exists()
-    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
-        w = csv.DictWriter(f, fieldnames=[
-            "ts","event","symbol","side","qty","price","sl","tp",
-            "order_id","link_id","mode","extra"
-        ])
-        if write_header:
-            w.writeheader()
-        row.setdefault("extra", "")
-        row.setdefault("tp", "")
-        w.writerow(row)
-        f.flush()
+import csv
+import os
+from pathlib import Path
+
+from core.github_uploader import upload_trades_to_github
+
+LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv"))
+
+
+def append_trade_event(row: dict) -> None:
+    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
+    write_header = not LOG_PATH.exists()
+    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
+        w = csv.DictWriter(
+            f,
+            fieldnames=[
+                "ts",
+                "event",
+                "symbol",
+                "side",
+                "qty",
+                "price",
+                "sl",
+                "tp",
+                "order_id",
+                "link_id",
+                "mode",
+                "extra",
+            ],
+        )
+        if write_header:
+            w.writeheader()
+        row.setdefault("extra", "")
+        row.setdefault("tp", "")
+        w.writerow(row)
+        f.flush()
+
+    # сразу пытаемся отправить обновление в GitHub (только онлайн)
+    try:
+        upload_trades_to_github(str(LOG_PATH).replace("\\\\", "/"))
+    except Exception as e:
+        print(f"[WARN] upload trades.csv: {e}")
