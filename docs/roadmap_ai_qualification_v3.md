# Roadmap. Этап 2 — ИИ-квалификация компаний (v3)

> Дорожная карта реализации [tz_ai_qualification_v3.md](./tz_ai_qualification_v3.md).
> Прогресс отмечается чек-боксами. После каждого блока — commit + push.

**Старт:** 2026-05-15
**Источник истины:** `docs/tz_ai_qualification_v3.md` (раздел 24, План реализации).

---

## Стек технологий (что добавляем сверх существующего)

| Назначение | Библиотека / Инструмент |
|------------|------------------------|
| LLM | `openai>=2.30` (уже есть; используем для OpenAI и OpenRouter через `base_url`) |
| HTTP-клиент для сайтов | `httpx>=0.27` (новая) |
| Извлечение текста с сайтов | `trafilatura>=1.8` (новая) |
| Юникодные `\b` для русского | `regex>=2024.0` (новая) |
| Нормализация телефонов в E.164 | `phonenumbers>=8.13` (новая) |
| Схожесть строк (матчинг компаний) | `rapidfuzz>=3.5` (новая) |
| ORM / миграции | `sqlalchemy==2.0.36` + `alembic==1.14.0` (уже есть) |
| Браузерный fallback | `playwright==1.58.0` (уже есть) |

---

## Блок 1. Фундамент

- [x] **1.1** Создать `docs/roadmap_ai_qualification_v3.md` (этот файл).
- [x] **1.2** Alembic-миграция `add_ai_qualification`:
  - таблица `ai_profiles` (бриф + извлечённые поля + кеш по `brief_hash`)
  - поля тарифа в `tenants` (`tariff_plan`, квоты, счётчики, `quota_period_start`)
  - Я.Карты-сигналы в `companies` (`yandex_rating`, `yandex_reviews_count`, `yandex_last_review_date`, …)
  - кросс-обогащение в `companies` (`cross_enriched_at`, `cross_enrichment_source`, `cross_match_confidence`)
  - ИИ-поля в `companies` (`ai_score`, `ai_status`, `ai_comment`, `ai_signals`, `ai_hook`, `ai_keyword_matches_*`, `ai_tokens_used`, `ai_qualified_at`, `ai_profile_id`)
  - поля в `parse_runs` (`ai_profile_id`, `ai_qualify_enabled`, `ai_qualify_stats`, `enable_cross_enrichment`)
  - индексы `ix_company_ai_status`, `ix_company_yandex_url`, `ix_ai_profiles_tenant_active`
- [x] **1.3** Обновить SQLAlchemy-модели в `src/db/models.py` (новая модель `AIProfile`, поля во всех остальных).
- [x] **1.4** `src/services/matching.py` + `tests/test_matching.py`: `normalize_company_name`, `name_similarity`, `phone_to_e164`, `regions_match`.
- [x] **1.5** `src/services/quota_service.py` + `tests/test_quota_service.py`: `check_can_use_ai`, `increment_companies/tokens`, `reset_if_period_expired`, `get_usage`.
- [x] **1.6** `src/ai/scoring.py` + `tests/test_scoring.py`: `score_company_early` (Stage 0a), `score_company_full` (Stage 0b, 3 группы A/B/C, нормализация по доступным источникам).
- [x] **1.7** `pytest` всё зелёное → commit `feat(ai): фундамент конвейера квалификации (миграция, matching, quota, scoring)` → push.

## Блок 2. Доработка парсера Я.Карт

- [ ] **2.1** `src/yandex_maps/auth.py`: Playwright-логин, сохранение `storage_state` в `config/yandex_session.json`, переиспользование сессии.
- [ ] **2.2** `src/yandex_maps/reviews.py`: `get_last_review_date(yandex_url, page)` + `parse_relative_date(text, today)` (форматы: «сегодня», «N дней назад», «23 апреля», «23 апреля 2024 г.»).
- [ ] **2.3** Доработать `src/yandex_maps/parser.py` (или scraper.py): собирать `yandex_rating`, `yandex_reviews_count`, `yandex_last_review_date`, `yandex_hours_filled`, `yandex_coordinates_filled`, `yandex_url`, `yandex_operating_status`. Не ронять парсинг при сбое одной карточки.
- [ ] **2.4** `tests/test_yandex_reviews.py` (форматы дат, синхронные парсеры) → commit `feat(yandex): дата последнего отзыва + расширенные сигналы карточки` → push.

## Блок 3. Кросс-обогащение источников

- [ ] **3.1** `src/services/cross_enrichment_service.py`:
  - `enrich_from_yandex(company)` — Direction 1 (Rusprofile → Я.Карты по телефону E.164)
  - `enrich_from_rusprofile(company)` — Direction 2 (Я.Карты → Rusprofile по «название + регион»)
  - идемпотентность через `cross_enriched_at` (TTL 30 дней)
  - `asyncio.Semaphore(2)`, таймаут 30 сек/направление
- [ ] **3.2** `tests/test_cross_enrichment.py` с моками парсеров.
- [ ] **3.3** Тесты зелёные → commit `feat(cross): обогащение источников Я.Карты ↔ Rusprofile` → push.

## Блок 4. ИИ-конвейер (Stage A, 1, 2, 3)

- [ ] **4.1** `src/ai/__init__.py` + `src/ai/llm_client.py`: тонкая обёртка `AsyncLLMClient` над `openai.AsyncOpenAI`, поддержка `AI_PROVIDER=openai|openrouter` через `base_url`, retry с exponential backoff, учёт токенов.
- [ ] **4.2** `src/ai/profile_extractor.py` + `config/prompts/extract_profile.txt` + `src/services/profile_service.py` (CRUD + кеш по `brief_hash`).
- [ ] **4.3** `src/api/profiles_router.py`: `POST /api/profiles/extract`, CRUD профилей.
- [ ] **4.4** `src/ai/website_extractor.py`: `httpx` + `trafilatura` + Playwright fallback при 403/429, валидация (`<300` симв. → unknown, заглушка → cold).
- [ ] **4.5** `src/ai/keyword_matcher.py`: совпадения через `regex.\b{kw}\b`, нормализация (lowercase, ё→е), решение hot/cold/needs_llm.
- [ ] **4.6** `src/ai/qualifier.py` + `config/prompts/qualify_company.txt`: Stage 3, `response_format=json_object`, парсинг ответа `{status, comment, signals, hook}`.
- [ ] **4.7** `tests/` для каждого модуля (`test_profile_extractor`, `test_keyword_matcher`, `test_website_extractor`, `test_qualifier`) с моками LLM/httpx → commit `feat(ai): конвейер Stage A/1/2/3` → push.

## Блок 5. Оркестрация и API

- [ ] **5.1** `src/services/qualify_service.py` — `qualify_run(run_id, tenant_id, profile_id, enable_cross_enrichment, force)`, тариф-aware ветвление (Simple/AI), хард-лимит 300, `Semaphore`-ы по стадиям, структурированный JSONL-лог.
- [ ] **5.2** `src/api/tariff_router.py` (`GET /api/tariff`) + `POST /api/runs/{id}/qualify`.
- [ ] **5.3** Интеграция в `src/services/parse_service.py`: после `run_rusprofile` / `run_yandex` запуск `qualify_run` если в payload `ai_profile_id` или `enable_cross_enrichment=True`.
- [ ] **5.4** `tests/test_qualify_service.py` (интеграционный, end-to-end с моками) → commit `feat(ai): qualify_service + API эндпоинты квалификации/тарифа` → push.

## Блок 6. Sheets + Mini App

- [ ] **6.1** Sheets — новые колонки (Я.Карты-сигналы, кросс-обогащение, универсальный скоринг, ИИ-блок), сортировка `hot first → ai_score desc`, цветовое форматирование (`hot` → зелёный, `quota_exceeded` → жёлтый, `skip` → серый). Изменить `src/api/export_xlsx.py` и Sheets-pusher.
- [ ] **6.2** Mini App: плашка тарифа в шапке, раздел «ИИ-профили» (двухшаговый UX), выпадающий список профилей в форме парсинга (для AI-тарифа), чекбокс «Кросс-обогащение источников» (для всех).
- [ ] **6.3** Карточки запусков в истории — отображение метрик квалификации (для AI и Simple отдельно), кнопка «Запустить квалификацию».
- [ ] **6.4** Smoke-test через бот → commit `feat(ui): тариф, профили, квалификация в Mini App + Sheets-форматирование` → push.

## Блок 7. Документация и приёмка

- [ ] **7.1** `docs/qualify_pipeline.md` — короткий гид для клиента: что включает каждый тариф, как создать профиль, как читать колонки в Sheets.
- [ ] **7.2** Запуск всех тестов, финальный smoke-test, commit `docs(ai): гид клиента по квалификации` → push.

---

## Предположения и заглушки

Если в процессе встретятся внешние зависимости/секреты, которых сейчас нет — **ставим заглушки** и фиксируем здесь:

- **OpenAI/OpenRouter API-ключ** — заглушка через `OPENAI_API_KEY` из `.env`. Если ключа нет — Stage A/3 при вызове отдадут понятную ошибку (`ai_status="unknown"`).
- **Yandex логин/пароль** — заглушка через `YANDEX_LOGIN/YANDEX_PASSWORD`. При пустых значениях `auth.py` пропускает логин, `reviews.py` парсит относительные даты как умеет.
- **trafilatura** — если не установится корректно под Windows, фолбэк на `BeautifulSoup`-извлечение текста.
- **rapidfuzz/phonenumbers** — стандартные пакеты, должны установиться.
- **Промпты** — пишем дефолтные, клиент потом отредактирует.

---

## Правила выполнения

1. Каждый блок завершается прогоном `pytest tests/` (только тесты соответствующих модулей, не интеграционные с реальными API).
2. После зелёных тестов — commit с понятным сообщением + push в `main`.
3. Никаких неутверждённых изменений вне ТЗ.
4. Логирование щедрое — каждое решение в `logs/qualify/*.jsonl`.
5. Заглушки помечаются `# TODO: заглушка — заменить когда будет <X>` в коде и фиксируются в этом файле.
