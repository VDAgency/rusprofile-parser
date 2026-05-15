# ТЗ. Этап 2 — ИИ-квалификация компаний

> Документ — техническое задание на разработку конвейера ИИ-квалификации
> компаний, найденных парсером. Квалификация работает поверх уже собранных
> данных и определяет, является ли компания «горячим лидом» для покупки
> корпоративных сувениров.
>
> **Статус:** к реализации в новой сессии.  
> **Автор:** Claude (Sonnet 4.6).  
> **Дата составления:** 2026-05-14.

---

## 1. Контекст

Парсер (Этап 1) собирает компании из Rusprofile и Яндекс.Карт, дедуплицирует
их по теме, сохраняет в SQLite и выгружает в Google Sheets. Клиент — продавец
корпоративных сувениров и бизнес-подарков.

**Проблема:** тысячи найденных компаний нельзя обзвонить подряд — нужна
приоритизация. Большинство компаний по ОКВЭД потенциально подходят, но их
реальная готовность покупать сувениры сильно варьируется: живой ли бизнес,
есть ли HR/маркетинг, проводят ли корпоративы.

**Решение:** двухступенчатая квалификация:
1. **Скоринг** — быстрая оценка по дешёвым сигналам без ИИ.
2. **ИИ-анализ сайта** — LLM читает текст сайта компании и выносит вердикт.

Компании, прошедшие оба уровня, помечаются `ai_status = "hot"` и
приоритизируются в выгрузке.

---

## 2. Текущее состояние

Модель `Company` (`src/db/models.py`) содержит поля `site`, `phone`, `revenue`,
`raw_json` — достаточно для скоринга. Поле `source` говорит, откуда компания
(rusprofile / yandex_maps).

**Чего пока нет в схеме:**
- `ai_score` — числовой балл от скоринга
- `ai_status` — итог квалификации: `"hot"` / `"cold"` / `"skip"` / `null`
- `ai_comment` — краткое объяснение от LLM (≤ 200 символов)
- `ai_qualified_at` — дата последней квалификации

**Чего нет в коде:**
- модуль скоринга (`src/ai/scoring.py`)
- модуль извлечения текста с сайта (`src/ai/website_extractor.py`)
- клиент LLM (`src/ai/llm_client.py`)
- сервис квалификации (`src/services/qualify_service.py`)
- Alembic-миграция на новые поля
- кнопка «Квалифицировать» в Mini App
- новые колонки в Sheets-выгрузке

---

## 3. Архитектура конвейера

```
ParseRun → список company_id (is_new=True)
                ↓
       [1] Scoring  ─── score < threshold → ai_status="skip", стоп
                ↓
       [2] Website extractor  ─── нет сайта → ai_status="cold", стоп
                ↓
       [3] LLM client (OpenAI / OpenRouter)
                ↓
       ai_status="hot" / "cold"  +  ai_comment
                ↓
       upsert → companies.ai_status, ai_score, ai_comment, ai_qualified_at
                ↓
       Google Sheets repush (с новыми колонками)
```

Конвейер запускается **асинхронно** после завершения парсинга — либо
автоматически (если включён флаг `qualify=True`), либо вручную кнопкой
из истории запусков.

---

## 4. Уровень 1 — Скоринг (без ИИ)

### 4.1 Сигналы и веса

| Сигнал | Откуда | Вес |
|--------|--------|-----|
| Есть сайт | `company.site` | +3 |
| Есть телефон | `company.phone` | +2 |
| Есть email | `company.email` | +1 |
| Есть выручка за год | `company.revenue != None` | +2 |
| Статус «Действующая» | `company.status` | +2 |
| Выручка ≥ 5 млн руб | `raw_json.finance_revenue` ≥ 5_000_000 | +2 |
| Есть сотрудники > 5 | `raw_json.sshr` > 5 | +1 |
| Источник Яндекс.Карты (живой бизнес) | `company.source == "yandex_maps"` | +1 |
| Флаг «адрес недействителен» в Rusprofile | `raw_json.invalid_address == True` | −3 |
| Компания в стадии ликвидации / банкротства | `company.status` in ("ликвидируется", ...) | −5 |
| Нет ни телефона, ни сайта, ни email | все три отсутствуют | −2 |

**Итоговый score** — сумма весов всех применимых сигналов.  
**Порог:** `score < 4` → `ai_status = "skip"`, дальнейшая обработка не нужна.

### 4.2 Реализация

Файл: `src/ai/scoring.py`

```python
from dataclasses import dataclass

THRESHOLD = 4

@dataclass
class ScoreResult:
    score: int
    signals: list[str]   # человекочитаемые объяснения, зачем пригодится при отладке

def score_company(company) -> ScoreResult:
    ...
```

Функция **синхронная**, вызывается без I/O — быстро и просто тестировать.

---

## 5. Уровень 2 — Извлечение текста с сайта

### 5.1 Цель

Получить чистый текст страницы без HTML-разметки, CSS, JS-кода, меню и
футеров. На выходе — строка ≤ 3000 токенов (примерно 12 000 символов),
пригодная для передачи в LLM.

### 5.2 Что извлекаем

1. **Заголовки** `<h1>…<h6>` — с высоким приоритетом.
2. **Мета-теги** `<meta name="description">` и `<meta property="og:description">`.
3. **Основной текст** `<p>`, `<li>`, `<td>`, `<span>` (в разумных блоках).
4. **Alt-атрибуты** картинок (`<img alt="…">`) — часто содержат ключевые слова.
5. **Title** страницы.

### 5.3 Что НЕ берём

- Содержимое `<script>`, `<style>`, `<noscript>`, `<svg>`.
- Атрибуты HTML-тегов кроме alt, title.
- Навигационные блоки (`<nav>`, `<header>`, `<footer>`) — эвристически по
  тегу или по ARIA-ролям.
- Дублирующиеся строки (whitelist: первое вхождение).

### 5.4 Реализация

Файл: `src/ai/website_extractor.py`

```python
async def extract_website_text(url: str, timeout_s: int = 15) -> str | None:
    """Возвращает чистый текст сайта или None, если не удалось загрузить.

    Пробует httpx (быстро, без JS), при статусе 403/429 или пустом теле —
    fallback на Playwright (headless Chromium, тот же экземпляр что парсер).
    Текст обрезается до MAX_CHARS символов.
    """
```

**Два режима загрузки:**

| Режим | Когда | Инструмент |
|-------|-------|-----------|
| HTTP-запрос | По умолчанию | `httpx.AsyncClient` с рандомным UA |
| Headless-браузер | Сайт требует JS или вернул 403/пустой HTML | Playwright (переиспользует уже открытый browser-контекст парсера) |

**Ограничения:**
- Таймаут: 15 секунд.
- Максимум текста: 12 000 символов (после этого обрезается).
- Если сайта нет или загрузка упала — возвращаем `None`, компания получает
  `ai_status = "cold"` с комментарием «сайт недоступен».

---

## 6. Уровень 3 — ИИ-квалификация

### 6.1 Провайдер и модель

Настраивается через `.env`:

```env
AI_PROVIDER=openai          # openai | openrouter
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=gpt-4o-mini        # дефолт; может быть meta-llama/... и т.п.
AI_MAX_TOKENS=300
AI_TEMPERATURE=0.2
```

Файл: `src/ai/llm_client.py` — тонкая обёртка над `openai.AsyncOpenAI`.
OpenRouter совместим с OpenAI SDK (просто другой `base_url`).

### 6.2 Промпт

Системный промпт (задаётся один раз, хранится в `config/qualify_prompt.txt`,
чтобы клиент мог его редактировать без деплоя):

```
Ты — аналитик продаж компании, продающей корпоративные сувениры и бизнес-подарки.
Тебе предоставлен текст сайта потенциального клиента.
Определи, является ли эта компания потенциальным покупателем корпоративных сувениров.

Признаки горячего лида:
- Компания с сотрудниками (HR-отдел, корпоративная культура)
- Проводит мероприятия, корпоративы, конференции
- Продаёт товары клиентам (нужны подарки покупателям)
- Упоминает партнёрские программы, лояльность
- Есть дилерская/дистрибьюторская сеть

Признаки нерелевантной компании:
- Чисто производственное предприятие без B2C/B2B продаж
- Государственное учреждение без маркетинговой активности
- Компания в очевидном кризисе (сайт мёртв, нет активности)

Ответь строго в JSON без лишнего текста:
{"status": "hot" | "cold" | "unknown", "comment": "<до 150 символов на русском>"}
```

Пользовательское сообщение:
```
Компания: {company.name}
Регион: {company.region}
Текст сайта:
---
{website_text}
---
```

### 6.3 Разбор ответа

- Парсим JSON из ответа (с fallback через regex если LLM добавил markdown).
- `status` → `ai_status` в БД.
- `comment` → `ai_comment` (обрезаем до 200 символов).
- При ошибке парсинга JSON: `ai_status = "unknown"`, `ai_comment = "Ошибка разбора ответа"`.
- При исключении HTTP: повтор 1 раз с задержкой 5 сек, затем `ai_status = "unknown"`.

---

## 7. Сервис квалификации

Файл: `src/services/qualify_service.py`

```python
async def qualify_run(
    run_id: int,
    tenant_id: int,
    progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
) -> QualifyResult:
    """Квалифицирует все новые компании из run_id.

    Работает только с is_new=True записями RunCompany.
    progress_cb(done, total) вызывается после каждой компании.
    """
```

**Параллелизм:** обрабатываем компании последовательно (не параллельно) —
запросы к сайтам уже создают нагрузку, LLM имеет rate limit. При необходимости
добавить `asyncio.Semaphore(3)` для ограничения одновременных запросов.

**Идемпотентность:** пропускаем компанию, у которой `ai_qualified_at` установлен
в текущие сутки — чтобы повторный запуск квалификации не тратил токены зря.
Перезапустить принудительно можно флагом `force=True`.

---

## 8. Миграция БД

Файл: `alembic/versions/XXXX_add_ai_fields.py`

```python
op.add_column("companies", sa.Column("ai_score", sa.Integer(), nullable=True))
op.add_column("companies", sa.Column("ai_status", sa.String(10), nullable=True))
op.add_column("companies", sa.Column("ai_comment", sa.Text(), nullable=True))
op.add_column("companies", sa.Column("ai_qualified_at", sa.DateTime(), nullable=True))

op.create_index("ix_company_ai_status", "companies", ["tenant_id", "ai_status"])
```

Добавить соответствующие `Mapped` поля в модель `Company`.

---

## 9. Интеграция с парсером

### 9.1 Флаг в запросе парсинга

В `ParseRequest` (структура фильтров из Mini App) добавить опциональное поле:

```python
qualify: bool = False   # запустить ИИ-квалификацию после парсинга
```

В `parse_service.py::run_rusprofile` и `run_yandex` в конце:

```python
if payload.qualify:
    await qualify_service.qualify_run(run_id, tenant_id, progress_cb=...)
```

### 9.2 Ручной запуск из истории

Новый API-эндпоинт: `POST /api/runs/{id}/qualify`

- Проверяет, что `run.tenant_id == текущий пользователь`.
- Запускает `qualify_run(run_id, ...)` в фоне (`asyncio.create_task`).
- Возвращает `{"status": "started"}`.
- По завершении отправляет уведомление в Telegram: «Квалификация завершена:
  🔥 {hot} горячих, ❄ {cold} холодных, ⏭ {skip} пропущено».

---

## 10. Изменения в Google Sheets

Новые колонки после существующих (добавить в `src/api/export_xlsx.py` и
в функцию `push_to_sheets`):

| Колонка | Поле | Формат |
|---------|------|--------|
| ИИ-балл | `ai_score` | число |
| ИИ-статус | `ai_status` | `hot` / `cold` / `skip` / `unknown` |
| ИИ-комментарий | `ai_comment` | текст |
| Дата квалификации | `ai_qualified_at` | `ДД.ММ.ГГГГ` |

Строки с `ai_status = "hot"` подсвечиваются зелёным через
`gspread`-форматирование (условное форматирование по значению колонки).

---

## 11. Изменения в Mini App

### 11.1 Форма парсинга

Добавить чекбокс под кнопкой запуска (оба источника):

```html
<label class="checkbox-label">
  <input type="checkbox" id="qualify" name="qualify">
  <span>ИИ-квалификация после парсинга</span>
</label>
<div class="hint">
  Анализирует сайты найденных компаний и выделяет горячие лиды.
  Занимает ~10 сек на компанию.
</div>
```

### 11.2 Карточки в истории

В карточке запуска добавить:
- Статусную строку: `🔥 5 горячих | ❄ 12 холодных | ⏭ 8 пропущено` (если
  квалификация уже выполнялась).
- Кнопку «Квалифицировать» (если квалификации ещё не было).
- Кнопка недоступна (серая), пока квалификация выполняется.

### 11.3 Прогресс квалификации

Использовать тот же `statusBlock`, что и для парсинга. Статус-строка:
«Квалификация: 7 / 20 компаний проверено…»

---

## 12. Переменные окружения (добавить в `.env.example`)

```env
# ИИ-квалификация (Этап 2)
AI_PROVIDER=openai              # openai | openrouter
OPENAI_API_KEY=                 # если AI_PROVIDER=openai
OPENROUTER_API_KEY=             # если AI_PROVIDER=openrouter
AI_MODEL=gpt-4o-mini
AI_MAX_TOKENS=300
AI_TEMPERATURE=0.2
AI_SCORE_THRESHOLD=4            # компании ниже порога → skip без ИИ
AI_QUALIFY_PROMPT_FILE=config/qualify_prompt.txt
```

---

## 13. Новые файлы и изменяемые файлы

### Создать

| Файл | Назначение |
|------|-----------|
| `src/ai/__init__.py` | пакет |
| `src/ai/scoring.py` | скоринговая функция |
| `src/ai/website_extractor.py` | извлечение текста с сайта |
| `src/ai/llm_client.py` | клиент OpenAI/OpenRouter |
| `src/services/qualify_service.py` | оркестрация конвейера |
| `config/qualify_prompt.txt` | системный промпт (редактируется клиентом) |
| `alembic/versions/XXXX_add_ai_fields.py` | миграция |
| `tests/test_scoring.py` | тесты скоринга |
| `tests/test_qualify_service.py` | интеграционные тесты |

### Изменить

| Файл | Что изменить |
|------|-------------|
| `src/db/models.py` | добавить 4 поля в `Company` |
| `src/services/parse_service.py` | вызов `qualify_run` при `qualify=True` |
| `src/api/server.py` | эндпоинт `POST /api/runs/{id}/qualify` |
| `src/api/export_xlsx.py` | 4 новые колонки |
| `src/webapp/index.html` | чекбокс qualify + кнопка в истории |
| `src/webapp/app.v2.js` | логика qualify в форме и в истории |
| `.env.example` | новые переменные AI_* |

---

## 14. Зависимости

Добавить в `requirements.txt`:

```
httpx>=0.27          # HTTP-клиент для загрузки сайтов (уже может быть в проекте)
openai>=1.30         # поддерживает OpenAI и OpenRouter через base_url
```

---

## 15. Риски и ограничения

| Риск | Митигация |
|------|-----------|
| Сайт защищён Cloudflare / требует JS | Fallback на Playwright |
| LLM даёт не-JSON ответ | Regex-fallback парсер |
| Rate limit OpenAI | Последовательная обработка + retry 1x |
| Высокая стоимость при 300 компаниях | Скоринг режет ≥40% до ИИ; gpt-4o-mini дёшевый |
| Сайт на русском с кириллицей | gpt-4o-mini понимает русский нативно |
| Компания без сайта | `ai_status = "cold"`, без LLM-запроса |

---

## 16. Критерии приёмки

- [ ] Миграция применяется без ошибок на чистой БД.
- [ ] `pytest tests/test_scoring.py` — 100% зелёные.
- [ ] `pytest tests/test_qualify_service.py` — 100% зелёные (моки LLM и httpx).
- [ ] Парсинг с `qualify=True`: после завершения в Telegram приходит сводка.
- [ ] Компании с `ai_status="hot"` помечены зелёным в Sheets.
- [ ] Кнопка «Квалифицировать» в истории запускает процесс и показывает прогресс.
- [ ] Идемпотентность: повторный запрос квалификации пропускает уже обработанных.
- [ ] Парсер Яндекс.Карт и Rusprofile НЕ затронуты при `qualify=False`.

---

## 17. Стартовый промпт для новой сессии

```
Привет. Работаем над проектом rusprofile-parser — парсер компаний для
клиента (сувенирный бизнес). Этап 1 (парсинг, дедуп, Mini App,
Sheets) уже готов. Сейчас реализуем Этап 2 — ИИ-квалификацию компаний.

ТЗ: docs/tz_ai_qualification.md
Модели БД: src/db/models.py
Сервис парсинга: src/services/parse_service.py

Выполни по ТЗ:
1. Alembic-миграцию: добавь ai_score, ai_status, ai_comment, ai_qualified_at
   в модель Company.
2. src/ai/scoring.py — скоринговая функция с сигналами из раздела 4.
3. src/ai/website_extractor.py — извлечение текста (httpx + Playwright fallback).
4. src/ai/llm_client.py — клиент OpenAI/OpenRouter.
5. src/services/qualify_service.py — оркестрация.
6. Тесты: tests/test_scoring.py, tests/test_qualify_service.py.
7. API-эндпоинт POST /api/runs/{id}/qualify.
8. UI: чекбокс в форме + кнопка/статус в истории.

Начни с миграции, потом src/ai/, потом сервис, потом API, потом UI.
После каждого крупного блока — коммит.
```
