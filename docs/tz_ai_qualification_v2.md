# ТЗ. Этап 2 — ИИ-квалификация компаний (v2)

> **Документ** — техническое задание на конвейер ИИ-квалификации компаний,
> найденных парсером. Конвейер — **универсальный**: работает не под конкретный
> бизнес клиента, а под произвольный бриф ("кого ищем"), что позволяет
> переиспользовать систему в SaaS.
>
> **Статус:** к реализации.
> **Версия:** v2 (предыдущая версия — `tz_ai_qualification.md` от 2026-05-14).
> **Дата:** 2026-05-15.
> **Изменения относительно v1:** добавлена стадия извлечения профиля из брифа,
> разделён скоринг (хард-фильтры + сорт-скор), добавлен keyword-скоринг
> на тексте сайта, новая таблица `ai_profiles`, двухшаговый UX в Mini App,
> обогащён ответ LLM (signals + hook), отдельный раздел по логированию.

---

## 1. Контекст

Парсер (Этап 1) собирает компании из Rusprofile и Яндекс.Карт, дедуплицирует
их по теме, сохраняет в SQLite и выгружает в Google Sheets. Объём:
50–100 компаний за один запуск парсинга (максимум 300), 2–5 тысяч компаний
в месяц на одного клиента.

**Проблема:** даже после ОКВЭД-фильтрации и дедупа большинство компаний нужно
квалифицировать вручную — звонить, ходить по сайтам. Это узкое место.

**Решение:** автоматическая квалификация через пятиуровневый конвейер.
Конвейер настраивается **брифом клиента в свободной форме** — он описывает,
кого хочет найти, а система сама извлекает критерии, ключевые слова и затем
оценивает сайты найденных компаний.

**Первый клиент** — продавец корпоративных сувениров, ищет оптовые компании
(канцтовары и т.п.) как потенциальных покупателей. **Но архитектура задумана
универсальной** — следующие клиенты могут искать что угодно (поставщиков,
партнёров, B2B-сервисы), не меняя кода.

---

## 2. Текущее состояние

### 2.1 Что уже есть

- Модель `Company` с полями `site`, `phone`, `email`, `revenue`, `status`,
  `raw_json` — достаточно для хард-фильтров и сорт-скоринга.
- Поле `source` (`rusprofile` / `yandex_maps`).
- `ParseRun` — связь парсингового запуска с темой и компаниями.
- Mini App с автодополнением ОКВЭД и формой фильтров.
- API эндпоинты для истории, repush, экспорта.

### 2.2 Чего нет в БД

**Новая таблица:**
- `ai_profiles` — бриф клиента + извлечённые ключевые слова/критерии.

**Новые поля в `Company`:**
- `ai_score` (Integer) — сорт-скоринг 0–100 для сортировки в Sheets
- `ai_status` (String) — `"hot"` / `"cold"` / `"skip"` / `"unknown"`
- `ai_comment` (Text) — краткое объяснение решения ≤ 200 символов
- `ai_signals` (JSON) — массив сигналов от LLM
- `ai_hook` (Text) — зацепка для холодного звонка ≤ 100 символов
- `ai_keyword_matches_positive` (JSON) — какие позитивные ключевые слова нашлись на сайте
- `ai_keyword_matches_negative` (JSON) — какие негативные нашлись
- `ai_tokens_used` (Integer) — суммарный расход токенов LLM на эту компанию (0 если решили по keywords)
- `ai_qualified_at` (DateTime)
- `ai_profile_id` (FK → ai_profiles.id) — какой профиль использовали

**Новые поля в `ParseRun`:**
- `ai_profile_id` (FK → ai_profiles.id, nullable) — профиль, выбранный для квалификации этого запуска
- `ai_qualify_enabled` (Boolean) — была ли включена квалификация
- `ai_qualify_stats` (JSON) — агрегаты по завершению: `{hot, cold, skip, unknown, tokens_used, cost_rub}`

### 2.3 Чего нет в коде

| Файл | Назначение |
|------|-----------|
| `src/ai/profile_extractor.py` | LLM-извлечение профиля из брифа (Stage A) |
| `src/ai/scoring.py` | хард-фильтры + сорт-скоринг (Stage 0) |
| `src/ai/website_extractor.py` | извлечение текста сайта (Stage 1) |
| `src/ai/keyword_matcher.py` | поиск ключевых слов на тексте сайта (Stage 2) |
| `src/ai/llm_client.py` | клиент OpenAI/OpenRouter |
| `src/ai/qualifier.py` | оркестрация Stage 3 (LLM-вызов и парсинг ответа) |
| `src/services/qualify_service.py` | сервис: квалификация всех компаний из ParseRun |
| `src/services/profile_service.py` | CRUD-сервис профилей + кеш по хешу |
| `config/prompts/extract_profile.txt` | системный промпт Stage A |
| `config/prompts/qualify_company.txt` | системный промпт Stage 3 |
| `alembic/versions/XXXX_add_ai_qualification.py` | миграция |

---

## 3. Архитектура конвейера

```
┌────────────────────────────────────────────────────────────────┐
│  PHASE 1 — ПОДГОТОВКА ПРОФИЛЯ (один раз на бриф)              │
│                                                                │
│  [Mini App]  Клиент пишет бриф свободным текстом              │
│        ↓                                                       │
│  [Stage A]   LLM Call №1: profile_extractor                   │
│        ↓                                                       │
│              Возвращает: ICP, semantic_criteria,              │
│                          keywords_positive[],                  │
│                          keywords_negative[]                   │
│        ↓                                                       │
│  [Mini App]  Клиент проверяет извлечённый профиль,            │
│              правит ключевые слова при желании,                │
│              сохраняет → ai_profiles                           │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│  PHASE 2 — ПАРСИНГ С КВАЛИФИКАЦИЕЙ                            │
│                                                                │
│  Клиент запускает парсинг, выбирает один из своих профилей   │
│        ↓                                                       │
│  Парсер собирает компании (50–300)                            │
│        ↓                                                       │
│   ─── далее для каждой is_new=True компании ───              │
│        ↓                                                       │
│  [Stage 0]   Хард-фильтры (статус, контакты)                  │
│              → если убитая → ai_status="skip", СТОП           │
│              Сорт-скор 0–100 → ai_score                       │
│        ↓                                                       │
│  [Stage 1]   Извлечение текста сайта                          │
│              (главная + /about + /services если есть)         │
│              → нет сайта или мусор → ai_status="cold", СТОП  │
│        ↓                                                       │
│  [Stage 2]   Keyword-скоринг сайта против профиля             │
│              → ≥3 позитивных, 0 негативных → "hot", СТОП     │
│              → ≥2 негативных, 0 позитивных → "cold", СТОП    │
│              → иначе → продолжаем в LLM                       │
│        ↓                                                       │
│  [Stage 3]   LLM Call №2: qualifier                           │
│              профиль + текст сайта → status, comment,         │
│              signals[], hook                                   │
│        ↓                                                       │
│  Сохранение в Company + лог решений                           │
│        ↓                                                       │
│  По завершении: агрегат в ParseRun.ai_qualify_stats,          │
│                 repush в Sheets, Telegram-уведомление         │
└────────────────────────────────────────────────────────────────┘
```

**Ключевая идея экономии токенов:**
- Stage 0 режет мусор (~20% компаний).
- Stage 1 режет компании без сайта (~10–15%).
- Stage 2 решает очевидные случаи без LLM (~30–40%).
- До Stage 3 (LLM) доходит ~25–40% компаний.

При 5000 компаний/мес ожидаемый расход LLM-токенов: 5000 × 0.35 × (3000 input + 200 output) ≈ 5.6 млн input + 350k output. На gpt-4o-mini это ~$0.85 + $0.21 ≈ **$1.1/мес** при текущих ценах. Дёшево.

---

## 4. Stage 0 — Хард-фильтры + сорт-скоринг

### 4.1 Хард-фильтры (бинарный kill)

Компания получает `ai_status = "skip"` и не идёт дальше, если выполнено хотя
бы одно:

| Условие | Источник |
|---------|----------|
| Статус "ликвидируется", "ликвидирована", "банкротство" | `company.status` |
| Флаг недействительного адреса | `raw_json.invalid_address == True` |
| Нет ни сайта, ни телефона, ни email | все три отсутствуют |

### 4.2 Сорт-скоринг (0–100)

Используется **только для сортировки в Sheets**, не для отсечения. Чтобы
менеджер видел сверху "крупная компания с выручкой 100 млн" и обзванивал в
правильном порядке.

| Сигнал | Источник | Баллы |
|--------|----------|-------|
| Есть сайт | `company.site` | +20 |
| Есть телефон | `company.phone` | +15 |
| Есть email | `company.email` | +10 |
| Выручка ≥ 5 млн | `raw_json.finance_revenue` | +15 |
| Сотрудников > 5 | `raw_json.sshr` | +10 |
| Статус "действующая" | `company.status` | +15 |
| Источник Яндекс.Карты | `company.source` | +10 |
| Есть в раскрытии бухотчётности | `raw_json.balance_year` | +5 |

Сумма ≤ 100. Сохраняется в `ai_score`.

### 4.3 Файл

`src/ai/scoring.py`

```python
from dataclasses import dataclass

@dataclass
class ScoreResult:
    score: int                # 0–100
    hard_filter_passed: bool  # False → ai_status="skip"
    signals: list[str]        # человекочитаемые объяснения (для лога)
    kill_reasons: list[str]   # если hard_filter_passed=False — почему

def score_company(company) -> ScoreResult:
    ...
```

Функция **синхронная**, без I/O — легко тестировать.

---

## 5. Stage A — Извлечение профиля из брифа клиента (LLM Call №1)

### 5.1 Цель

Превратить свободный текст брифа в структурированный профиль, который
дальше используется в Stage 2 и Stage 3.

### 5.2 Входные данные

Бриф клиента — строка ≤ 2000 символов, например:

> "Я продаю корпоративные сувениры и подарки. Хочу найти оптовые
> компании, которые торгуют канцтоварами, бытовой химией или
> косметикой — они могут покупать у меня подарки для своих клиентов
> и партнёров. Маленькие розничные магазины не подходят, госконторы
> тоже мимо."

### 5.3 Выходные данные

JSON-объект:

```json
{
  "icp_description": "Оптовая компания (B2B), продающая FMCG-товары другим бизнесам (канцтовары, бытовая химия, косметика). Имеет дилерскую/партнёрскую сеть или работает с корпоративными клиентами.",
  "semantic_criteria": [
    "Компания работает в B2B, а не с физлицами",
    "Имеет собственный склад или товарные остатки",
    "Имеет дилеров, партнёров или корпоративных клиентов, которым уместно дарить сувениры",
    "Не является государственным учреждением",
    "Действующий бизнес — не однодневка, не на грани закрытия"
  ],
  "keywords_positive": [
    "опт", "оптом", "оптовая", "оптовые поставки",
    "B2B", "юридическим лицам", "корпоративным клиентам",
    "дилер", "дилерская сеть", "партнёрская программа",
    "склад", "склады", "со склада",
    "поставщик", "поставка", "поставки",
    "канцтовары", "канцелярия", "офисные товары"
  ],
  "keywords_negative": [
    "розничный магазин", "только для физлиц",
    "госзакупки", "тендеры 44-ФЗ", "бюджетное учреждение",
    "ликвидация компании", "распродажа склада закрытие"
  ]
}
```

### 5.4 Промпт (`config/prompts/extract_profile.txt`)

```
Ты — аналитик, который превращает свободный бриф клиента в структурированный
профиль идеального лида для парсера компаний.

Бриф клиента:
---
{brief}
---

Извлеки из брифа и верни СТРОГО JSON без пояснений и markdown:

{
  "icp_description": "<развёрнутое описание идеального клиента, 2-3 предложения>",
  "semantic_criteria": ["<критерий 1>", "<критерий 2>", ...],
  "keywords_positive": ["<слово>", ...],
  "keywords_negative": ["<слово>", ...]
}

Требования:
- keywords_positive: 8–20 элементов. Реальные термины, которые встречаются
  на сайтах целевых компаний. Включай синонимы и словоформы (опт, оптом,
  оптовая). Lowercase, на русском.
- keywords_negative: 3–10 элементов. То, что говорит "это НЕ наш клиент".
- semantic_criteria: 3–6 коротких критериев, по которым LLM в следующем
  шаге будет оценивать конкретные сайты.
- icp_description: 2–3 предложения, чтобы менеджер сразу понимал, кого ищем.
```

### 5.5 Файл

`src/ai/profile_extractor.py`

```python
async def extract_profile_from_brief(brief: str) -> ProfileData:
    """LLM Call №1.

    Прогоняет бриф через LLM, парсит JSON-ответ, возвращает структуру.
    При невалидном JSON — retry 1 раз с явным указанием формата.
    """
```

### 5.6 Кеширование

В таблице `ai_profiles` поле `brief_hash` (SHA-1 от нормализованного брифа).
Перед вызовом LLM проверяем: есть ли уже профиль с таким хешем у этого
tenant. Если есть — переиспользуем без LLM-вызова.

---

## 6. Stage 1 — Извлечение текста с сайта

### 6.1 Цель

Получить чистый текст ≤ 12 000 символов (≈ 3000 токенов) с сайта компании,
пригодный для keyword-скоринга и подачи в LLM.

### 6.2 Что забираем

1. **Главная страница** — обязательно.
2. **Дополнительные страницы** (если есть в навигации первого уровня):
   - `/about`, `/o-kompanii`, `/o-nas` — про компанию
   - `/services`, `/uslugi`, `/products` — что продают
   - `/contacts`, `/kontakty` — филиалы, география

Конкатенируем, обрезаем до 12 000 символов суммарно.

### 6.3 Инструменты

Используем **trafilatura** — готовая Python-библиотека для извлечения
основного контента (убирает меню, футер, рекламу). Простая и проверенная.

**Два режима загрузки:**

| Режим | Когда | Инструмент |
|-------|-------|-----------|
| HTTP-запрос | По умолчанию | `httpx.AsyncClient` с рандомным UA |
| Headless-браузер | 403/429/пустой HTML | Playwright (переиспользует контекст парсера) |

**Таймаут:** 15 секунд на страницу, 45 секунд на всю компанию (главная + добор).

### 6.4 Валидация результата

После извлечения проверяем:

| Проверка | Действие |
|----------|----------|
| Текст < 300 символов | `ai_status = "unknown"`, comment "мало контента на сайте" |
| Заглушка хостинга ("сайт скоро откроется", "domain parked", "404") | `ai_status = "cold"`, comment "сайт-заглушка" |
| Только англоязычный текст без кириллицы | пометить флагом `lang_warning`, всё равно прогнать через LLM |
| Загрузка не удалась | `ai_status = "cold"`, comment "сайт недоступен" |

### 6.5 Файл

`src/ai/website_extractor.py`

```python
@dataclass
class WebsiteExtractResult:
    text: str | None
    pages_fetched: list[str]      # какие URL реально загрузились
    used_playwright: bool         # был ли fallback
    validation_issue: str | None  # "stub" / "too_short" / "fetch_failed" / None
    fetch_duration_ms: int

async def extract_website_text(
    base_url: str,
    timeout_s: int = 45,
    max_chars: int = 12000,
) -> WebsiteExtractResult:
    ...
```

---

## 7. Stage 2 — Keyword-скоринг сайта против профиля

### 7.1 Логика

Берём текст сайта (lowercase, нормализованный) и считаем совпадения с
`profile.keywords_positive` и `profile.keywords_negative`.

**Решение:**

| Условие | Итог |
|---------|------|
| `positive_matches ≥ KW_HOT_MIN` (default 3) **и** `negative_matches == 0` | `ai_status="hot"`, LLM **НЕ вызываем** |
| `negative_matches ≥ KW_COLD_MIN` (default 2) **и** `positive_matches == 0` | `ai_status="cold"`, LLM **НЕ вызываем** |
| Все остальные случаи | передаём в Stage 3 (LLM) |

Все найденные ключевые слова сохраняем в
`ai_keyword_matches_positive` / `ai_keyword_matches_negative` — попадут
в Sheets и в лог.

### 7.2 Нормализация

- Текст сайта: lowercase, замена ё→е, замена не-буквенно-цифровых на пробел.
- Совпадение: подстрока, ограниченная границами слов (`\b{kw}\b` через `regex`).
- Учёт частоты не делаем — достаточно факта присутствия. Иначе SEO-простыни
  будут давать ложные срабатывания.

### 7.3 Файл

`src/ai/keyword_matcher.py`

```python
@dataclass
class KeywordMatchResult:
    positive_matches: list[str]
    negative_matches: list[str]
    decision: str  # "hot" / "cold" / "needs_llm"
    decision_reason: str

def match_keywords(
    site_text: str,
    profile: AIProfile,
    hot_threshold: int = 3,
    cold_threshold: int = 2,
) -> KeywordMatchResult:
    ...
```

Функция синхронная, без I/O.

---

## 8. Stage 3 — ИИ-квалификация (LLM Call №2)

### 8.1 Когда вызываем

Только если Stage 2 вернул `decision="needs_llm"`. То есть сайт не дал
однозначного сигнала через ключевые слова.

### 8.2 Промпт (`config/prompts/qualify_company.txt`)

```
Ты — аналитик продаж. Оцени, является ли компания потенциальным клиентом
для нашего бизнеса.

Идеальный клиент: {icp_description}

Критерии оценки:
{semantic_criteria_bulleted}

Информация о компании:
- Название: {company.name}
- Регион: {company.region}
- ОКВЭД: {company.okved} ({company.okved_descr})
- Найдены позитивные ключевые слова: {keyword_matches_positive}
- Найдены негативные ключевые слова: {keyword_matches_negative}

Текст сайта компании:
---
{website_text}
---

Ответь СТРОГО в JSON без пояснений и markdown:

{
  "status": "hot" | "cold" | "unknown",
  "comment": "<до 150 символов на русском, объясни решение>",
  "signals": ["<сигнал 1>", "<сигнал 2>", ...],
  "hook": "<до 100 символов: зацепка для холодного звонка>"
}

Где:
- status: "hot" если компания соответствует критериям, "cold" если нет,
  "unknown" если данных недостаточно для решения
- signals: 2–5 коротких фактов из сайта, на которых основано решение
- hook: конкретная зацепка для менеджера ("у вас на сайте указана
  дилерская программа — у нас есть подарки для партнёров"). Только
  если status="hot", иначе пустая строка.
```

### 8.3 Параметры LLM

| Параметр | Значение |
|----------|----------|
| Модель | `gpt-4o-mini` (default), настраивается через `.env` |
| `max_tokens` | 400 |
| `temperature` | 0.2 |
| `response_format` | `{"type": "json_object"}` |

### 8.4 Разбор ответа

- Парсим JSON напрямую (включён `response_format=json_object`).
- Fallback regex если LLM всё-таки вернул markdown (`\`\`\`json ... \`\`\``).
- `comment` обрезаем до 200 символов, `hook` до 100.
- `signals` ограничиваем до 5 элементов.

### 8.5 Обработка ошибок

| Ошибка | Действие |
|--------|----------|
| Невалидный JSON | retry 1 раз с уточнением формата → `ai_status="unknown"` |
| HTTP-ошибка от OpenAI | retry 1 раз через 5 сек → `ai_status="unknown"` |
| Rate limit | exponential backoff: 5/15/30 сек, потом `unknown` |
| Превышен timeout (30 сек) | `ai_status="unknown"` |

### 8.6 Файл

`src/ai/qualifier.py`

```python
async def qualify_company_with_llm(
    company: Company,
    site_text: str,
    profile: AIProfile,
    keyword_result: KeywordMatchResult,
) -> QualifyLLMResult:
    ...
```

---

## 9. Сервис квалификации

### 9.1 Файл

`src/services/qualify_service.py`

```python
async def qualify_run(
    run_id: int,
    tenant_id: int,
    profile_id: int,
    progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
    force: bool = False,
) -> QualifyRunStats:
    """Квалифицирует все новые компании из run_id по выбранному профилю."""

@dataclass
class QualifyRunStats:
    total: int
    hot: int
    cold: int
    skip: int
    unknown: int
    llm_calls: int           # сколько компаний реально дошли до LLM
    tokens_used_total: int
    cost_rub_estimate: float
    duration_seconds: int
```

### 9.2 Параллелизм

- **Stage 1 (загрузка сайтов):** `asyncio.Semaphore(3)` — сетевой bound.
- **Stage 3 (LLM-вызовы):** `asyncio.Semaphore(5)` — OpenAI tier 1 держит.
- Stage 0 и Stage 2 — синхронные, без ограничений.

При 100 компаниях с параллелизмом 3 — ожидаемое время ~5–8 минут (вместо
17 минут последовательной обработки).

### 9.3 Идемпотентность

Компанию пропускаем, если `ai_qualified_at` установлен в текущие сутки
**и** `ai_profile_id` совпадает с переданным. Перезапустить принудительно —
флагом `force=True`.

### 9.4 Хард-лимит

В одном qualify_run обрабатываем максимум **300 компаний** (берём первые 300
по `ai_score desc`). Защита от случайного запуска на огромном run.

### 9.5 Прогресс

`progress_cb(done, total)` вызывается каждые 5 компаний (не каждую — чтобы
не перегружать Telegram webhook).

---

## 10. Миграция БД

Файл: `alembic/versions/XXXX_add_ai_qualification.py`

```python
# Таблица профилей
op.create_table(
    "ai_profiles",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("brief", sa.Text(), nullable=False),
    sa.Column("brief_hash", sa.String(40), nullable=False, index=True),
    sa.Column("icp_description", sa.Text(), nullable=False),
    sa.Column("semantic_criteria", sa.JSON(), nullable=False),
    sa.Column("keywords_positive", sa.JSON(), nullable=False),
    sa.Column("keywords_negative", sa.JSON(), nullable=False),
    sa.Column("extraction_model", sa.String(50)),
    sa.Column("extraction_tokens_used", sa.Integer(), default=0),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.Column("is_active", sa.Boolean(), default=True),
)
op.create_index("ix_ai_profiles_tenant_active", "ai_profiles", ["tenant_id", "is_active"])

# Поля в Company
op.add_column("companies", sa.Column("ai_score", sa.Integer()))
op.add_column("companies", sa.Column("ai_status", sa.String(10)))
op.add_column("companies", sa.Column("ai_comment", sa.Text()))
op.add_column("companies", sa.Column("ai_signals", sa.JSON()))
op.add_column("companies", sa.Column("ai_hook", sa.Text()))
op.add_column("companies", sa.Column("ai_keyword_matches_positive", sa.JSON()))
op.add_column("companies", sa.Column("ai_keyword_matches_negative", sa.JSON()))
op.add_column("companies", sa.Column("ai_tokens_used", sa.Integer(), default=0))
op.add_column("companies", sa.Column("ai_qualified_at", sa.DateTime()))
op.add_column("companies", sa.Column("ai_profile_id", sa.Integer(), sa.ForeignKey("ai_profiles.id")))
op.create_index("ix_company_ai_status", "companies", ["tenant_id", "ai_status"])

# Поля в ParseRun
op.add_column("parse_runs", sa.Column("ai_profile_id", sa.Integer(), sa.ForeignKey("ai_profiles.id")))
op.add_column("parse_runs", sa.Column("ai_qualify_enabled", sa.Boolean(), default=False))
op.add_column("parse_runs", sa.Column("ai_qualify_stats", sa.JSON()))
```

Соответственно — `Mapped`-поля в моделях `Company`, `ParseRun` и новая модель
`AIProfile` в `src/db/models.py`.

---

## 11. Интеграция с парсером

### 11.1 Запрос парсинга

В `ParseRequest` добавляем:

```python
ai_profile_id: Optional[int] = None   # если задан — после парсинга запускается квалификация
```

В `parse_service.py::run_rusprofile` и `run_yandex`:

```python
if payload.ai_profile_id:
    await qualify_service.qualify_run(
        run_id=run.id,
        tenant_id=tenant_id,
        profile_id=payload.ai_profile_id,
        progress_cb=...,
    )
```

### 11.2 Ручной запуск квалификации

**Новый эндпоинт:** `POST /api/runs/{id}/qualify`

Body:
```json
{ "profile_id": 5, "force": false }
```

- Проверяет `run.tenant_id == текущий пользователь`.
- Запускает `qualify_run(...)` в фоне (`asyncio.create_task`).
- Возвращает `{"status": "started"}`.
- По завершении — Telegram-уведомление:
  «🔥 Квалификация завершена: 12 горячих, 23 холодных, 5 пропущено, 60 проверено через ИИ. Потрачено токенов: 180000 (~12 ₽).»

### 11.3 API профилей

| Метод | Эндпоинт | Назначение |
|-------|----------|-----------|
| `POST` | `/api/profiles/extract` | Принимает `{brief, name}`, вызывает Stage A, возвращает извлечённый профиль (ещё не сохранённый) |
| `POST` | `/api/profiles` | Сохраняет профиль (после подтверждения клиентом) |
| `GET` | `/api/profiles` | Список профилей tenant'а |
| `GET` | `/api/profiles/{id}` | Один профиль |
| `PATCH` | `/api/profiles/{id}` | Правка имени, ключевых слов, критериев |
| `DELETE` | `/api/profiles/{id}` | Soft delete (`is_active=False`) |

---

## 12. Mini App — двухшаговый запуск + работа с профилями

### 12.1 Новый раздел "Профили квалификации"

В bottom-nav добавляем пункт **"ИИ-профили"**. Внутри:

- Список существующих профилей с превью (название, дата создания, кол-во ключевых слов).
- Кнопка **"+ Создать профиль"**.
- Тап по профилю — открывает редактирование.

### 12.2 Создание профиля — два шага

**Шаг 1: "Опишите, кого ищем"**

```
┌────────────────────────────────────────┐
│  Создание ИИ-профиля                   │
├────────────────────────────────────────┤
│  Название профиля:                     │
│  [Сувениры — оптовики                ] │
│                                        │
│  Опишите своими словами, какие         │
│  компании вы ищете и зачем:            │
│  ┌────────────────────────────────────┐│
│  │ Я продаю корпоративные сувениры... ││
│  │                                    ││
│  │                                    ││
│  └────────────────────────────────────┘│
│                                        │
│  💡 Подробнее = лучше. Опишите кто    │
│     ваш клиент, кто точно не подходит, │
│     что для вас "горячий лид".         │
│                                        │
│  [    Подготовить профиль    ]         │
└────────────────────────────────────────┘
```

После нажатия — статус-бар: "ИИ извлекает ключевые слова… (~5 сек)".

**Шаг 2: "Проверьте профиль"**

```
┌────────────────────────────────────────┐
│  Проверьте профиль                     │
├────────────────────────────────────────┤
│  Идеальный клиент:                     │
│  Оптовая компания (B2B), продающая    │
│  FMCG-товары другим бизнесам...       │
│  [Развернуть]                          │
│                                        │
│  Признаки горячего лида:               │
│  • Компания работает в B2B...          │
│  • Имеет собственный склад...          │
│  [Развернуть]                          │
│                                        │
│  Позитивные ключевые слова (15):       │
│  [опт ×] [оптом ×] [B2B ×] [дилер ×]  │
│  [+ добавить]                          │
│                                        │
│  Негативные ключевые слова (5):        │
│  [розница ×] [госзакупки ×]            │
│  [+ добавить]                          │
│                                        │
│  [Сохранить профиль]                   │
│  [Переделать (изменить бриф)]          │
└────────────────────────────────────────┘
```

Клиент может удалять/добавлять ключевые слова перед сохранением.

### 12.3 Форма парсинга

В существующей форме парсинга — выпадающий список:

```
ИИ-квалификация: [Не использовать ▼]
                 ├ Не использовать
                 ├ Сувениры — оптовики
                 ├ Подарки на праздники
                 └ + Создать новый профиль
```

### 12.4 Карточка запуска в истории

После завершения квалификации показываем:

```
┌────────────────────────────────────────┐
│  Запуск №42 — 87 компаний              │
│  Самарская обл., ОКВЭД 46.49.3         │
│                                        │
│  🔥 12 горячих                         │
│  ❄ 23 холодных                         │
│  ⏭ 5 пропущено (мёртвые)               │
│  ❓ 7 неопределено                      │
│                                        │
│  Профиль: Сувениры — оптовики          │
│  Токенов: 180k (~12 ₽)                │
│                                        │
│  [Открыть в Sheets] [Excel в чат]      │
└────────────────────────────────────────┘
```

Если квалификации не было — кнопка "Запустить квалификацию" с выбором профиля.

### 12.5 Прогресс

Тот же `statusBlock`, что для парсинга:
"Квалификация: 24 / 87 компаний… 🔥 8 найдено."

---

## 13. Google Sheets

### 13.1 Новые колонки

В конце текущих колонок добавляем:

| Колонка | Поле | Формат |
|---------|------|--------|
| Сорт-балл | `ai_score` | число 0–100 |
| ИИ-статус | `ai_status` | hot / cold / skip / unknown |
| Комментарий ИИ | `ai_comment` | текст |
| Сигналы | `ai_signals` | через запятую |
| Зацепка для звонка | `ai_hook` | текст |
| ✓ позитивные | `ai_keyword_matches_positive` | через запятую |
| ✗ негативные | `ai_keyword_matches_negative` | через запятую |
| Дата квалификации | `ai_qualified_at` | `ДД.ММ.ГГГГ ЧЧ:ММ` |

### 13.2 Сортировка

Строки сортируются по: `ai_status='hot' first`, потом `ai_score desc`.

### 13.3 Форматирование

- `ai_status="hot"` → зелёная заливка строки.
- `ai_status="cold"` → серая заливка строки.
- `ai_status="skip"` → светло-серая, шрифт серый.
- Колонка "Зацепка для звонка" — жирный шрифт.

Через `gspread` форматирование, не условное форматирование на стороне Google
(оно не сохранится при repush).

---

## 14. Логирование, метрики, observability

> **Это критично:** клиент должен видеть, почему компания получила тот или
> иной статус, а мы — где конвейер ломается. Логируем щедро.

### 14.1 Структурированный лог решений

Каждая компания, прошедшая квалификацию, генерирует один JSON-лог в
`logs/qualify/qualify_{run_id}_{date}.jsonl`:

```json
{
  "company_id": 1234,
  "company_name": "ООО Канцторг",
  "inn": "7707083893",
  "profile_id": 5,
  "stages": {
    "stage_0_scoring": {
      "score": 75,
      "hard_filter_passed": true,
      "signals": ["site", "phone", "revenue>5M", "active"],
      "kill_reasons": []
    },
    "stage_1_website": {
      "fetched": true,
      "pages": ["https://kanztorg.ru/", "https://kanztorg.ru/about"],
      "text_length": 8420,
      "used_playwright": false,
      "validation_issue": null,
      "duration_ms": 2340
    },
    "stage_2_keywords": {
      "positive_matches": ["опт", "оптовая", "B2B", "склад"],
      "negative_matches": [],
      "decision": "hot",
      "decision_reason": "4 positive, 0 negative → above threshold"
    },
    "stage_3_llm": null,
    "final": {
      "ai_status": "hot",
      "ai_score": 75,
      "tokens_used": 0,
      "decision_path": "stage_2"
    }
  },
  "timestamp": "2026-05-20T14:32:18+03:00"
}
```

Это даёт **полную трассируемость** каждого решения.

### 14.2 Метрики run

В `ParseRun.ai_qualify_stats` после завершения:

```json
{
  "total": 87,
  "by_status": {"hot": 12, "cold": 23, "skip": 5, "unknown": 7, "needs_recheck": 0},
  "decisions_by_stage": {
    "stage_0_skip": 5,
    "stage_1_cold": 8,
    "stage_2_hot": 10,
    "stage_2_cold": 12,
    "stage_3_llm_hot": 2,
    "stage_3_llm_cold": 3,
    "stage_3_llm_unknown": 7
  },
  "llm_calls": 12,
  "tokens_used_total": 180000,
  "tokens_input": 170000,
  "tokens_output": 10000,
  "cost_rub_estimate": 12.5,
  "duration_seconds": 380,
  "errors": []
}
```

### 14.3 Сырые ответы LLM

В отдельный файл `logs/qualify/llm_raw_{run_id}.jsonl` пишем сырой запрос
и ответ LLM (для каждого Stage 3 вызова). Понадобится, если клиент скажет
"эта компания должна быть hot, а у вас cold" — посмотрим, что LLM реально
ответил.

### 14.4 Алерты

В Telegram-уведомление включаем:
- Если `errors` непустой → отдельная строка "⚠ Ошибок: N (см. лог)".
- Если `cost_rub_estimate > 50` → предупреждение.
- Если `tokens_used_total > AI_MONTHLY_BUDGET_TOKENS` (накопленный за месяц) →
  блокировка дальнейших запусков до конца месяца.

---

## 15. Переменные окружения

Добавить в `.env.example`:

```env
# ─── ИИ-квалификация (Этап 2) ───

# Провайдер и ключи
AI_PROVIDER=openai                    # openai | openrouter
OPENAI_API_KEY=
OPENROUTER_API_KEY=

# Модели
AI_MODEL_EXTRACT=gpt-4o-mini          # Stage A — извлечение профиля
AI_MODEL_QUALIFY=gpt-4o-mini          # Stage 3 — оценка компании
AI_MAX_TOKENS_EXTRACT=1500
AI_MAX_TOKENS_QUALIFY=400
AI_TEMPERATURE=0.2

# Промпты
AI_PROMPT_EXTRACT_FILE=config/prompts/extract_profile.txt
AI_PROMPT_QUALIFY_FILE=config/prompts/qualify_company.txt

# Stage 2 — пороги keyword-скоринга
KW_HOT_MIN=3                          # минимум позитивных совпадений для hot без LLM
KW_COLD_MIN=2                         # минимум негативных для cold без LLM

# Stage 1 — извлечение сайтов
WEBSITE_TIMEOUT_S=15                  # таймаут одной страницы
WEBSITE_MAX_PAGES=3                   # главная + 2 дополнительных
WEBSITE_MAX_CHARS=12000               # лимит итогового текста

# Параллелизм
QUALIFY_WEBSITE_CONCURRENCY=3         # одновременных загрузок сайтов
QUALIFY_LLM_CONCURRENCY=5             # одновременных LLM-вызовов
QUALIFY_MAX_COMPANIES_PER_RUN=300     # хард-лимит на один запуск

# Бюджет
AI_MONTHLY_BUDGET_TOKENS=10000000     # лимит токенов в месяц, при превышении — блок
AI_COST_PER_1K_INPUT_RUB=0.015        # для расчёта оценочной стоимости
AI_COST_PER_1K_OUTPUT_RUB=0.06
```

---

## 16. Новые файлы и изменяемые файлы

### Создать

| Файл | Назначение |
|------|-----------|
| `src/ai/__init__.py` | пакет |
| `src/ai/scoring.py` | Stage 0 — хард-фильтры + сорт-скор |
| `src/ai/profile_extractor.py` | Stage A — LLM Call №1 |
| `src/ai/website_extractor.py` | Stage 1 — текст сайта |
| `src/ai/keyword_matcher.py` | Stage 2 — keyword-скоринг |
| `src/ai/llm_client.py` | клиент OpenAI/OpenRouter |
| `src/ai/qualifier.py` | Stage 3 — LLM Call №2 |
| `src/services/qualify_service.py` | оркестрация конвейера |
| `src/services/profile_service.py` | CRUD + кеш профилей |
| `src/api/profiles_router.py` | API эндпоинты профилей |
| `config/prompts/extract_profile.txt` | системный промпт Stage A |
| `config/prompts/qualify_company.txt` | системный промпт Stage 3 |
| `alembic/versions/XXXX_add_ai_qualification.py` | миграция |
| `tests/test_scoring.py` | тесты хард-фильтров и сорт-скора |
| `tests/test_keyword_matcher.py` | тесты Stage 2 |
| `tests/test_profile_extractor.py` | тесты с моком LLM |
| `tests/test_qualify_service.py` | интеграционные тесты конвейера |
| `tests/fixtures/sample_briefs.json` | примеры брифов для тестов |
| `tests/fixtures/sample_sites/` | HTML-фикстуры реальных сайтов |

### Изменить

| Файл | Что изменить |
|------|-------------|
| `src/db/models.py` | новые поля в `Company`, `ParseRun`; новая модель `AIProfile` |
| `src/services/parse_service.py` | вызов `qualify_run` при `ai_profile_id` |
| `src/api/server.py` | подключение `profiles_router`, эндпоинт `POST /api/runs/{id}/qualify` |
| `src/api/export_xlsx.py` | новые колонки + сортировка |
| `src/api/sheets_pusher.py` | новые колонки + цветовое форматирование |
| `src/webapp/index.html` | bottom-nav "ИИ-профили", форма создания, выбор в форме парсинга |
| `src/webapp/app.v2.js` | логика создания профиля, выбор в парсинге, отображение метрик |
| `.env.example` | новые AI_* переменные |
| `requirements.txt` | trafilatura, openai, regex |

---

## 17. Зависимости

Добавить в `requirements.txt`:

```
httpx>=0.27          # уже может быть
openai>=1.30         # совместим с OpenRouter через base_url
trafilatura>=1.8     # извлечение основного контента с веб-страниц
regex>=2024.0        # юникодные \b для русского
```

---

## 18. Риски и митигации

| Риск | Митигация |
|------|-----------|
| LLM в Stage A извлёк не те ключевые слова | Двухшаговый UX — клиент видит результат и правит |
| LLM возвращает не-JSON | `response_format=json_object` + retry с уточнением + regex-fallback |
| Сайт защищён Cloudflare / требует JS | Playwright-fallback (переиспользуем контекст парсера) |
| Сайт-заглушка / односtраничник без контента | Валидация на Stage 1: < 300 символов → unknown |
| Rate limit OpenAI | Semaphore(5) + exponential backoff |
| Случайный запуск на 10к компаний | Хард-лимит `QUALIFY_MAX_COMPANIES_PER_RUN=300` |
| Сжигание бюджета токенов | Месячный лимит `AI_MONTHLY_BUDGET_TOKENS`, блок при превышении |
| "Эта компания должна быть hot, а у вас cold" | Полный structured-лог всех решений (раздел 14) — всегда можно посмотреть, что и почему |
| Изменение тарифов OpenAI | Стоимость рассчитывается через `.env` переменные, не хардкод |

---

## 19. Критерии приёмки

- [ ] Миграция применяется без ошибок на чистой БД.
- [ ] `pytest tests/` — 100% зелёные (включая моки LLM и httpx).
- [ ] Через Mini App можно создать новый профиль: ввести бриф → увидеть извлечённые ключевые слова → отредактировать → сохранить.
- [ ] Кеширование профиля по `brief_hash` работает: повторное создание с тем же текстом не вызывает LLM.
- [ ] Запуск парсинга с выбранным профилем: после завершения парсинга стартует квалификация, в Telegram приходит сводка.
- [ ] Stage 2 (keyword-скоринг) решает явные кейсы без LLM — это видно в логе `decision_path = "stage_2"`.
- [ ] Stage 3 (LLM) вызывается только для спорных компаний; `tokens_used` ненулевой только в этих случаях.
- [ ] Компании с `ai_status="hot"` подсвечены зелёным в Sheets, отсортированы первыми.
- [ ] Кнопка "Запустить квалификацию" в истории запусков работает с выбором профиля.
- [ ] Идемпотентность: повторный qualify-run с тем же профилем в течение суток пропускает уже обработанных.
- [ ] Хард-лимит 300 компаний на run работает.
- [ ] Месячный лимит токенов работает: при превышении новые запуски блокируются с понятной ошибкой.
- [ ] Парсер Rusprofile и Яндекс.Карт не затронуты при `ai_profile_id=None`.
- [ ] Структурированные логи решений пишутся в `logs/qualify/*.jsonl` и читаемы.

---

## 20. План реализации (порядок коммитов)

1. **Миграция БД** — таблица `ai_profiles`, поля в `Company` и `ParseRun`.
2. **`src/ai/scoring.py`** — Stage 0, синхронная функция + тесты.
3. **`src/ai/llm_client.py`** — тонкая обёртка над OpenAI SDK + мок для тестов.
4. **`src/ai/profile_extractor.py`** — Stage A, промпт, парсинг JSON + тесты на моках.
5. **`src/services/profile_service.py`** — CRUD + кеш по `brief_hash`.
6. **`src/api/profiles_router.py`** — API профилей.
7. **`src/ai/website_extractor.py`** — Stage 1, trafilatura + Playwright fallback + валидация.
8. **`src/ai/keyword_matcher.py`** — Stage 2, синхронный матчинг + тесты.
9. **`src/ai/qualifier.py`** — Stage 3, промпт, парсинг ответа.
10. **`src/services/qualify_service.py`** — оркестрация всех стадий + логирование.
11. **API:** эндпоинт `POST /api/runs/{id}/qualify`, интеграция в `parse_service`.
12. **Sheets:** новые колонки, сортировка, форматирование.
13. **Mini App:** раздел профилей, двухшаговое создание, выбор в форме парсинга, метрики в истории.
14. **Документация:** `docs/qualify_pipeline.md` — короткий гид для клиента.

После каждого коммита — короткий smoke-test через Mini App.

---

## 21. Стартовый промпт для новой сессии

```
Привет. Работаем над проектом rusprofile-parser. Парсер Rusprofile (Этап 1)
сейчас в работе у другого разработчика, я его не трогаю. Параллельно
начинаем реализацию Этапа 2 — ИИ-квалификации компаний по универсальному
конвейеру.

ТЗ: docs/tz_ai_qualification_v2.md
Модели БД: src/db/models.py
Сервис парсинга: src/services/parse_service.py
Mini App: src/webapp/

Архитектура — пятиуровневый конвейер:
  Stage A (LLM) — извлекаем профиль из брифа клиента
  Stage 0      — хард-фильтры + сорт-скор (без ИИ)
  Stage 1      — извлекаем текст сайта (trafilatura + Playwright fallback)
  Stage 2      — keyword-скоринг сайта против профиля (без ИИ)
  Stage 3 (LLM) — оценка спорных кейсов через gpt-4o-mini

Стартуем по плану из раздела 20 ТЗ. Начни с миграции БД, потом Stage 0,
потом llm_client. После каждого блока — коммит, я проверю.

Код-стиль: БЫЛО/СТАЛО для всех правок, без неутверждённых изменений,
архитектурное согласование до реализации, русский неформальный регистр.
```
