# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Язык общения

Всегда отвечай на русском языке. Комментарии в коде, коммит-сообщения, документация — всё на русском.

## Обзор проекта

Парсер для квалификации компаний-клиентов на базе Rusprofile.ru с ИИ-анализом сайтов. Этапы:
1. **Парсинг Rusprofile + Я.Карты + выгрузка в Google Sheets + Telegram Mini App бот.** ✅ В проде.
2. **ИИ-квалификация компаний по анализу сайтов (GPT-4o-mini), 5-этапный конвейер, кросс-обогащение источников.** ✅ В проде с 2026-05-15. См. [docs/tz_ai_qualification_v3.md](docs/tz_ai_qualification_v3.md), [docs/qualify_pipeline.md](docs/qualify_pipeline.md).
3. **Личный кабинет, тарифы (Trial / Basic / Pro), биллинг через ЮKassa.** 🚧 В работе с 2026-05-20. См. [docs/tz_billing_tariffs.md](docs/tz_billing_tariffs.md), [docs/roadmap_billing_tariffs.md](docs/roadmap_billing_tariffs.md).
4. Масштабирование — дополнительные источники данных.

## Стек технологий

- **Python 3.11+**
- **Playwright** (Chromium) — парсинг JS-страниц Rusprofile
- **BeautifulSoup4 + lxml** — извлечение данных из HTML
- **aiogram 3** — Telegram-бот с Mini App интерфейсом
- **gspread + google-auth** — выгрузка в Google Sheets
- **OpenAI API** — ИИ-квалификация (этап 2)
- **APScheduler** — планировщик задач

## Установка

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env       # заполнить данными
```

## Переменные окружения

См. `.env.example`. Необходимые секреты: `TELEGRAM_BOT_TOKEN`, `RUSPROFILE_LOGIN`/`RUSPROFILE_PASSWORD`, `GOOGLE_SHEET_ID`, JSON-ключ Google в `config/credentials.json`.

## Архитектура

Весь исходный код в `src/`. Проект объединяет:
- Скрапер на Playwright — авторизация на Rusprofile и извлечение данных о компаниях
- Telegram-бот (aiogram 3) + HTTP API (aiohttp) в одном процессе
- Mini App UI с двумя вкладками: «Парсинг» и «История»
- Интеграция с Google Sheets (gspread) для экспорта результатов
- Справочник ОКВЭД с поиском по «живым» запросам (`src/okved/`, `data/okved/`)
- Параллельный источник Яндекс Карт (`src/yandex_maps/`)
- Локальная БД (SQLite, миграции Alembic) для дедупа и истории запусков
- Планируемая интеграция с OpenAI для анализа сайтов компаний

Секреты конфигурации в `config/` (credentials.json в .gitignore).
SQLite-файл — `data/parser.db` (в .gitignore). Логи в `logs/`.

## Справочник ОКВЭД

* Данные: `data/okved/okved_enriched.json` (справочник с описаниями)
  и `data/okved/okved_targets.json` (пресеты под сувенирку).
* Бэкенд: `src/okved/` — `search_by_text`, `expand_children`,
  `get_preset_codes`, `filter_codes_in_handbook`. Документация в
  `data/okved/README.md`, ТЗ в `docs/tz_okved_handbook.md`.
* Mini App использует копии файлов в `src/webapp/okved.json` и
  `src/webapp/okved_targets.json`. После любых правок справочника
  выполнять `python scripts/sync_webapp_okved.py` — он копирует
  и минифицирует JSON для фронта.
* Тесты: `pytest tests/test_okved.py` (24 теста — целостность
  иерархии, поиск, пресеты).

## База данных, дедуп и история

* Схема: `Tenant → Theme → ParseRun → RunCompany → Company`. Multi-tenancy
  с самого начала: `tenant_id` во всех таблицах. Tenant = telegram_user_id.
* Дедуп — гибрид: ИНН/ОГРН — главные ключи; телефон — fallback **только**
  если у обеих сторон ИНН и ОГРН пустые. Алгоритм в `src/db/dedup.py`.
* Тема — стабильный SHA-256 от канонизированных фильтров, дедуп идёт
  внутри темы.
* Лимит парсинга — «Сколько новых компаний найти» (1–300, default 100)
  в Mini App; парсер останавливается при достижении.
* Документация: ТЗ — `docs/tz_dedup_db.md`, краткий обзор — `data/db/README.md`.
* Тесты: `pytest tests/test_db.py tests/test_api_auth.py`.

## Миграции

* SQLAlchemy 2.0 + Alembic. Миграции в `alembic/versions/`.
* Новая миграция: `alembic revision --autogenerate -m "что"`.
* Применить: `alembic upgrade head` (часть деплой-чеклиста).

## HTTP API для Mini App

* `src/api/server.py` — aiohttp в том же процессе, что и aiogram-бот.
* Эндпойнты `/api/history`, `/api/runs/{id}/repush`, `/api/runs/{id}/xlsx`,
  `/api/healthz`. Все приватные требуют валидный
  Telegram WebApp initData (`X-Telegram-Init-Data`, проверка HMAC в
  `src/api/auth.py`, TTL 12 ч).
* Nginx проксирует `/api/` → `127.0.0.1:8080`.

## Важные правила работы

* Парсер Яндекс Карт (`src/yandex_maps/`) — рабочий и используется в
  проде, без согласования его не трогать.
* Деплой на сервер делаем только после полной локальной проверки;
  при правках схемы БД — обязательно `alembic upgrade head` после
  `git pull` на сервере.
