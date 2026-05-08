"""Синхронизирует справочник ОКВЭД и подменяет cache-busting токен в Mini App.

Mini App — статичный фронт в ``src/webapp/``, он читает справочник
по относительному пути (``okved.json`` рядом с ``app.js``). Чтобы не
держать симлинк (несовместимо с Windows/git), мы копируем актуальные
JSON-файлы из ``data/okved/`` в ``src/webapp/`` минифицированными.

Дополнительно: в ``index.html`` есть плейсхолдер
``__ASSET_VERSION__`` в URL'ах ``app.js`` и ``style.css``. Этот скрипт
заменяет его на текущий timestamp — Telegram WebView не кеширует
файл, если query-параметр ``?v=`` сменился.

Запуск:
    python scripts/sync_webapp_okved.py

Перезапускайте при любых правках ``data/okved/*.json`` ИЛИ
``src/webapp/index.html|app.js|style.css``. CI/деплой может вызывать
этот скрипт автоматически перед публикацией.
"""

import json
import re
import sys
from datetime import datetime
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

    # Cache-busting: подменяем __ASSET_VERSION__ или старый ?v=... на
    # свежий timestamp. Поддерживаем оба варианта, чтобы можно было
    # запустить скрипт несколько раз подряд (idempotent).
    index_path = WEBAPP / "index.html"
    if index_path.exists():
        new_version = datetime.now().strftime("%Y%m%d_%H%M%S")
        text = index_path.read_text(encoding="utf-8")
        text2 = re.sub(
            r'(\.(?:js|css))\?v=[^"\']*',
            lambda m: f"{m.group(1)}?v={new_version}",
            text,
        )
        text2 = text2.replace("__ASSET_VERSION__", new_version)
        if text2 != text:
            index_path.write_text(text2, encoding="utf-8")
            print(f"  index.html: cache-version = {new_version}")
        else:
            print("  index.html: токены не найдены (пропущено)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
