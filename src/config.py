"""Конфигурация проекта — загрузка переменных окружения."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Корень проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем .env
load_dotenv(BASE_DIR / ".env")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBAPP_URL = os.getenv("TELEGRAM_WEBAPP_URL", "")

# Rusprofile
RUSPROFILE_LOGIN = os.getenv("RUSPROFILE_LOGIN", "")
RUSPROFILE_PASSWORD = os.getenv("RUSPROFILE_PASSWORD", "")
RUSPROFILE_BASE_URL = "https://www.rusprofile.ru"
RUSPROFILE_SEARCH_URL = f"{RUSPROFILE_BASE_URL}/search"

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv(
    "GOOGLE_SHEETS_CREDENTIALS_FILE", "config/credentials.json"
)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

# OpenAI (Этап 2)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# База данных. По умолчанию SQLite-файл в data/. На SaaS-этапе
# переедем на PostgreSQL — для этого подменим DATABASE_URL в .env.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(BASE_DIR / 'data' / 'parser.db').as_posix()}",
)

# HTTP API (Mini App история, Excel-экспорт). Поднимаем aiohttp в том
# же процессе, что и aiogram-бот; Nginx проксирует /api/ сюда.
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8080"))

# Whitelist user_id для unsafe-аутентификации Mini App.
# Используется как fallback, когда Telegram-клиент НЕ передаёт
# `tg.initData` (известная проблема Telegram Desktop под Windows).
# Формат: "12345678,87654321" — список через запятую.
# Безопасность: сервер примет `X-Telegram-User-Id-Unsafe` ТОЛЬКО для
# id из этого whitelist. На SaaS-этапе whitelist должен быть пустым.
_unsafe_raw = os.getenv("ALLOW_UNSAFE_USER_IDS", "").strip()
ALLOW_UNSAFE_USER_IDS: set[int] = set()
if _unsafe_raw:
    for chunk in _unsafe_raw.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ALLOW_UNSAFE_USER_IDS.add(int(chunk))

# Лимит «новых» компаний за один запуск парсинга — общий для всех
# источников. UI ограничивает выбором 1..MAX_NEW_HARD_LIMIT.
DEFAULT_MAX_NEW = 100
MAX_NEW_HARD_LIMIT = 300

# Парсинг
REQUEST_DELAY_MIN = 2  # мин. задержка между запросами (сек)
REQUEST_DELAY_MAX = 5  # макс. задержка
COMPANIES_PER_PAGE = 20  # компаний на странице Rusprofile
MAX_PAGES = 50  # макс. страниц для парсинга за один запуск

# Логирование
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "parser.log"

# Заголовки для структуры Google Sheets
SHEET_HEADERS = [
    "Название",
    "ИНН",
    "ОГРН",
    "Регион",
    "Адрес",
    "ОКВЭД",
    "Выручка",
    "Прибыль",
    "Телефон",
    "Email",
    "Сайт",
    "Статус компании",
    "Статус ИИ",
    "Комментарий ИИ",
    "Дата парсинга",
]

# --- Яндекс Карты -----------------------------------------------------------
YANDEX_MAPS_BASE_URL = "https://yandex.ru/maps/"
YANDEX_SHEET_NAME = "Яндекс Карты"
YANDEX_SHEET_HEADERS = [
    "Название",
    "Рубрики",
    "Регион",
    "Адрес",
    "Телефон",
    "Сайт",
    "Рейтинг",
    "Отзывов",
    "Режим работы",
    "Координаты",
    "Ссылка Яндекс",
    "Дата парсинга",
]
# Верхний лимит карточек за один запуск (защита от бесконечного скролла)
YANDEX_MAX_PLACES = 300

# Прокси для Яндекс-парсера (опционально; пустые — парсим напрямую)
PROXY_SERVER = os.getenv("PROXY_SERVER", "")       # напр. http://proxy.example.com:8000
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")
