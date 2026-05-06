"""Загрузка JSON-справочника ОКВЭД и пресетов с кешированием в памяти.

Файлы лежат в ``data/okved/``. Они загружаются один раз при первом
обращении и далее переиспользуются. Если справочник вдруг
понадобится обновить «на лету» — есть ``reset_cache()`` (для
тестов; в проде проще рестартануть процесс).
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from src.config import BASE_DIR

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data" / "okved"
HANDBOOK_PATH = DATA_DIR / "okved_enriched.json"
TARGETS_PATH = DATA_DIR / "okved_targets.json"


@lru_cache(maxsize=1)
def load_handbook() -> list[dict]:
    """Возвращает массив записей ОКВЭД из ``okved_enriched.json``."""
    if not HANDBOOK_PATH.exists():
        raise FileNotFoundError(
            f"Не найден справочник ОКВЭД: {HANDBOOK_PATH}. "
            "См. data/okved/README.md."
        )
    raw = json.loads(HANDBOOK_PATH.read_text(encoding="utf-8"))
    items = raw.get("items") or []
    logger.info("Загружен справочник ОКВЭД: %d записей", len(items))
    return items


@lru_cache(maxsize=1)
def load_presets() -> list[dict]:
    """Возвращает массив пресетов из ``okved_targets.json``."""
    if not TARGETS_PATH.exists():
        raise FileNotFoundError(
            f"Не найден файл пресетов: {TARGETS_PATH}."
        )
    raw = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    presets = raw.get("presets") or []
    logger.info("Загружено пресетов ОКВЭД: %d", len(presets))
    return presets


def reset_cache() -> None:
    """Сбрасывает кеш — нужно только в тестах."""
    load_handbook.cache_clear()
    load_presets.cache_clear()
