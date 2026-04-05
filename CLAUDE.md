# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Язык общения

Всегда отвечай на русском языке. Комментарии в коде, коммит-сообщения, документация — всё на русском.

## Обзор проекта

Парсер для квалификации компаний-клиентов на базе Rusprofile.ru с ИИ-анализом сайтов. Три этапа:
1. Парсинг Rusprofile + выгрузка в Google Sheets + Telegram Mini App бот
2. ИИ-квалификация компаний по анализу сайтов (GPT-4o-mini)
3. Масштабирование — дополнительные источники данных

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
- Telegram-бот (aiogram 3, асинхронный) с Mini App UI для взаимодействия с пользователем
- Интеграция с Google Sheets (gspread) для экспорта результатов
- Планируемая интеграция с OpenAI для анализа сайтов компаний

Секреты конфигурации в `config/` (credentials.json в .gitignore). Логи в `logs/`.
