# Roadmap. Этап 3 — Личный кабинет, тарифы и биллинг

> Дорожная карта реализации [tz_billing_tariffs.md](./tz_billing_tariffs.md).
> Прогресс отмечается чек-боксами. После каждого блока — commit + push.

**Старт:** 2026-05-20
**Источник истины:** `docs/tz_billing_tariffs.md` (раздел 14, План реализации).

---

## Стек технологий (что добавляем сверх существующего)

| Назначение | Библиотека / Инструмент |
|------------|------------------------|
| Платежи | `yookassa>=3.4` (новая) |
| Планировщик | `apscheduler==3.11.2` (уже есть, добавим billing-jobs) |
| ORM / миграции | `sqlalchemy==2.0.36` + `alembic==1.14.0` (уже есть) |

---

## Блок 1. Фундамент: БД и helper-ы

- [x] **1.1** Alembic-миграция `add_billing`:
  - Поля в `tenants`: `trial_started_at`, `trial_expires_at`,
    `trial_parses_left`, `parses_used_period`, `active_subscription_id`,
    `is_blocked`, `blocked_reason`.
  - Таблица `subscriptions` (tenant_id, tariff_plan, status, dates,
    yookassa_payment_method_id, price_rub, auto_renew).
  - Таблица `payments` (yookassa_payment_id, status, amount, metadata,
    error_*).
  - Индексы: `ix_subscriptions_tenant_active`, `ix_subscriptions_expires`,
    `ix_payments_tenant`.
- [x] **1.2** Обновить SQLAlchemy-модели: `Tenant` (расширение),
  `Subscription`, `Payment`, ENUM-ы `TariffPlan` (+TRIAL/TRIAL_EXPIRED/
  BASIC/PRO), `SubscriptionStatus`, `PaymentStatus`, `BlockedReason`.
- [x] **1.3** `src/services/tariff_helpers.py`: `is_ai_available`,
  `is_parsing_available`, `is_paid_plan`, `get_trial_snapshot` +
  тесты `test_tariff_helpers.py` (22 шт.).
- [x] **1.4** Обновить `src/db/dedup.py::ensure_tenant` — новый tenant
  создаётся с `tariff_plan='trial'`, `trial_started_at=now`,
  `trial_expires_at=now+TRIAL_DAYS`, `trial_parses_left=TRIAL_PARSES_LIMIT`.
  Существующие tenant'ы не трогаются. Тесты `test_ensure_tenant_trial.py`.
- [x] **1.5** `pytest tests/` → 282 passed → commit `feat(billing): фундамент — миграция, модели, tariff_helpers, Trial по умолчанию для новых tenant'ов` → push.

## Блок 2. Заглушка оплаты (без ЮKassa — эквайринг ещё не оформлен)

> **⚠ ИЗМЕНЕНИЕ ПЛАНА (2026-05-20):** клиент пока не оформил эквайринг,
> поэтому полная интеграция с ЮKassa отложена. Сейчас ставим **stub**:
> кнопка «Оплатить» открывает страницу-заглушку с текстом «Сервис
> платежей ещё в разработке». Активация платных тарифов после
> получения эквайринга — через ручной helper / SQL.
> Возврат к полному Блоку 2 запланирован отдельным итемом «После
> эквайринга: yookassa_client + real payment_service».

- [x] **2.1** `src/webapp/payment_stub.html` — статичная страница-заглушка
  с текстом, ссылкой «Связаться с поддержкой» и кнопкой «Назад в бот».
  Поддерживает темизацию через Telegram theme params.
- [x] **2.2** `src/services/payment_service.py` (stub-версия):
  `create_stub_payment_url(tariff, tenant_id)` + `is_billing_configured`.
  Запись в БД не создаём (вернёмся, когда подключим ЮKassa).
- [x] **2.3** Тесты `test_payment_service_stub.py` (13 шт.): URL
  формируется корректно, нормализация регистра, валидация payable
  тарифов → commit `feat(billing): stub-страница оплаты до подключения
  эквайринга` → push.

## Блок 3. subscription_service

- [x] **3.1** `src/services/subscription_service.py`:
  - `get_active_subscription` (игнорит expired/past_due)
  - `list_tenant_subscriptions` (история для UI)
  - `activate_subscription_manually(tariff, months, notes,
    yookassa_payment_method_id)` — единая точка активации, та же
    будет вызвана из payment-webhook после подключения эквайринга
  - `cancel_subscription` — auto_renew=False, доступ до expires_at
  - `mark_past_due` — для scheduler после неудачного списания
  - `block_after_grace` — grace истёк → EXPIRED + блок tenant
  - `expire_subscription` — естественное истечение → TRIAL_EXPIRED + блок
  - `find_expiring_soon` / `find_past_due_overdue` — для scheduler
  - `renew_subscription_stub` — заглушка автопродления (всегда False
    до подключения ЮKassa)
- [x] **3.2** Lifecycle-тесты `test_subscription_service.py` (20 шт.):
  активация / продление / смена тарифа / отмена / past_due / grace /
  expire / find_*.
- [x] **3.3** `pytest tests/` → 315 passed → commit `feat(billing):
  subscription_service с lifecycle и ручной активацией` → push.

## Блок 4. Scheduler + notifications

- [x] **4.1** `src/services/notifications.py`: 9 типов уведомлений
  (`NotificationKind`-enum + `NOTIFICATION_TEMPLATES`), безопасная
  отправка `send_notification(bot, uid, kind, **context)` через
  try/except (бот заблокирован клиентом — не падаем). Шаблоны включают
  HTML-форматирование `<b>...</b>`. Шаблоны хранятся прямо в модуле
  (а не в отдельном файле — для простоты).
- [x] **4.2** `src/services/billing_scheduler.py`: APScheduler-jobs
  - `check_trial_expirations` (каждые 30 мин)
  - `check_subscription_expirations` (каждый час)
  - `try_recurrent_renewals` (каждые 4 часа, использует
    `renew_subscription_stub` пока — всегда past_due)
  - `process_past_due` (каждый час)
  - `send_trial_reminders` (раз в день в 07:00 UTC = 10:00 МСК)
- [x] **4.3** Регистрация в `src/main.py`: `AsyncIOScheduler(timezone="UTC")`,
  `register_billing_jobs(scheduler, bot)`, `scheduler.start()`. Корректное
  завершение через `scheduler.shutdown(wait=False)` в `finally`.
- [x] **4.4** Тесты `test_notifications.py` (10 шт.) +
  `test_billing_scheduler.py` (11 шт.). Изоляция scheduler-job через
  in-memory SQLite + commit в фикстурах (job открывает свою сессию
  через `get_session()`).
- [x] **4.5** `pytest tests/` → 336 passed → commit `feat(billing):
  scheduler-jobs для trial/подписок + унифицированные уведомления` → push.

## Блок 5. API + интеграция в parse

- [x] **5.1** Расширен `src/api/tariff_router.py::get_tariff`:
  поля `trial` (days_left/parses_left/expires_at), `is_blocked`,
  `blocked_reason`, `parsing_blocked_message`, `is_ai_available`,
  `is_parsing_available`, `subscription` (active sub из БД),
  `parses_used_period`. Tenant'а ещё нет в БД → нейтральный
  «новичок-trial» (без ошибок).
- [x] **5.2** `src/api/billing_router.py`:
  - `GET /api/billing/plans` — список тарифов и цен + `billing_configured`
  - `POST /api/billing/create-payment` — **stub-режим**: отдаёт URL
    payment_stub.html
  - `GET /api/billing/payment-status/{id}` — всегда 404 в stub
  - `POST /api/billing/cancel-subscription` — реальная отмена
    (`auto_renew=False`), работает с активацией от CLI
  - `GET /api/billing/payments` — реальная история (пустая в stub)
  - `GET /api/billing/subscription` — реальная подписка + история
  - `POST /api/billing/yookassa-webhook` — **отложено** (вернёмся
    с реальной интеграцией)
- [x] **5.3** `src/bot/handlers.py::handle_webapp_data`: перед запуском
  парсинга вызывает `is_parsing_available(tenant)` → если False,
  отвечает понятным сообщением (`availability.message`) и не стартует.
  Tenant создаётся через `ensure_tenant` (Trial для новых).
- [x] **5.4** `src/services/parse_service.py`:
  - Helper `_decrement_parses_counter(tenant)`: -1 от
    `trial_parses_left` (для Trial, не уходит в минус),
    +1 к `parses_used_period` (для всех).
  - Helper `_compensate_parses_counter(tenant_id)`: возвращает парсинг
    при ошибке через свою сессию.
  - Вызов decrement в `run_rusprofile` и `run_yandex` сразу после
    `session.flush()` создания ParseRun.
  - Вызов compensate в обоих early-error branches (`error_message and
    not persisted`).
- [x] **5.5** Тесты:
  - `test_parse_billing_integration.py` (9 шт.): decrement для Trial/
    Basic/Pro/SIMPLE, защита от минуса, компенсация, безопасность
    при unknown tenant_id.
  - `test_billing_router.py` (12 шт.): plans, create-payment в stub,
    отказ для не-payable, cancel-subscription с активной/без, история.
- [x] **5.6** Все 357 тестов зелёные → commit `feat(billing): API
  эндпоинты + блокировка парсинга + декремент Trial + компенсация` → push.

## Блок 6. Mini App — кабинет и оплата (stub)

- [x] **6.1** `src/webapp/index.html`:
  - Новая страница `page-cabinet` (4 секции: тариф / тарифы для оплаты /
    история подписок / статус загрузки).
  - Кнопка в bottom-nav `data-page="cabinet"` с иконкой 👤 — видна всем.
  - Banner блокировки на странице «Парсинг» (`#parseBlockedBanner`)
    с кнопкой «Открыть кабинет».
- [x] **6.2** `src/webapp/app.v2.js`:
  - `loadCabinet()` — параллельно `/api/tariff` + `/api/billing/plans`
    + `/api/billing/subscription`.
  - `renderCabinetTariff(t, sub)` — отображение для Trial/active/blocked/
    legacy с usage-метрикой (парсинги + ИИ-квота).
  - `renderCabinetPlans(plansData, currentTariff)` — список Basic/Pro
    с кнопками «Оплатить» и пометкой текущего тарифа.
  - `renderCabinetHistory(sub)` — таблица всех подписок.
  - `payTariff(tariff)` — `POST /api/billing/create-payment` →
    `tg.openLink(confirmation_url)` (stub-страница).
  - `cancelSubscriptionFlow()` — confirm + `POST /api/billing/cancel-subscription`.
  - `updateParseBlockedBanner(t)` — баннер на странице «Парсинг».
  - В DOMContentLoaded — отдельный лёгкий `GET /api/tariff` для
    инициализации баннера (без открытия кабинета).
  - `switchToPage(target)` — программное переключение (для кнопки
    «Открыть кабинет» в баннере).
- [x] **6.3** `src/webapp/style.v2.css`: `banner-blocked`, `cabinet-card`,
  `cabinet-meter`, `cabinet-usage`, `cabinet-bar`, `cabinet-plan`
  (+ `.current`), `cabinet-history-row`. Темизация через
  Telegram theme params.
- [x] **6.4** Polling статуса платежа НЕ реализован (в stub-режиме нет
  чего polling'ить, payment-status всегда 404). Вернёмся в отложенном
  блоке после ЮKassa.
- [x] **6.5** Smoke имportов backend (билинговые роутеры) → commit
  `feat(ui): личный кабинет с тарифами + stub-кнопка оплаты` → push.

## Блок 7. Ручная активация подписок + документация (вместо ЮKassa smoke)

> Полный smoke с ЮKassa отложен до получения эквайринга у клиента
> (см. отдельный итем «После эквайринга»). Сейчас — минимум, чтобы
> можно было ВРУЧНУЮ активировать подписку платежом «вне системы».

- [x] **7.1** CLI-скрипт `scripts/activate_subscription.py`:
  активирует Basic/Pro подписку для указанного `telegram_user_id`
  с заданным сроком. Опции `--uid`, `--tariff`, `--months`, `--notes`,
  `--username`, `--notify`. Использует
  `subscription_service.activate_subscription_manually` (тот же
  метод, что будет вызван из ЮKassa-webhook после подключения).
- [x] **7.2** `docs/billing_manual_activation.md` — гид для админа:
  как узнать `telegram_user_id`, как активировать, как проверить
  состояние БД, как отменить, что когда подключим ЮKassa.
- [x] **7.3** Smoke-тест на проде — после деплоя.
- [x] **7.4** Финальный commit `docs(billing): CLI ручной активации +
  гид админа` → push.

## Отложено: возврат к полной интеграции с ЮKassa

> Когда клиент оформит эквайринг и пришлёт `YOOKASSA_SHOP_ID` +
> `YOOKASSA_SECRET_KEY` — возвращаемся к этому списку:

- [ ] **R.1** `src/services/yookassa_client.py`: AsyncYooKassaClient,
  `create_payment`, `charge_recurrent`, `parse_webhook`.
- [ ] **R.2** Дописать `src/services/payment_service.py`: убрать stub,
  добавить `create_real_payment_for_tariff`, `process_webhook_*`,
  `process_recurrent_charge`. Записывать в таблицу `payments`.
- [ ] **R.3** Чеки 54-ФЗ.
- [ ] **R.4** Тесты с моками HTTP-вызовов SDK.
- [ ] **R.5** Заменить в API `create_stub_payment_url` на реальный
  `create_real_payment_for_tariff`.
- [ ] **R.6** Заменить кнопку «Оплатить (в разработке)» в Mini App на
  обычную кнопку оплаты.
- [ ] **R.7** Зарегистрировать webhook URL в ЮKassa-кабинете.
- [ ] **R.8** End-to-end тест с тестовой картой ЮKassa.
- [ ] **R.9** `docs/billing_guide.md` — финальный гид клиенту.

---

## Предположения и заглушки

Если в процессе встретятся внешние зависимости/секреты, которых сейчас
нет — **ставим заглушки** и фиксируем здесь:

- **`YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY`** — заглушки в `.env`
  до получения тест-аккаунта от клиента. Если ключей нет — `payment_service`
  возвращает 503 с понятной ошибкой, парсинг не блокируется.
- **`BASIC_PRICE_RUB` / `PRO_PRICE_RUB`** — `=0` пока клиент не назовёт
  цены. При попытке создать платёж с 0 — отдаём 400 "цена не настроена".
- **Чеки 54-ФЗ** — пока клиент не подключил ИП к ЮKassa, передаём
  чеки в receipt с TODO-email. Если ИП не подключён, ЮKassa просто
  не делает фискализацию.
- **Существующие `simple`/`ai` tenant'ы** — оставляем как «бессрочная
  Basic/Pro» (через `is_paid_plan` helper). Миграция их не трогает.
- **Email клиента для чеков** — пока не запрашиваем, перед первой
  оплатой добавим обязательное поле в кабинет.

---

## Правила выполнения

1. Каждый блок завершается прогоном `pytest tests/` (только тесты
   соответствующих модулей; полный прогон — перед commit'ом).
2. После зелёных тестов — commit с понятным сообщением + push в `main`.
3. После Блока 5 (API готов) и перед Блоком 6 (Mini App) — короткий
   smoke-test API через curl с реальным initData.
4. Никаких неутверждённых изменений вне ТЗ.
5. Логирование щедрое: каждая платёжная транзакция, webhook, попытка
   автосписания — отдельная запись в `logs/billing/*.log`.
6. Заглушки помечаются `# TODO: заглушка — заменить когда будет <X>`
   в коде и фиксируются в этом файле.

---

## Связанные документы

- [tz_billing_tariffs.md](./tz_billing_tariffs.md) — полное ТЗ.
- [tz_ai_qualification_v3.md](./tz_ai_qualification_v3.md) — Этап 2,
  завершён.
- [roadmap_ai_qualification_v3.md](./roadmap_ai_qualification_v3.md) —
  roadmap Этапа 2 (все блоки выполнены, в проде с 2026-05-15).
