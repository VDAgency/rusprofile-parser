"""Синхронизирует справочник ОКВЭД для Mini App.

Mini App — статичный фронт в ``src/webapp/``, он читает справочник
по относительному пути (``okved.json`` рядом с ``app.js``). Чтобы не
держать симлинк (несовместимо с Windows/git), мы копируем актуальные
JSON-файлы из ``data/okved/`` в ``src/webapp/`` минифицированными.

Запуск:
    python scripts/sync_webapp_okved.py

Перезапускайте при любых правках ``data/okved/okved_enriched.json``
или ``data/okved/okved_targets.json``. CI/деплой может вызывать этот
скрипт автоматически перед публикацией Mini App.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "okved"
WEBAPP = ROOT / "src" / "webapp"

SOURCES = {
    "okved_enriched.json": "okved.json",
    "okved_targets.json": "okved_targets.json",
}


def main() -> int:
    if not DATA.exists():
        print(f"Не найден каталог {DATA}", file=sys.stderr)
        return 1
    WEBAPP.mkdir(parents=True, exist_ok=True)

    total = 0
    for src_name, dst_name in SOURCES.items():
        src = DATA / src_name
        dst = WEBAPP / dst_name
        if not src.exists():
            print(f"  пропуск: {src} не существует", file=sys.stderr)
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        # Минифицируем, чтобы файл, едущий в Telegram-клиент, был как
        # можно меньше (200–400 КБ вместо 600+).
        dst.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        size_kb = dst.stat().st_size / 1024
        print(f"  {src.name} -> {dst.relative_to(ROOT)} ({size_kb:.1f} KB)")
        total += 1

    print(f"Готово: {total} файлов синхронизировано")
    return 0


if __name__ == "__main__":
    sys.exit(main())
