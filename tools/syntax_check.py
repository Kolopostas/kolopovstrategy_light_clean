import py_compile
import subprocess
import sys

files = subprocess.check_output(
    "git ls-files '*.py'", shell=True, text=True
).splitlines()

if not files:
    print("No Python files found.")
    sys.exit(0)

for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"[OK] {f}")
    except Exception as e:
        print(f"[FAIL] {f}: {e}")
        sys.exit(1)

print("Syntax OK")
