# ТЗ. Этап 2 — ИИ-квалификация компаний (v3)

> **Документ** — техническое задание для коллеги Claude в VSCode на реализацию
> универсального конвейера квалификации лидов с двусторонним кросс-обогащением
> источников и тарифной системой Simple/AI.
>
> **Версия:** v3
> **Предыдущие версии:**
> - `tz_ai_qualification.md` (v1, 2026-05-14) — изначальный план
> - `tz_ai_qualification_v2.md` (v2, 2026-05-15) — универсальный пятиуровневый конвейер
> **Дата:** 2026-05-15
>
> **Главные отличия v3 от v2:**
> 1. Добавлен **Stage E — кросс-обогащение источников** (Я.Карты ↔ Rusprofile)
> 2. Добавлена **тарифная система** Simple / AI с квотами на ИИ-обработку
> 3. **Stage 0 переработан** в универсальный скоринг с тремя группами сигналов
> 4. Добавлена **задача по доработке парсера Я.Карт** (дата последнего отзыва)
> 5. Конвейер работает **на обоих тарифах**, ИИ-стадии исполняются только на AI

---

## 1. Контекст

Парсер собирает компании из Rusprofile и Яндекс.Карт, дедуплицирует их по
теме, сохраняет в SQLite и выгружает в Google Sheets. Объёмы: 50–300 компаний
за один запуск парсинга, 2–5 тысяч компаний/мес на клиента.

**Стратегическая цель:** превратить парсер в SaaS-продукт. Текущий клиент
(сувенирная продукция) — первый кейс. Архитектура должна поддерживать:

- Множество независимых клиентов (мультитенантность уже заложена).
- Универсальный скоринг — без жёсткой привязки к нише клиента.
- Два тарифа использования:
  - **Simple** — без ИИ. Парсинг + универсальный скоринг + кросс-обогащение.
    Клиент сам решает по сорт-баллу, кому звонить.
  - **AI** — всё то же + ИИ-квалификация по брифу клиента.
    Лимитирован квотой (N компаний или M токенов в месяц).

**Ключевая идея универсальности скоринга:** «насколько компания живая и
платёжеспособная» — этот вопрос задаётся одинаково для всех ниш. Сигналы:
действующая или нет, размер по выручке, активность (свежие отзывы, рейтинг),
количество каналов связи, сайт жив или нет.

---

## 2. Изменения относительно v2 (changelog)

| Раздел | Что изменилось |
|--------|----------------|
| 3. Архитектура | Добавлен Stage E (кросс-обогащение), Stage 0 разделён на 0a (quick kill) и 0b (sort score) |
| 4. Stage 0 | Полностью переработан: универсальный скоринг с 3 группами сигналов A/B/C |
| **5 (новый)** | Stage E — кросс-обогащение источников (Я.Карты ↔ Rusprofile) |
| **6 (новый)** | Тарифная система Simple/AI и квоты |
| 7. Stage A | Без изменений (LLM Call №1 — извлечение профиля из брифа) |
| 8. Stage 1 | Без изменений (извлечение текста сайта) |
| 9. Stage 2 | Без изменений (keyword-скоринг) |
| 10. Stage 3 | Без изменений (LLM Call №2 — оценка) |
| **11 (новый)** | Доработка парсера Я.Карт: добавить дату последнего отзыва |
| 12. БД | + поля для тарифов и квот в `tenants`, + поля Я.Карт в `companies` |
| 13. Mini App | + индикатор тарифа и квоты, + скрытие ИИ-профиля для Simple |
| 14. Sheets | + колонки кросс-обогащения и Я.Карт-сигналов |

---

## 3. Текущее состояние

### 3.1 Что уже есть

- Модель `Company` с полями `site`, `phone`, `email`, `revenue`, `status`,
  `raw_json` и т.д.
- Модель `Tenant` (мультитенантность).
- Модель `ParseRun` (история запусков).
- Mini App с автодополнением ОКВЭД и формой фильтров.
- API эндпоинты для истории, repush, экспорта.
- Парсер Я.Карт собирает: название, рубрики, регион, адрес, телефон, сайт,
  **рейтинг**, **кол-во отзывов**, режим работы, координаты, ссылку на Я.Карты.

### 3.2 Чего нет в парсере Я.Карт

- **Дата последнего отзыва** — критический сигнал свежести/живости.
  Требует авторизации в Я.Картах через Playwright. Подробно — раздел 11.

### 3.3 Чего нет в БД

**Новые таблицы:**
- `ai_profiles` — бриф клиента + извлечённые ключевые слова/критерии.

**Новые поля в `Tenant`:**
- `tariff_plan` (String) — `"simple"` / `"ai"`
- `ai_quota_companies_monthly` (Integer) — лимит компаний на ИИ-обработку в месяц
- `ai_quota_tokens_monthly` (Integer) — лимит токенов в месяц
- `ai_companies_processed_period` (Integer) — счётчик обработанных компаний в текущем периоде
- `ai_tokens_used_period` (Integer) — счётчик потраченных токенов
- `quota_period_start` (DateTime) — начало текущего расчётного периода (для сброса счётчиков)

**Новые поля в `Company` (универсальные ИИ):**
- `ai_score` (Integer) — сорт-скор 0–100
- `ai_status` (String) — `"hot"` / `"cold"` / `"skip"` / `"unknown"` / `"quota_exceeded"`
- `ai_comment` (Text)
- `ai_signals` (JSON)
- `ai_hook` (Text)
- `ai_keyword_matches_positive` (JSON)
- `ai_keyword_matches_negative` (JSON)
- `ai_tokens_used` (Integer, default 0)
- `ai_qualified_at` (DateTime)
- `ai_profile_id` (FK → ai_profiles.id, nullable)

**Новые поля в `Company` (Я.Карты-сигналы для скоринга):**
- `yandex_rating` (Float) — рейтинг 0–5
- `yandex_reviews_count` (Integer) — количество отзывов
- `yandex_last_review_date` (Date) — дата последнего отзыва
- `yandex_hours_filled` (Boolean) — режим работы заполнен
- `yandex_coordinates_filled` (Boolean) — координаты есть
- `yandex_url` (String) — ссылка на карточку
- `yandex_operating_status` (String) — `"working"` / `"temporarily_closed"` / `"permanently_closed"` / `null`

**Новые поля в `Company` (кросс-обогащение):**
- `cross_enriched_at` (DateTime) — когда выполнено кросс-обогащение
- `cross_enrichment_source` (String) — `"yandex"` / `"rusprofile"` / `"both"` / `null`
- `cross_match_confidence` (Float) — насколько уверенно сматчили (0–1)

**Новые поля в `ParseRun`:**
- `ai_profile_id` (FK → ai_profiles.id, nullable)
- `ai_qualify_enabled` (Boolean) — была ли включена квалификация
- `ai_qualify_stats` (JSON) — агрегаты по завершению

### 3.4 Чего нет в коде

| Файл | Назначение |
|------|-----------|
| `src/yandex_maps/reviews.py` | Парсер даты последнего отзыва (новый, для доработки парсера Я.Карт) |
| `src/services/cross_enrichment_service.py` | Кросс-обогащение источников |
| `src/services/matching.py` | Алгоритмы матчинга компаний |
| `src/ai/scoring.py` | Универсальный сорт-скоринг |
| `src/ai/profile_extractor.py` | LLM Call №1 — извлечение профиля |
| `src/ai/website_extractor.py` | Извлечение текста сайта |
| `src/ai/keyword_matcher.py` | Keyword-скоринг сайта |
| `src/ai/llm_client.py` | Клиент OpenAI/OpenRouter |
| `src/ai/qualifier.py` | LLM Call №2 — оценка компании |
| `src/services/qualify_service.py` | Оркестрация конвейера (тариф-aware) |
| `src/services/profile_service.py` | CRUD профилей + кеш |
| `src/services/quota_service.py` | Учёт и контроль квот |
| `src/api/profiles_router.py` | API эндпоинты профилей |
| `src/api/tariff_router.py` | API тарифа и квот |
| `config/prompts/extract_profile.txt` | Промпт Stage A |
| `config/prompts/qualify_company.txt` | Промпт Stage 3 |
| `alembic/versions/XXXX_add_ai_qualification.py` | Миграция БД |

---

## 4. Архитектура конвейера

### 4.1 Тариф Simple (без ИИ)

```
[Парсер собирает компании]
        ↓
[Stage 0a] Быстрые хард-фильтры
   • статус "ликвидируется"/"банкротство" → skip
   • Я.Карты "временно/закрыто навсегда" → skip
   • нет ни сайта, ни телефона, ни email → skip
        ↓
[Stage E] Кросс-обогащение источников
   • Rusprofile-компания → ищем на Я.Картах по телефону (Е164)
   • Я.Карты-компания → ищем в Rusprofile по "название + город"
   • Заполняем недостающие поля
        ↓
[Stage 0b] Сорт-скоринг (универсальный, 3 группы)
   • Группа A: контактность (макс 30)
   • Группа B: платёжеспособность Rusprofile (макс 35)
   • Группа C: активность Я.Карт (макс 35)
   • Поздние хард-фильтры (свежесть отзывов, мёртвая карточка)
        ↓
Сохранение + Google Sheets с сортировкой по ai_score desc
```

### 4.2 Тариф AI (всё то же + ИИ)

```
[Mini App] Клиент пишет бриф ("кого ищем")
        ↓
[Stage A — LLM Call №1] Извлекаем профиль:
   ICP, semantic_criteria, keywords_positive[], keywords_negative[]
   (один раз, кешируем по хешу брифа)
        ↓
[Mini App] Клиент проверяет/правит ключевые слова → сохраняет
        ↓
─── Далее в каждом запуске парсинга ───
        ↓
[Парсер собирает компании]
        ↓
[Stage 0a → Stage E → Stage 0b]  (как в Simple)
        ↓
─── Только для прошедших Stage 0 (не skip) ───
─── Проверка квоты тарифа: если превышена — ai_status="quota_exceeded" ───
        ↓
[Stage 1] Извлечение текста сайта
   (главная + /about + /services если есть, trafilatura + Playwright fallback)
   → нет сайта или мусор → ai_status="cold", СТОП
        ↓
[Stage 2] Keyword-скоринг сайта против профиля
   → ≥3 позитивных, 0 негативных → "hot", LLM не нужен
   → ≥2 негативных, 0 позитивных → "cold", LLM не нужен
   → иначе → продолжаем в LLM
        ↓
[Stage 3 — LLM Call №2] Qualifier
   профиль + текст сайта + ключевые слова → status, comment, signals, hook
        ↓
Сохранение + repush в Sheets + Telegram-уведомление
```

### 4.3 Ключевая идея экономии токенов

На тарифе AI:
- Stage 0a режет очевидно мёртвые (~15%).
- Stage E подтягивает данные, повышая качество Stage 0b.
- Stage 0 (общий) режет ещё ~20%.
- Stage 1 режет компании без сайта (~10–15%).
- Stage 2 решает явные кейсы без LLM (~30–40% от оставшихся).
- До Stage 3 (LLM) доходит ~25–35% от всех компаний.

При 5000 компаний/мес расход LLM-токенов: ~5–8 ₽/мес. Дёшево.

---

## 5. Stage E — Кросс-обогащение источников

### 5.1 Цель

Когда компания пришла из одного источника, дообогатить её данными из второго.
Это нужно потому что:
- Rusprofile даёт юридические данные (выручка, статус, ОКВЭД) но не показывает,
  жив ли реально бизнес сейчас.
- Я.Карты показывают живость (отзывы, рейтинг, режим работы), но не дают
  юридических данных.

Чтобы корректно посчитать **универсальный сорт-скор**, нужны оба набора сигналов.

### 5.2 Направление 1: Rusprofile → Я.Карты

**Когда:** компания пришла из Rusprofile, поля `yandex_*` пусты.

**Алгоритм:**

1. Берём `company.phone`, нормализуем в Е164.
2. Открываем `https://yandex.ru/maps/?text={phone_e164}` через Playwright.
3. Ждём загрузки результатов поиска.
4. Если найдена ровно одна организация:
   - Заходим в карточку.
   - Парсим: `yandex_rating`, `yandex_reviews_count`, `yandex_last_review_date`,
     `yandex_hours_filled`, `yandex_coordinates_filled`, `yandex_url`,
     `yandex_operating_status`.
   - `cross_match_confidence = 1.0`, `cross_enrichment_source = "yandex"`.
5. Если найдено несколько — дополнительно фильтруем по совпадению названия
   (схожесть > 70%). Если остаётся одна — берём её, `confidence = 0.7`.
6. Если ничего не найдено — оставляем `yandex_*` пустыми, не считается ошибкой.

**Fallback:** если у компании нет телефона — пропускаем кросс-обогащение
из Rusprofile в Я.Карты (без телефона матчинг ненадёжен).

**Файл:** `src/services/cross_enrichment_service.py::enrich_from_yandex(company)`

### 5.3 Направление 2: Я.Карты → Rusprofile

**Когда:** компания пришла из Я.Карт, поля `revenue`, `inn`, `status` пусты.

**Алгоритм:**

1. Берём `company.name` и `company.region`.
2. Открываем Rusprofile, делаем поиск по названию (используем существующий
   модуль `src/rusprofile/parser.py`, режим точечного поиска).
3. Фильтруем результаты:
   - Регион должен совпадать с регионом из Я.Карт.
   - Схожесть названия ≥ 80% (Levenshtein или rapidfuzz).
4. Если остаётся **одна** запись — берём её, `confidence = 0.9`.
5. Если остаётся **несколько** — берём ту, у которой схожесть названия выше,
   `confidence = 0.6`. В лог пишем warning.
6. Если ноль — пропускаем, не ошибка.

7. Из найденной записи через `enrich_company_details` подтягиваем:
   `inn`, `ogrn`, `revenue`, `profit`, `employees_count`, `status`,
   `registration_date`.
8. `cross_enrichment_source = "rusprofile"`.

**Файл:** `src/services/cross_enrichment_service.py::enrich_from_rusprofile(company)`

### 5.4 Матчинг — общий модуль

`src/services/matching.py`

```python
from rapidfuzz import fuzz

def normalize_company_name(name: str) -> str:
    """Убирает ООО/АО/ИП, кавычки, лишние пробелы, lowercase."""

def name_similarity(name1: str, name2: str) -> float:
    """Возвращает 0–1 по rapidfuzz.fuzz.ratio после нормализации."""

def phone_to_e164(phone: str) -> str | None:
    """Приводит телефон к Е164. Через библиотеку phonenumbers."""

def regions_match(region1: str, region2: str) -> bool:
    """Сравнивает регионы. Учитывает: 'Самарская обл.' == 'Самарская область'."""
```

### 5.5 Параметры и поведение

**Когда запускается:** между Stage 0a (быстрые хард-фильтры) и Stage 0b
(сорт-скор), для каждой выжившей компании.

**Идемпотентность:** если `cross_enriched_at` стоит и менее 30 дней назад —
пропускаем (данные не успели устареть). Перезапустить — `force=True`.

**Параллелизм:** `asyncio.Semaphore(2)` — Я.Карты и Rusprofile чувствительны
к нагрузке, перебор приведёт к rate-limit/блокировкам.

**Опциональность:** в `ParseRequest` поле `enable_cross_enrichment: bool = True`.
Клиент может выключить в Mini App чекбоксом, если хочет быстрее.

**Таймаут:** 30 секунд на одну компанию (одно направление). Если не успело —
лог warning, поля пустые, скоринг считается по тому что есть.

### 5.6 Стоимость по времени

- Без кросс-обогащения: 50 компаний парсятся за ~30 сек.
- С кросс-обогащением: +5–8 сек/компания → 50 компаний за ~5–7 минут.

Для крупных запусков (300 компаний) — до 40 минут. Это приемлемо для
ночного запуска, но утром может быть долго. Прогресс в Mini App обязателен.

---

## 6. Тарифная система

### 6.1 Тарифы

| Тариф | Что включено | Лимит |
|-------|-------------|-------|
| **Simple** | Парсинг + Stage 0 + Stage E + Sheets | По договору (парсер-лимит, но без ИИ) |
| **AI** | Всё из Simple + Stage A/1/2/3 (ИИ-квалификация) | Месячная квота: N компаний ИЛИ M токенов |

Тариф хранится в `tenants.tariff_plan`. По умолчанию для нового пользователя —
`"simple"`. Переключение тарифа — пока вручную через БД (UI будет позже).

### 6.2 Квоты

Для тарифа AI задаются два независимых лимита:

| Квота | Что считает | Default (в `.env`) |
|-------|-------------|---------------------|
| `ai_quota_companies_monthly` | Количество компаний, прошедших Stage 3 (LLM вызов) | 1000 |
| `ai_quota_tokens_monthly` | Суммарные токены LLM (input + output) | 10_000_000 |

Достижение **любой** из квот блокирует дальнейшие ИИ-вызовы до сброса.

**Сброс счётчиков:** каждые 30 дней от `quota_period_start`. Это поле
обновляется при сбросе. Никаких календарных месяцев — сглаживает биллинг.

### 6.3 Поведение при превышении квоты

В `qualify_service` перед каждым LLM-вызовом проверяем:

```python
if tenant.ai_companies_processed_period >= tenant.ai_quota_companies_monthly:
    company.ai_status = "quota_exceeded"
    company.ai_comment = "Месячная квота ИИ исчерпана. Сброс — {date}."
    save(company)
    continue  # не вызываем LLM, идём дальше
```

Когда достигается 80% квоты — Telegram-уведомление клиенту:
«⚠ Вы использовали 80% месячной квоты ИИ-квалификации. Сброс через X дней.»

Когда квота полностью исчерпана — отдельное уведомление с предложением
повысить тариф (UI и платёжка — отдельный этап).

### 6.4 Сервис квот

`src/services/quota_service.py`

```python
class QuotaService:
    async def check_can_use_ai(self, tenant_id: int) -> bool:
        """True если квота не исчерпана."""

    async def increment_companies(self, tenant_id: int, n: int = 1) -> None:
        """+1 к счётчику компаний после успешного Stage 3."""

    async def increment_tokens(self, tenant_id: int, tokens: int) -> None:
        """+N к счётчику токенов."""

    async def reset_if_period_expired(self, tenant_id: int) -> None:
        """Если 30 дней прошло — сбрасываем счётчики, обновляем period_start."""

    async def get_usage(self, tenant_id: int) -> dict:
        """Для отображения в Mini App."""
```

Вызов `reset_if_period_expired` — в начале каждого qualify_run и в API-запросе
просмотра квоты.

### 6.5 Mini App — отображение тарифа

В заголовке Mini App плашка:

```
┌──────────────────────────────────────┐
│  Тариф: AI                           │
│  ИИ-квалификация: 234 / 1000 (23%)   │
│  ████░░░░░░░░░░░░░░░░░░░░░░░░░░░     │
│  Сброс: через 12 дней                │
└──────────────────────────────────────┘
```

Для Simple-тарифа плашка показывает:

```
┌──────────────────────────────────────┐
│  Тариф: Simple                       │
│  ИИ-квалификация не подключена.      │
│  [Подключить AI]                     │
└──────────────────────────────────────┘
```

Кнопка «Подключить AI» — пока заглушка с текстом «Свяжитесь с поддержкой».

### 6.6 Зависимость от тарифа в Mini App

| Элемент | Simple | AI |
|---------|--------|-----|
| Раздел «ИИ-профили» в bottom-nav | ❌ скрыт | ✅ показывается |
| Выпадающий список «ИИ-квалификация» в форме парсинга | ❌ скрыт | ✅ показывается |
| Карточка запуска в истории | Без блока ИИ | С блоком ИИ |
| Кнопка «Квалифицировать» на старом запуске | ❌ скрыта | ✅ показывается |

---

## 7. Stage 0 — Универсальный скоринг

### 7.1 Stage 0a — Быстрые хард-фильтры (kill)

Выполняются **до** кросс-обогащения. Цель — не тратить время на enrichment
очевидно мёртвых компаний.

| Условие | Источник |
|---------|----------|
| Статус "ликвидируется" / "ликвидирована" / "банкротство" | `company.status` |
| Флаг "адрес недействителен" | `raw_json.invalid_address` |
| Я.Карты: статус "Закрыто навсегда" | `yandex_operating_status == "permanently_closed"` |
| Я.Карты: статус "Временно не работает" | `yandex_operating_status == "temporarily_closed"` |
| Нет ни сайта, ни телефона, ни email | все три отсутствуют |

→ `ai_status = "skip"`, конвейер не идёт дальше.

### 7.2 Stage 0b — Сорт-скоринг (универсальный, 0–100)

Запускается **после** кросс-обогащения. Считает балл для сортировки в Sheets.

#### Группа A — Контактность и каналы связи (макс 30)

| Сигнал | Баллы |
|--------|-------|
| Есть рабочий сайт | +10 |
| Есть телефон | +5 |
| Есть email | +5 |
| Telegram-канал | +2 |
| ВКонтакте | +2 |
| WhatsApp | +2 |
| Instagram | +2 |
| Дополнительные мессенджеры | до +2 (сумма 4+ каналов даёт макс) |

#### Группа B — Платёжеспособность Rusprofile (макс 35)

| Сигнал | Баллы |
|--------|-------|
| Статус "действующая" | +10 |
| Есть данные по выручке | +5 |
| Выручка ≥ 5 млн ₽ | +5 |
| Выручка ≥ 50 млн ₽ | дополнительно +5 |
| Прибыль положительная | +5 |
| Сотрудников > 5 | +5 |

#### Группа C — Активность Я.Карт (макс 35)

| Сигнал | Баллы |
|--------|-------|
| Карточка существует на Я.Картах | +5 |
| Рейтинг ≥ 4.0 | +5 |
| Кол-во отзывов ≥ 10 | +5 |
| Дата последнего отзыва < 3 мес назад | +10 |
| Дата последнего отзыва 3–12 мес назад | +5 (вместо +10) |
| Дата последнего отзыва 12–24 мес назад | 0 (без баллов) |
| Режим работы заполнен | +5 |
| Координаты есть | +5 |

#### Поздние хард-фильтры (после кросс-обогащения)

| Условие | Действие |
|---------|----------|
| Дата последнего отзыва > 24 мес назад **и** карточка существует > 24 мес | `ai_status = "skip"` (мёртвая точка) |
| 0 отзывов **и** карточка существует > 12 мес | `ai_status = "skip"` (никто не ходит) |

### 7.3 Нормализация и интерпретация

**Максимально возможный балл** зависит от того, по каким источникам собраны данные:

| Источники | Макс. балл |
|-----------|-----------|
| Только Rusprofile (без матча в Я.Картах) | 65 (группы A + B) |
| Только Я.Карты (без матча в Rusprofile) | 65 (группы A + C) |
| Оба источника (матч сработал) | 100 |

В Sheets выводим **нормализованный процент** от максимума возможного,
плюс «звёздность» источников. Так компании из обоих источников естественно
поднимаются наверх.

### 7.4 Файл

`src/ai/scoring.py`

```python
from dataclasses import dataclass

@dataclass
class ScoreResult:
    score_raw: int            # суммарный балл
    score_max_possible: int   # 65 / 65 / 100
    score_normalized: int     # процент 0–100
    hard_filter_passed: bool  # False → ai_status="skip"
    kill_reasons: list[str]
    signals: dict             # подробно по группам, для лога

def score_company_early(company) -> ScoreResult:
    """Stage 0a — быстрые хард-фильтры."""

def score_company_full(company) -> ScoreResult:
    """Stage 0b — полный скоринг после кросс-обогащения."""
```

---

## 8. Stage A — Извлечение профиля из брифа (только AI)

> Без изменений относительно v2. Краткое описание ниже, детали — см. v2 раздел 5.

### 8.1 Когда запускается

Перед началом парсинга на AI-тарифе. Клиент в Mini App пишет бриф своими
словами. LLM извлекает: ICP, semantic_criteria, keywords_positive[],
keywords_negative[]. Сохраняется в `ai_profiles`.

### 8.2 Двухшаговый UX

1. **Шаг 1:** клиент пишет бриф, жмёт «Подготовить профиль» → LLM Call №1.
2. **Шаг 2:** клиент видит извлечённый профиль, правит ключевые слова,
   сохраняет.

### 8.3 Кеширование

В `ai_profiles.brief_hash` (SHA-1 от нормализованного брифа). Перед LLM-вызовом
проверяем — если профиль с таким хешем уже есть у tenant, переиспользуем.

### 8.4 Файлы

- `src/ai/profile_extractor.py` — функция `extract_profile_from_brief(brief)`
- `config/prompts/extract_profile.txt` — промпт
- `src/services/profile_service.py` — CRUD + кеш

---

## 9. Stage 1 — Извлечение текста сайта (только AI)

> Без изменений относительно v2. Краткое описание ниже.

Используется **trafilatura** для извлечения основного контента. Дополнительно
качаются страницы `/about`, `/services` если есть в навигации. Лимит 12k символов.
Fallback Playwright при 403/429. Валидация: <300 символов → unknown,
сайт-заглушка → cold.

Файл: `src/ai/website_extractor.py`

---

## 10. Stage 2 — Keyword-скоринг сайта (только AI)

> Без изменений относительно v2.

Текст сайта (lowercase, normalized) против `profile.keywords_positive/negative`.

| Условие | Итог |
|---------|------|
| ≥3 позитивных, 0 негативных | `ai_status="hot"`, LLM не вызываем |
| ≥2 негативных, 0 позитивных | `ai_status="cold"`, LLM не вызываем |
| Иначе | передаём в Stage 3 |

Пороги в `.env`: `KW_HOT_MIN=3`, `KW_COLD_MIN=2`.

Файл: `src/ai/keyword_matcher.py`

---

## 11. Stage 3 — LLM-квалификация (только AI)

> Без изменений относительно v2.

Промпт строится динамически из профиля. Модель: `gpt-4o-mini`. Response format
`json_object`. Ответ: `{status, comment, signals[], hook}`. После успешного
вызова — `quota_service.increment_companies(tenant_id, 1)` и
`increment_tokens(tenant_id, used)`.

Файлы:
- `src/ai/qualifier.py`
- `src/ai/llm_client.py`
- `config/prompts/qualify_company.txt`

---

## 12. Доработка парсера Я.Карт — дата последнего отзыва

> **Это отдельная задача**, которая блокирует полноценную работу Stage 0
> (скоринг свежести). Без неё группа C будет считаться неполной.

### 12.1 Цель

Добавить в карточку компании Я.Карт парсинг даты последнего отзыва.
Без авторизации Я.Карты часто отдают только относительные даты («3 месяца назад»),
с авторизацией — точные. Поэтому нужен логин.

### 12.2 Авторизация

В `.env`:
```
YANDEX_LOGIN=
YANDEX_PASSWORD=
YANDEX_SESSION_PATH=config/yandex_session.json
```

Логин — один раз в начале сессии парсинга. Storage state сохраняется в
`config/yandex_session.json` (добавить в `.gitignore`). При невалидной
сессии — повторный логин.

### 12.3 Парсинг даты

В карточке организации открыть вкладку «Отзывы», взять самый верхний
(самый свежий). Парсер должен понимать форматы:

| Формат | Пример | Что в БД |
|--------|--------|----------|
| Сегодня / Вчера | "сегодня в 14:32" | `today` / `today - 1 day` |
| N дней назад | "3 дня назад" | `today - 3 days` |
| N недель назад | "2 недели назад" | `today - 14 days` |
| N месяцев назад | "месяц назад", "5 месяцев назад" | `today - 30*N days` |
| Дата без года | "23 апреля" | текущий год если месяц ≤ текущий, иначе предыдущий |
| Дата с годом | "23 апреля 2024 г." | точная дата |

Парсер — модуль `src/yandex_maps/reviews.py`:

```python
from datetime import date
from playwright.async_api import Page

async def get_last_review_date(yandex_url: str, page: Page) -> date | None:
    """
    Открывает карточку, заходит во вкладку 'Отзывы',
    парсит дату самого свежего отзыва. Возвращает None если отзывов нет.
    """

def parse_relative_date(text: str, today: date) -> date | None:
    """Превращает 'вчера', '3 дня назад', '23 апреля' в date."""
```

### 12.4 Поведение при сбое

- Если авторизация не прошла — лог warning, продолжаем парсинг **без** даты
  отзыва (поле `yandex_last_review_date = None`).
- Если карточка не открылась — то же.
- Если дата нечитаемая — то же.
- **Никогда не ронять весь парсинг из-за одной карточки.**

### 12.5 Расширение: ещё какие поля стоит добавить

Опционально, если будет несложно при разборе карточки:

| Поле | Зачем |
|------|-------|
| `yandex_operating_status` | "Временно не работает" / "Закрыто навсегда" — для хард-фильтров |
| `yandex_hours_filled` | заполнен ли режим работы — для скоринга |
| `yandex_coordinates_filled` | есть ли координаты — для скоринга |
| `yandex_owner_verified` | "Подтверждено владельцем" — сильный сигнал живости |
| `yandex_response_rate` | отвечают ли на отзывы — необязательно |

Минимум для работы скоринга — первые три. Остальные на усмотрение разработчика.

### 12.6 Приёмка задачи парсера

- На тестовой выборке 30+ карточек дата отзыва парсится для большинства (>70%).
- Корректно обрабатываются все форматы дат (включая относительные).
- При отсутствии отзывов — пустая ячейка.
- При сбое авторизации — парсинг продолжается, пишется warning.
- Storage state сохраняется и переиспользуется при следующем запуске.

---

## 13. Сервис квалификации (orchestrator)

### 13.1 Файл

`src/services/qualify_service.py`

```python
async def qualify_run(
    run_id: int,
    tenant_id: int,
    profile_id: int | None = None,        # None для Simple-тарифа
    enable_cross_enrichment: bool = True,
    progress_cb: Callable | None = None,
    force: bool = False,
) -> QualifyRunStats:
    """Прогоняет все is_new=True компании из run_id через конвейер.

    Логика:
    1. Получаем tenant, проверяем tariff_plan.
    2. Для каждой компании:
       a. Stage 0a (быстрые хард-фильтры) → если skip, дальше не идём.
       b. Stage E (кросс-обогащение) если enable_cross_enrichment.
       c. Stage 0b (полный сорт-скор).
       d. Если tariff = simple → сохраняем, дальше не идём.
       e. Если tariff = ai и profile_id задан:
          - Проверка квоты. Если исчерпана → ai_status="quota_exceeded".
          - Stage 1 (сайт) → 2 (keyword) → 3 (LLM).
          - increment_companies, increment_tokens после Stage 3.
    3. Агрегируем статистику, сохраняем в ParseRun.ai_qualify_stats.
    4. Repush в Sheets.
    """
```

### 13.2 Структура статистики

```python
@dataclass
class QualifyRunStats:
    total: int
    by_status: dict[str, int]   # {hot, cold, skip, unknown, quota_exceeded}
    decisions_by_stage: dict[str, int]  # счётчики где остановилось
    cross_enrichment_matches: dict[str, int]  # {yandex_found, rusprofile_found, both, none}
    llm_calls: int
    tokens_used_total: int
    cost_rub_estimate: float
    duration_seconds: int
    errors: list[str]
```

### 13.3 Параллелизм

- Stage E: `asyncio.Semaphore(2)` (бережно с Я.Картами и Rusprofile)
- Stage 1 (сайты): `asyncio.Semaphore(3)`
- Stage 3 (LLM): `asyncio.Semaphore(5)`
- Stage 0 (a и b) — синхронные, без I/O.

### 13.4 Идемпотентность

- Кросс-обогащение пропускается, если `cross_enriched_at < 30 дней назад`.
- ИИ-квалификация пропускается, если `ai_qualified_at` в текущие сутки и
  `ai_profile_id` совпадает. Перезапуск — `force=True`.

### 13.5 Хард-лимит

Один qualify_run обрабатывает максимум **300 компаний** (берём первые 300
по `ai_score desc` после Stage 0b). Защита от случайного запуска на огромном
парсинге.

---

## 14. Миграция БД

Файл: `alembic/versions/XXXX_add_ai_qualification.py`

```python
# ─── Таблица профилей ───
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

# ─── Поля тарифа в tenants ───
op.add_column("tenants", sa.Column("tariff_plan", sa.String(20), default="simple"))
op.add_column("tenants", sa.Column("ai_quota_companies_monthly", sa.Integer(), default=0))
op.add_column("tenants", sa.Column("ai_quota_tokens_monthly", sa.Integer(), default=0))
op.add_column("tenants", sa.Column("ai_companies_processed_period", sa.Integer(), default=0))
op.add_column("tenants", sa.Column("ai_tokens_used_period", sa.Integer(), default=0))
op.add_column("tenants", sa.Column("quota_period_start", sa.DateTime()))

# ─── Я.Карты-сигналы в companies ───
op.add_column("companies", sa.Column("yandex_rating", sa.Float()))
op.add_column("companies", sa.Column("yandex_reviews_count", sa.Integer()))
op.add_column("companies", sa.Column("yandex_last_review_date", sa.Date()))
op.add_column("companies", sa.Column("yandex_hours_filled", sa.Boolean()))
op.add_column("companies", sa.Column("yandex_coordinates_filled", sa.Boolean()))
op.add_column("companies", sa.Column("yandex_url", sa.String(500)))
op.add_column("companies", sa.Column("yandex_operating_status", sa.String(30)))

# ─── Кросс-обогащение в companies ───
op.add_column("companies", sa.Column("cross_enriched_at", sa.DateTime()))
op.add_column("companies", sa.Column("cross_enrichment_source", sa.String(20)))
op.add_column("companies", sa.Column("cross_match_confidence", sa.Float()))

# ─── ИИ-поля в companies ───
op.add_column("companies", sa.Column("ai_score", sa.Integer()))
op.add_column("companies", sa.Column("ai_status", sa.String(20)))  # +"quota_exceeded"
op.add_column("companies", sa.Column("ai_comment", sa.Text()))
op.add_column("companies", sa.Column("ai_signals", sa.JSON()))
op.add_column("companies", sa.Column("ai_hook", sa.Text()))
op.add_column("companies", sa.Column("ai_keyword_matches_positive", sa.JSON()))
op.add_column("companies", sa.Column("ai_keyword_matches_negative", sa.JSON()))
op.add_column("companies", sa.Column("ai_tokens_used", sa.Integer(), default=0))
op.add_column("companies", sa.Column("ai_qualified_at", sa.DateTime()))
op.add_column("companies", sa.Column("ai_profile_id", sa.Integer(), sa.ForeignKey("ai_profiles.id")))
op.create_index("ix_company_ai_status", "companies", ["tenant_id", "ai_status"])
op.create_index("ix_company_yandex_url", "companies", ["yandex_url"])

# ─── Поля в parse_runs ───
op.add_column("parse_runs", sa.Column("ai_profile_id", sa.Integer(), sa.ForeignKey("ai_profiles.id")))
op.add_column("parse_runs", sa.Column("ai_qualify_enabled", sa.Boolean(), default=False))
op.add_column("parse_runs", sa.Column("ai_qualify_stats", sa.JSON()))
op.add_column("parse_runs", sa.Column("enable_cross_enrichment", sa.Boolean(), default=True))
```

---

## 15. API

### 15.1 Новые эндпоинты

| Метод | Эндпоинт | Назначение |
|-------|----------|-----------|
| `POST` | `/api/profiles/extract` | Stage A: бриф → профиль (без сохранения) |
| `POST` | `/api/profiles` | Сохранить профиль |
| `GET` | `/api/profiles` | Список профилей tenant'а |
| `GET` | `/api/profiles/{id}` | Один профиль |
| `PATCH` | `/api/profiles/{id}` | Правка |
| `DELETE` | `/api/profiles/{id}` | Soft delete |
| `POST` | `/api/runs/{id}/qualify` | Запустить квалификацию вручную |
| `GET` | `/api/tariff` | Текущий тариф + квоты + использование |

### 15.2 Изменения существующего

В `POST /api/runs` (запуск парсинга) — новые опциональные поля в body:

```json
{
  ...existing fields...,
  "ai_profile_id": 5,                       // null для Simple-тарифа
  "enable_cross_enrichment": true
}
```

Если `ai_profile_id` задан, а тариф `simple` → 403 Forbidden с понятным
сообщением «Подключите тариф AI».

### 15.3 GET /api/tariff response

```json
{
  "tariff_plan": "ai",
  "quotas": {
    "companies_monthly": 1000,
    "tokens_monthly": 10000000
  },
  "usage_period": {
    "started": "2026-05-15T00:00:00",
    "ends": "2026-06-14T00:00:00",
    "days_remaining": 29
  },
  "usage_current": {
    "companies_processed": 234,
    "tokens_used": 2340000,
    "companies_percent": 23,
    "tokens_percent": 23
  }
}
```

---

## 16. Mini App

### 16.1 Раздел «ИИ-профили» (только AI-тариф)

В bottom-nav пункт «ИИ-профили». Список профилей с превью.
Кнопка «+ Создать профиль» → двухшаговый UX (раздел 8.2).

### 16.2 Заголовок Mini App — плашка тарифа

См. раздел 6.5. Прогресс-бар, дата сброса, кнопка апгрейда для Simple.

### 16.3 Форма парсинга

Для **AI-тарифа** — выпадающий список:
```
ИИ-квалификация: [Не использовать ▼]
                 ├ Не использовать
                 ├ Сувениры — оптовики
                 └ + Создать новый профиль
```

Для **Simple-тарифа** — этого выпадающего списка просто нет.

Чекбокс «Кросс-обогащение источников» **показывается на обоих тарифах**,
по умолчанию включён. Хинт: «Дополнительно подтянет данные из второго
источника (Я.Карты или Rusprofile). Замедлит парсинг.»

### 16.4 Карточка запуска в истории

После завершения квалификации:

**AI-тариф:**
```
🔥 12 горячих | ❄ 23 холодных | ⏭ 5 пропущено | ❓ 7 неопределено
Профиль: Сувениры — оптовики
Токенов: 180k (~12 ₽)
Кросс-обогащение: 67 / 87 матчей
```

**Simple-тариф:**
```
Парсинг: 87 компаний
✅ 65 прошли фильтр | ⏭ 22 отсеяны
Сред. балл: 54 / 100
Кросс-обогащение: 67 / 87 матчей
```

---

## 17. Google Sheets — новые колонки

В конце текущих колонок:

### 17.1 Я.Карты-сигналы (на обоих тарифах)

| Колонка | Поле |
|---------|------|
| Я.Карты рейтинг | `yandex_rating` |
| Я.Карты отзывов | `yandex_reviews_count` |
| Я.Карты посл. отзыв | `yandex_last_review_date` |
| Я.Карты режим работы | `yandex_hours_filled` (✓ / —) |
| Я.Карты статус | `yandex_operating_status` |
| Я.Карты ссылка | `yandex_url` |

### 17.2 Кросс-обогащение (на обоих тарифах)

| Колонка | Поле |
|---------|------|
| Кросс-источник | `cross_enrichment_source` |
| Достоверность матча | `cross_match_confidence` (%) |

### 17.3 Универсальный скоринг (на обоих тарифах)

| Колонка | Поле |
|---------|------|
| Балл | `ai_score` (нормализованный 0–100) |
| Балл сырой | сумма / макс возможный |
| Звёздность | `★` = Rusprofile only, `★★` = оба источника, `☆` = Я.Карты only |

### 17.4 ИИ-квалификация (только AI-тариф)

Колонки те же что в v2:
- Статус ИИ, Комментарий, Сигналы, Зацепка для звонка,
  ✓ позитивные ключи, ✗ негативные ключи, Дата квалификации.

### 17.5 Сортировка и форматирование

- Сортировка: `ai_status='hot' first`, потом `ai_score desc`.
- `ai_status="hot"` → зелёная заливка.
- `ai_status="quota_exceeded"` → жёлтая заливка, в комментарии «Квота ИИ».
- `ai_status="skip"` → серая.

---

## 18. Логирование

### 18.1 Структурированный лог решений

Каждая компания, прошедшая квалификацию, генерирует JSON-запись в
`logs/qualify/qualify_{run_id}_{date}.jsonl`:

```json
{
  "company_id": 1234,
  "company_name": "ООО Канцторг",
  "source_original": "rusprofile",
  "tariff": "ai",
  "stages": {
    "stage_0a_early": {"hard_filter_passed": true, "kill_reasons": []},
    "stage_e_cross": {
      "direction": "rusprofile→yandex",
      "matched": true,
      "confidence": 1.0,
      "enriched_fields": ["yandex_rating", "yandex_reviews_count", "yandex_last_review_date"],
      "duration_ms": 4200
    },
    "stage_0b_full": {
      "score_raw": 73,
      "score_max": 100,
      "score_normalized": 73,
      "group_a": 22,
      "group_b": 25,
      "group_c": 26,
      "hard_filter_passed": true
    },
    "stage_1_website": { /* как в v2 */ },
    "stage_2_keywords": { /* как в v2 */ },
    "stage_3_llm": null,
    "final": {
      "ai_status": "hot",
      "ai_score": 73,
      "tokens_used": 0,
      "decision_path": "stage_2",
      "quota_check_passed": true
    }
  },
  "timestamp": "2026-05-20T14:32:18+03:00"
}
```

### 18.2 Сырые ответы LLM

В `logs/qualify/llm_raw_{run_id}.jsonl` — запрос + ответ для каждого
Stage A и Stage 3 вызова. На случай разборок «почему компания cold».

### 18.3 Метрики run

В `ParseRun.ai_qualify_stats` после завершения — структура из 13.2.

### 18.4 Алерты

В Telegram:
- При 80% квоты — предупреждение.
- При 100% квоты — блок + предложение апгрейда.
- При ошибках кросс-обогащения > 30% — «Не удалось матчить много компаний.
  Возможно, Я.Карты сменили вёрстку.»

---

## 19. Переменные окружения

Добавить в `.env.example`:

```env
# ─── Я.Карты — авторизация (для парсинга даты отзывов) ───
YANDEX_LOGIN=
YANDEX_PASSWORD=
YANDEX_SESSION_PATH=config/yandex_session.json

# ─── ИИ-квалификация ───
AI_PROVIDER=openai                    # openai | openrouter
OPENAI_API_KEY=
OPENROUTER_API_KEY=

AI_MODEL_EXTRACT=gpt-4o-mini
AI_MODEL_QUALIFY=gpt-4o-mini
AI_MAX_TOKENS_EXTRACT=1500
AI_MAX_TOKENS_QUALIFY=400
AI_TEMPERATURE=0.2

AI_PROMPT_EXTRACT_FILE=config/prompts/extract_profile.txt
AI_PROMPT_QUALIFY_FILE=config/prompts/qualify_company.txt

# ─── Stage 2 — пороги keyword-скоринга ───
KW_HOT_MIN=3
KW_COLD_MIN=2

# ─── Stage 1 — сайты ───
WEBSITE_TIMEOUT_S=15
WEBSITE_MAX_PAGES=3
WEBSITE_MAX_CHARS=12000

# ─── Stage E — кросс-обогащение ───
CROSS_ENRICH_CONCURRENCY=2
CROSS_ENRICH_TIMEOUT_S=30
CROSS_ENRICH_TTL_DAYS=30              # сколько дней действительны данные
CROSS_ENRICH_NAME_SIMILARITY_THRESHOLD=0.80

# ─── Параллелизм ───
QUALIFY_WEBSITE_CONCURRENCY=3
QUALIFY_LLM_CONCURRENCY=5
QUALIFY_MAX_COMPANIES_PER_RUN=300

# ─── Тарифы (defaults для новых тенантов) ───
DEFAULT_TARIFF_PLAN=simple
DEFAULT_AI_QUOTA_COMPANIES=1000
DEFAULT_AI_QUOTA_TOKENS=10000000
TARIFF_PERIOD_DAYS=30

# ─── Стоимость для расчёта ───
AI_COST_PER_1K_INPUT_RUB=0.015
AI_COST_PER_1K_OUTPUT_RUB=0.06
```

---

## 20. Файлы — создать и изменить

### Создать

| Файл | Назначение |
|------|-----------|
| `src/yandex_maps/reviews.py` | Парсер даты последнего отзыва (нужен для **парсера Я.Карт**, а не для quality) |
| `src/services/matching.py` | Алгоритмы матчинга компаний |
| `src/services/cross_enrichment_service.py` | Stage E |
| `src/services/quota_service.py` | Учёт и контроль квот |
| `src/services/qualify_service.py` | Оркестрация всего конвейера |
| `src/services/profile_service.py` | CRUD профилей |
| `src/ai/__init__.py` | пакет |
| `src/ai/scoring.py` | Stage 0a + 0b |
| `src/ai/profile_extractor.py` | Stage A |
| `src/ai/website_extractor.py` | Stage 1 |
| `src/ai/keyword_matcher.py` | Stage 2 |
| `src/ai/llm_client.py` | OpenAI/OpenRouter |
| `src/ai/qualifier.py` | Stage 3 |
| `src/api/profiles_router.py` | API профилей |
| `src/api/tariff_router.py` | API тарифа |
| `config/prompts/extract_profile.txt` | Промпт Stage A |
| `config/prompts/qualify_company.txt` | Промпт Stage 3 |
| `alembic/versions/XXXX_add_ai_qualification.py` | Миграция |
| `tests/test_scoring.py` | Тесты Stage 0 |
| `tests/test_matching.py` | Тесты алгоритмов матчинга |
| `tests/test_cross_enrichment.py` | Тесты Stage E (с моками) |
| `tests/test_quota_service.py` | Тесты квот |
| `tests/test_keyword_matcher.py` | Тесты Stage 2 |
| `tests/test_profile_extractor.py` | Тесты Stage A (с моками LLM) |
| `tests/test_qualify_service.py` | Интеграционные тесты конвейера |
| `tests/fixtures/sample_briefs.json` | Бриф-фикстуры |
| `tests/fixtures/sample_sites/` | HTML-фикстуры реальных сайтов |
| `tests/fixtures/yandex_cards/` | HTML-фикстуры Я.Карты карточек |

### Изменить

| Файл | Что изменить |
|------|-------------|
| `src/db/models.py` | Поля во всех моделях (см. раздел 14) |
| `src/yandex_maps/parser.py` | Подключить `reviews.py`, авторизацию, сбор `yandex_*` полей |
| `src/yandex_maps/auth.py` | **Новый** или существующий — Playwright-логин на Я.Картах |
| `src/services/parse_service.py` | Вызов `qualify_run` после парсинга при `ai_profile_id` или `enable_cross_enrichment` |
| `src/api/server.py` | Подключение `profiles_router`, `tariff_router`, эндпоинт `qualify` |
| `src/api/export_xlsx.py` | Новые колонки + сортировка |
| `src/api/sheets_pusher.py` | Новые колонки + форматирование |
| `src/webapp/index.html` | Плашка тарифа, раздел «ИИ-профили», чекбокс кросс-обогащения |
| `src/webapp/app.v2.js` | Логика тарифа, профилей, отображение метрик |
| `.env.example` | Все новые переменные |
| `requirements.txt` | Новые зависимости |

---

## 21. Зависимости

Добавить в `requirements.txt`:

```
httpx>=0.27
openai>=1.30
trafilatura>=1.8
regex>=2024.0
phonenumbers>=8.13     # E164 нормализация
rapidfuzz>=3.5         # схожесть строк для матчинга
```

---

## 22. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Я.Карты блокируют логин (капча, 2FA) | Storage state кеширует сессию; при провале — лог warning, конвейер работает без даты отзыва |
| Rusprofile вернёт несколько кандидатов при поиске по названию | Фильтр по региону + порог схожести имени 80% + лог warning |
| Cross-enrichment сильно замедляет парсинг | Чекбокс «отключить кросс-обогащение» в Mini App + параллелизм Semaphore(2) |
| LLM в Stage A извлёк не те ключевые слова | Двухшаговый UX — клиент видит и правит |
| LLM возвращает не-JSON | `response_format=json_object` + retry + regex-fallback |
| Сайт защищён Cloudflare | Playwright-fallback |
| Сайт-заглушка | Валидация: <300 символов → unknown |
| Случайный запуск на 5000 компаний | Хард-лимит 300 + квота тарифа |
| Сжигание бюджета | Месячная квота с блокировкой |
| "Эта компания должна быть hot, а у вас cold" | Полный structured-лог всех решений |
| Превышение квоты в середине run | `ai_status="quota_exceeded"`, компания остаётся в выгрузке с пометкой |

---

## 23. Критерии приёмки

### Парсер Я.Карт (раздел 12)
- [ ] Авторизация работает, storage_state сохраняется и переиспользуется.
- [ ] На 30+ карточках дата отзыва парсится для >70%.
- [ ] Все форматы дат корректно обрабатываются.
- [ ] При сбое — warning в лог, парсинг продолжается.

### Кросс-обогащение (раздел 5)
- [ ] Rusprofile → Я.Карты: на тестовой выборке 50 компаний с телефонами матчится ≥50%.
- [ ] Я.Карты → Rusprofile: на тестовой выборке 50 компаний матчится ≥30%.
- [ ] Поля `yandex_*` / Rusprofile-поля заполняются корректно.
- [ ] `cross_enriched_at` ставится, TTL 30 дней соблюдается.
- [ ] Можно отключить чекбоксом в Mini App.

### Скоринг (раздел 7)
- [ ] Stage 0a отсекает компании с liquidated/permanently_closed.
- [ ] Stage 0b считает три группы корректно, нормализация работает.
- [ ] Поздние хард-фильтры (мёртвая карточка >24 мес) срабатывают.
- [ ] Сортировка в Sheets — hot first, потом ai_score desc.

### Тарифы (раздел 6)
- [ ] Новый tenant создаётся с `tariff_plan="simple"`.
- [ ] При Simple — запрос с `ai_profile_id` отдаёт 403.
- [ ] При AI — квота инкрементится при каждом Stage 3 вызове.
- [ ] При 80% квоты — Telegram-уведомление.
- [ ] При 100% — компании получают `ai_status="quota_exceeded"`.
- [ ] Сброс счётчиков после 30 дней работает.
- [ ] `GET /api/tariff` возвращает текущее состояние.

### ИИ-конвейер (как в v2)
- [ ] Через Mini App создаётся профиль (двухшаговый UX).
- [ ] Кеширование по `brief_hash` работает.
- [ ] Stage 2 решает явные кейсы без LLM.
- [ ] Stage 3 даёт `status, comment, signals, hook`.
- [ ] Идемпотентность по дате квалификации.

### Общие
- [ ] Миграция применяется без ошибок.
- [ ] `pytest tests/` — все зелёные.
- [ ] Парсер Rusprofile и Я.Карт работают и без `enable_cross_enrichment`.
- [ ] Логи в `logs/qualify/*.jsonl` пишутся и читаемы.

---

## 24. Порядок реализации (план коммитов)

Реализация разбита на блоки. После каждого блока — коммит и smoke-test.

### Блок 1: Фундамент (1–2 дня)

1. **Миграция БД** — таблица `ai_profiles`, поля в `tenants`, `companies`, `parse_runs`.
2. **`src/services/matching.py`** — алгоритмы матчинга + тесты.
3. **`src/services/quota_service.py`** — учёт квот + тесты.
4. **`src/ai/scoring.py`** — Stage 0a и 0b + тесты (синхронные, без I/O).

### Блок 2: Доработка парсера Я.Карт (1–2 дня)

5. **`src/yandex_maps/auth.py`** — авторизация + storage_state.
6. **`src/yandex_maps/reviews.py`** — парсер даты + парсер форматов + тесты.
7. **Обновление `src/yandex_maps/parser.py`** — собирает новые поля `yandex_*`.

### Блок 3: Кросс-обогащение (1–2 дня)

8. **`src/services/cross_enrichment_service.py`** — Direction 1 + Direction 2 + тесты с моками.
9. **Интеграция в `qualify_service`** — вызывается между Stage 0a и Stage 0b.

### Блок 4: ИИ-конвейер (2–3 дня)

10. **`src/ai/llm_client.py`** — OpenAI/OpenRouter обёртка + моки.
11. **`src/ai/profile_extractor.py`** + промпт + `profile_service.py`.
12. **`src/api/profiles_router.py`** — API профилей.
13. **`src/ai/website_extractor.py`** — Stage 1.
14. **`src/ai/keyword_matcher.py`** — Stage 2.
15. **`src/ai/qualifier.py`** — Stage 3 + промпт.

### Блок 5: Оркестрация и API (1 день)

16. **`src/services/qualify_service.py`** — собираем весь конвейер.
17. **Тариф-aware логика** — проверки квот, ветвление Simple/AI.
18. **API эндпоинты** — `qualify`, `tariff`.
19. **Интеграция в `parse_service.py`** — автозапуск квалификации после парсинга.

### Блок 6: Sheets + Mini App (1–2 дня)

20. **Sheets** — новые колонки, сортировка, форматирование.
21. **Mini App** — плашка тарифа, раздел профилей, чекбоксы.
22. **Карточки запусков** — отображение метрик в истории.

### Блок 7: Документация и приёмочные тесты (0.5 дня)

23. **`docs/qualify_pipeline.md`** — короткий гид для клиента.
24. **Приёмочные тесты** на реальных запусках 30–50 компаний.

**Итого:** ~9–13 рабочих дней.

---

## 25. Стартовый промпт для новой сессии Claude Code

```
Привет. Работаем над проектом rusprofile-parser. Парсер Rusprofile сейчас
дорабатывает другой разработчик — его не трогаем. Я работаю над Этапом 2 —
универсальной ИИ-квалификацией с тарифной системой и кросс-обогащением
источников.

Полное ТЗ: docs/tz_ai_qualification_v3.md
Модели БД: src/db/models.py
Сервис парсинга: src/services/parse_service.py
Парсер Я.Карт: src/yandex_maps/ (буду дорабатывать в рамках задачи)
Mini App: src/webapp/

Архитектура — шестиуровневый конвейер:
  Stage A      — LLM-извлечение профиля из брифа (только AI-тариф)
  Stage 0a     — быстрые хард-фильтры
  Stage E      — кросс-обогащение источников (Я.Карты ↔ Rusprofile)
  Stage 0b     — универсальный сорт-скор (3 группы: контакты, ПС, активность)
  Stage 1      — извлечение текста сайта (только AI-тариф)
  Stage 2      — keyword-скоринг против профиля (только AI-тариф)
  Stage 3      — LLM-оценка спорных кейсов (только AI-тариф)

Два тарифа: Simple (без ИИ) и AI (с месячной квотой). Кросс-обогащение
работает на обоих.

Реализуем строго по плану из раздела 24 ТЗ. Начинаем с Блока 1 — миграция БД,
matching, quota_service, scoring. После каждого блока — коммит и smoke-test.

Код-стиль:
- БЫЛО/СТАЛО для всех правок
- Никаких неутверждённых изменений вне ТЗ
- Архитектурное согласование до реализации, если что-то неясно — спрашивай
- Русский неформальный регистр в комментариях и сообщениях
- Логирование щедрое — каждое решение в JSONL для трассируемости
```
