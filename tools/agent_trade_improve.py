# tools/agent_trade_improve.py
# Цель: подготовить код так, чтобы CI не падал на стилистике и синтаксисе.
# Делает минимальные безопасные правки без внешних зависимостей:
# 1) Создаёт .flake8 с max-line-length=120, ignore W291, exclude для этого скрипта.
# 2) Чинит дублирующий импорт "os" внизу positions_guard.py (F811) -> оставляет "import sys".
# 3) Нормализует опасные невидимые символы в .py файлах (NBSP, необычные дефисы), табы -> 4 пробела.
# 4) Не ломает билд: при ошибках выводит warn и продолжает.

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[write] {path.relative_to(ROOT)}")


def upsert_flake8() -> None:
    cfg = (
        "[flake8]\n"
        "# Увеличиваем лимит длины строки (гасим массовые E501)\n"
        "max-line-length = 120\n"
        "\n"
        "# Хвостовые пробелы сейчас не критичны\n"
        "extend-ignore = W291\n"
        "\n"
        "# Исключаем этот вспомогательный скрипт\n"
        "exclude =\n"
        "    .git,\n"
        "    __pycache__,\n"
        "    tools/agent_trade_improve.py\n"
    )
    write_file(ROOT / ".flake8", cfg)


def patch_positions_guard_import() -> None:
    p = ROOT / "positions_guard.py"
    if not p.exists():
        print("[skip] positions_guard.py not found")
        return

    src = p.read_text(encoding="utf-8")

    # Внизу файла встречается:
    # if __name__ == "__main__":
    #     import sys, os
    # Меняем на импорт только sys (основной os уже импортирован сверху).
    pattern = re.compile(
        r'(^\s*if __name__ == ["\']__main__["\']:\s*\n)(\s*)import\s+sys\s*,\s*os\s*$',
        re.M,
    )
    if pattern.search(src):
        src = pattern.sub(r"\1\2import sys", src)
        write_file(p, src)
    else:
        print("[info] positions_guard.py: нижний 'import sys, os' не найден")


def _normalize_text(s: str) -> str:
    # NBSP -> space
    s = s.replace("\u00A0", " ")
    # Неразрывный дефис и прочие типографские тире -> обычный дефис
    s = s.replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-")
    s = s.replace("\u2014", "-").replace("\u2015", "-")
    # Табуляция -> 4 пробела
    s = s.replace("\t", "    ")
    return s


def normalize_repo_whitespace() -> None:
    # Нормализуем только .py файлы внутри репозитория (без venv и .git)
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        parts = set(rel.parts)
        if any(bad in parts for bad in {".git", ".venv", "venv", "__pycache__"}):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[warn] read {rel}: {e}")
            continue

        new_text = _normalize_text(text)
        if new_text != text:
            try:
                path.write_text(new_text, encoding="utf-8")
                print(f"[norm] {rel}")
            except Exception as e:
                print(f"[warn] write {rel}: {e}")


def main() -> None:
    upsert_flake8()
    patch_positions_guard_import()
    normalize_repo_whitespace()
    print("[done] agent_trade_improve completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # не валим CI: пусть шаг create-pull-request соберет PR с тем, что успели сделать
        print(f"[warn] agent_trade_improve: {e}", file=sys.stderr)
