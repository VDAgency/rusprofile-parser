# Rusprofile Parser

Парсер квалификации компаний-клиентов на базе Rusprofile.ru с ИИ-анализом сайтов.

## Стек

- **Python 3.11+** — основной язык
- **Playwright** — парсинг JS-страниц Rusprofile
- **BeautifulSoup4** — извлечение данных из HTML
- **aiogram 3** — Telegram-бот с Mini App интерфейсом
- **gspread** — выгрузка результатов в Google Sheets
- **OpenAI API** — ИИ-квалификация компаний по сайтам

## Этапы

| Этап | Описание | Статус |
|------|----------|--------|
| 1 | Парсинг Rusprofile + Google Sheets + Telegram Mini App | 🟢 В работе |
| 2 | ИИ-квалификация по анализу сайтов (GPT-4o-mini) | 🟡 После этапа 1 |
| 3 | Масштабирование — доп. источники данных | 🔵 Отдельный контракт |

## Структура проекта

```
├── src/            # Основной код (парсер, бот, интеграции)
├── config/         # Конфигурация (credentials, настройки)
├── docs/           # Документация и роадмап
├── logs/           # Логи работы парсера
├── .env.example    # Шаблон переменных окружения
└── requirements.txt
```

## Быстрый старт

```bash
# Клонировать репозиторий
git clone <repo-url>
cd rusprofile-parser

# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Установить зависимости
pip install -r requirements.txt
playwright install chromium

# Настроить переменные окружения
cp .env.example .env
# Заполнить .env своими данными
```
