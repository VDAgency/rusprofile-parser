# ТЗ. База данных для дедупликации компаний и история запусков

> Документ — техническое задание на доработку парсера: появляется
> локальная БД с историей всех когда-либо найденных компаний;
> повторные запуски не дублируют данные; пользователь может вытащить
> прошлые результаты в Sheets или Excel без повторного парсинга.
>
> **Статус:** согласовано, в реализации.
> **Автор:** Claude (Opus 4.7).
> **Дата согласования:** 2026-05-06.

---

## 1. Цель

* Не парсить дважды одну и ту же компанию: лимит «100 компаний»
  должен означать «100 **новых** компаний, которых клиент ещё не видел».
* Хранить историю всех найденных компаний в локальной БД, привязанной
  к пользователю Telegram (multi-tenancy с самого начала — под SaaS).
* Дать пользователю возможность вернуться к любому прошлому запуску и
  получить его результаты в Sheets или скачать как Excel.

## 2. Решения по архитектуре (согласовано)

| Вопрос | Решение |
|--------|---------|
| СУБД сейчас | **SQLite** (`data/parser.db`). |
| СУБД позже | PostgreSQL — миграция через SQLAlchemy без правок схемы. |
| Multi-tenancy | **Сразу**, во всех таблицах. Tenant = Telegram user_id. |
| Дедуп — алгоритм | **Гибрид**: сначала ИНН, потом ОГРН; телефон — fallback **только если у обеих сторон ИНН и ОГРН пустые** (не склеит холдинги с общей приёмной). |
| Дедуп — область | **По теме** (наборы фильтров). Та же компания в новой теме считается новой. |
| Если все «уже было» | Записываем сколько нашли новых; остаток (дубликаты) виден только во вкладке «История». |
| Лимит парсинга в UI | Поле `max_new` в Mini App, **по умолчанию 100, максимум 300**. |
| Sheets-лист | Вариант A: основной лист `Результаты` перезаписывается каждым запуском; история — отдельный механизм (вкладка «История»). |
| Excel-экспорт | Делаем сразу. Бот шлёт пользователю `.xlsx` файлом. |
| Mini App навигация | Bottom-nav: «Парсинг» / «История». Профиль — позже. |
| Телефон в БД | Нормализованный (только цифры, ведущая `8`→`7`) для дедупа + оригинальный для вывода. В Sheets/Excel выводим в формате `+7 (xxx) xxx-xx-xx`. |
| Sheets для разных пользователей | Сейчас глобальный `GOOGLE_SHEET_ID` из `.env`. Tenant имеет поле `google_sheet_id NULL` — когда выйдем на SaaS, каждый пользователь подставит свой. |

## 3. Схема БД (SQLAlchemy 2.0)

```
tenants
  id                    PK
  telegram_user_id      UNIQUE NOT NULL
  username              текстовый @username (для удобства, может меняться)
  google_sheet_id       NULL — на будущее под SaaS
  created_at

themes
  id                    PK
  tenant_id             FK tenants
  source                'rusprofile' | 'yandex_maps'
  filters_hash          SHA-256 канонизированных фильтров (для уникальности)
  filters_json          оригинальные фильтры (для отображения и re-export)
  title                 человекочитаемое имя ("Канцтовары, Москва")
  created_at
  last_used_at
  UNIQUE (tenant_id, filters_hash)
  INDEX (tenant_id, last_used_at DESC)

companies
  id                    PK
  tenant_id             FK tenants
  inn                   индекс
  ogrn                  индекс
  phone_normalized      индекс (только цифры)
  name, region, address, okved, revenue, profit
  phone                 оригинал «+7 (xxx) xxx-xx-xx»
  email, site, status
  source                'rusprofile' | 'yandex_maps'
  raw_json              JSON оригинала (на случай добавления полей)
  first_seen_at, last_seen_at
  INDEX (tenant_id, inn) WHERE inn IS NOT NULL
  INDEX (tenant_id, ogrn) WHERE ogrn IS NOT NULL
  INDEX (tenant_id, phone_normalized) WHERE phone_normalized IS NOT NULL

parse_runs
  id                    PK
  tenant_id             FK tenants
  theme_id              FK themes
  source                для удобства фильтрации
  started_at, finished_at
  status                'running' | 'done' | 'error' | 'cancelled'
  error_message         NULL
  requested_new         сколько просили
  total_found_in_source общее число в источнике (если знаем)
  total_new             сколько записали как новых
  total_skipped         сколько пропустили дубликатов
  sheet_url             ссылка на Sheets после успеха

run_companies
  run_id                FK parse_runs
  company_id            FK companies
  is_new                BOOLEAN — впервые ли в этом запуске
  PK (run_id, company_id)
  INDEX (company_id)
```

## 4. Алгоритм дедупа (гибридный)

```python
def is_duplicate(company, theme_known_keys):
    """theme_known_keys = (set_inn, set_ogrn, set_phone) — собрано из
    companies, связанных с темой через run_companies.
    """
    set_inn, set_ogrn, set_phone = theme_known_keys
    if company.inn and company.inn in set_inn:
        return True
    if company.ogrn and company.ogrn in set_ogrn:
        return True
    if not company.inn and not company.ogrn:
        ph = normalize_phone(company.phone)
        if ph and ph in set_phone:
            return True
    return False
```

Нормализация телефона:
```
+7 (495) 123-45-67  →  74951234567
8 (495) 123-45-67   →  74951234567
89991234567         →  79991234567
```

Загружаем `theme_known_keys` один раз в начале запуска (один SQL),
все проверки в Python set — O(1).

## 5. Идентификация темы (автоматическая)

`filters_hash = sha256(json(canonicalize(filters)))`, где
`canonicalize` сортирует списки кодов, отбрасывает None/пустые
строки, нижний регистр для строк-фильтров. Перестановка кодов
ОКВЭД даёт **тот же** хеш — клиент не должен думать «о, я в этот раз
не в том порядке выбрал».

`title` — автоматический human-readable:
* «Москва, ОКВЭД: 73.11, 73.20» (Rusprofile)
* «Москва: рекламные агентства» (Яндекс.Карты)

## 6. Логика парсинга (псевдокод)

```python
async def run_parse(filters, max_new=100, source="rusprofile"):
    tenant = ensure_tenant(user_id)
    theme = ensure_theme(tenant, source, filters)
    run = ParseRun.create(tenant, theme, requested_new=max_new, status="running")

    known = load_keys_for_theme(tenant, theme)
    new_companies = []

    async for raw in source_parser.iter(filters):
        company = build_company(raw)
        if is_duplicate(company, known):
            run.total_skipped += 1
            continue

        # Новая для этой темы
        existing = find_or_create_company(tenant, company)  # глобальный upsert по реквизитам
        link = RunCompany(run, existing, is_new=True)
        new_companies.append(existing)
        update_known(known, company)
        run.total_new += 1

        if len(new_companies) >= max_new:
            break

    run.status = "done"
    run.finished_at = now()
    run.sheet_url = write_to_sheets(new_companies, replace=True)
    return run
```

Защита от бесконечного хода: парсер ограничен страничным лимитом
(внутренний `MAX_PAGES`), при достижении — стоп с пометкой в
`status` и сообщением «достигнут предел источника, найдено X новых».

## 7. Mini App: что меняется

### 7.1 Bottom-nav (новое)

Постоянный нижний переключатель: **Парсинг** | **История**.
В будущем — **Профиль** (под SaaS).

### 7.2 Вкладка «Парсинг»

Прежний UI + новое поле:
* «Сколько новых компаний найти» — `<input type="number">`,
  default `100`, min `1`, max `300`. Подсказка:
  «Уже найденные ранее компании пропускаются и не считаются».

Прогресс-блок показывает:
> Парсинг… найдено 23 новых, пропущено 47 дубликатов

После завершения:
> Готово. Записано 100 новых компаний (пропущено 142 дубликата).
> Ссылка: …
> История: открыть вкладку «История».

### 7.3 Вкладка «История»

Список запусков пользователя (от свежих к старым), карточка
содержит:
* Дату/время.
* Источник (Rusprofile / Яндекс.Карты).
* Заголовок темы (автогенерим).
* Числа: новых N, пропущено M.
* Кнопки:
  * **«Открыть таблицу»** — ссылка на Sheets конкретного запуска.
  * **«Перезалить в Sheets»** — заново записать компании этого запуска
    в основной лист `Результаты`.
  * **«Скачать Excel»** — бот пришлёт `.xlsx` файлом.

## 8. Backend HTTP API (новое)

Mini App с `tg.sendData()` односторонний (только в бот), но для
подгрузки списка истории и скачивания Excel нужен HTTP. Поднимаем
**aiohttp web-server** в том же процессе, что и aiogram-бот
(aiogram уже использует aiohttp под капотом).

Эндпойнты (все валидируют Telegram WebApp **initData** через HMAC от
секрета бота):

| Метод | Путь | Что делает |
|-------|------|-----------|
| GET   | `/api/history?limit=50` | Список запусков пользователя. JSON. |
| POST  | `/api/runs/{id}/repush` | Перезалить компании запуска в Sheets. |
| GET   | `/api/runs/{id}/xlsx`   | Скачать Excel. |

initData приходит из Telegram при открытии Mini App, фронт
прокидывает его в каждый запрос (`X-Telegram-Init-Data`). Сервер
проверяет HMAC и достаёт `user_id`.

Nginx добавит `location /api/ { proxy_pass http://127.0.0.1:8080; }`.

## 9. Excel-экспорт

`openpyxl`. Колонки повторяют `SHEET_HEADERS` из config.py
(плюс «Дата первого нахождения» и «Кол-во запусков, в которых
была»). Имя файла:
`run_{run_id}_{source}_{date}.xlsx`. Формат телефона —
`+7 (xxx) xxx-xx-xx` (преобразование из normalized).

Бот отдаёт файл через `bot.send_document` в чат пользователя.

## 10. Миграции и развёртывание

* SQLAlchemy 2.0 + Alembic.
* `alembic init alembic`, скрипт `alembic.ini` в корне.
* Миграции в `alembic/versions/`.
* Обновление: `alembic upgrade head` после `git pull`.
* В `deploy.md` дописывается команда.

## 11. Этапы реализации

1. **БД и миграции** (~1 день) — SQLAlchemy, Alembic, модели,
   нормализация телефона, сервис-функции для дедупа, юнит-тесты.
2. **Интеграция в парсеры** (~1 день) — Rusprofile и Яндекс.Карты:
   создание ParseRun, дедуп при парсинге, запись в БД, метрики.
3. **Mini App: max_new + прогресс** (~0.5 дня) — поле в форме,
   проброс на бэкенд, новый формат статуса.
4. **Вкладка «История» + API + Excel** (~1.5 дня) — bottom-nav,
   aiohttp endpoints, Excel-экспорт через openpyxl.
5. **Документация и деплой** (~0.5 дня) — README, deploy.md,
   `alembic upgrade head`, nginx `/api/`, smoke-тесты.

Итого ~4–5 рабочих дней.

## 12. Критерии приёмки

1. После двух последовательных запусков по одинаковым фильтрам:
   во втором `total_new ≤ 0` (если за это время Rusprofile не
   обновил выдачу) и Sheets не показывает дубликаты.
2. Поле «Сколько новых компаний найти» уважает лимит 1–300.
3. Вкладка «История» показывает список, кнопка «Скачать Excel»
   возвращает файл с правильными данными.
4. Парсер Яндекс.Карт по-прежнему работает (smoke-тест).
5. На свежей БД (после `alembic upgrade head`) бот не падает; первый
   парсинг создаёт `tenant`, `theme`, `parse_run`.
6. SQLite-файл переносится между средами без правок (один файл),
   `pg_dump`-совместимости в схеме нет (SAVEPOINT/UPSERT —
   стандартные SQL, поддерживаются и SQLite, и Postgres).

## 13. Открытые вопросы и точки расширения (на будущее)

* Ручное именование тем («моя сувенирка Q2») — пока авто.
* «Забыть компанию» — кнопка удаления в истории, чтобы повторно
  спарсить (не реализуем сейчас).
* TTL — забывать компании старше 12 месяцев. Не реализуем,
  обсудим, когда БД распухнет.
* Авторизация Sheets через OAuth (вместо общей service-account
  таблицы) — отдельный SaaS-этап.
