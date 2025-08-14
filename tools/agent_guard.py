import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: str, check: bool = False) -> int:
    print(f"$ {cmd}")
    rc = subprocess.call(cmd, shell=True)
    if check and rc != 0:
        sys.exit(rc)
    return rc


def ensure_file(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        print(f"Created {path.as_posix()}")
    else:
        print(f"Exists {path.as_posix()}")


def ensure_env_example():
    content = textwrap.dedent(
        """        BYBIT_API_KEY=
        BYBIT_SECRET_KEY=
        PROXY_URL=
        DOMAIN=bybit
        RISK_FRACTION=0.2
        RECV_WINDOW=15000
        PAIRS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,BNB/USDT:USDT
        """
    )
    ensure_file(ROOT / ".env.example", content)


def ensure_procfile():
    content = "worker: python positions_guard.py\n"
    ensure_file(ROOT / "Procfile", content)


def try_imports() -> bool:
    ok = True
    out = subprocess.check_output("git ls-files '*.py'", shell=True, text=True)
    for py in out.splitlines():
        mod = py[:-3].replace("/", ".").replace("\\", ".")
        if mod.endswith(".__init__"):
            continue
        try:
            __import__(mod)
            print(f"[import OK] {mod}")
        except Exception as e:
            ok = False
            print(f"[import FAIL] {py}: {e}")
    return ok


def dry_run_positions_guard():
    pg = ROOT / "positions_guard.py"
    if not pg.exists():
        print("positions_guard.py not found — skip dry-run.")
        return
    # попробуем с одной парой
    rc = run("python positions_guard.py --pair BTC/USDT:USDT --dry-run")
    if rc != 0:
        # fallback — без аргументов
        run("python positions_guard.py --dry-run")


def main():
    print("─" * 50)
    print("Agent Guard: start")

    ensure_env_example()
    ensure_procfile()

    _ = try_imports()
    dry_run_positions_guard()

    print("Agent Guard: done")
    print("─" * 50)
    sys.exit(0)


if __name__ == "__main__":
    main()
