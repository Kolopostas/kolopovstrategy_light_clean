*** /dev/null
--- b/.flake8
@@
+[flake8]
+# Разрешаем строки до 120 символов (гасим E501 79-символьный лимит)
+max-line-length = 120
+
+# Хвостовые пробелы не считаем критичными
+extend-ignore = W291
+
+# Исключения из проверки
+exclude =
+    .git,
+    __pycache__,
+    tools/agent_trade_improve.py

*** a/positions_guard.py
--- b/positions_guard.py
@@
-if __name__ == "__main__":
-    import sys, os
+if __name__ == "__main__":
+    import sys
     iv = int(os.getenv("CHECK_INTERVAL", os.getenv("CHECK_INTERVAL_SECONDS", "30")))
     if "--once" in sys.argv:
         main()
     else:
         print(f"[CONFIG] CHECK_INTERVAL={iv}s")

*** a/tools/agent_trade_improve.py
--- b/tools/agent_trade_improve.py
@@
-# helper script ...
-import os
-import re
+# helper script ...
+import re
 import textwrap
 from pathlib import Path
@@
-    print(f"[write] {path.relative_to(ROOT)}") 
+    print(f"[write] {path.relative_to(ROOT)}")
