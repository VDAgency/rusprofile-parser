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

## Блок 2. ЮKassa wrapper + payment_service

- [ ] **2.1** `src/services/yookassa_client.py`: `AsyncYooKassaClient`
  (asyncio.to_thread поверх sync SDK), методы `create_payment`,
  `get_payment`, `charge_recurrent`, `parse_webhook`.
- [ ] **2.2** `src/services/payment_service.py`:
  `create_payment_for_tariff`, `process_webhook_succeeded`,
  `process_webhook_canceled`, `process_recurrent_charge`.
  Idempotence через `yookassa_idempotence_key`.
- [ ] **2.3** Чеки (54-ФЗ): встроить `receipt` в `create_payment`
  с TODO-полями (email + vat_code, заполнить при наличии данных от
  клиента).
- [ ] **2.4** Тесты `test_yookassa_client.py` + `test_payment_service.py`
  с моками HTTP-вызовов SDK → commit `feat(billing): yookassa wrapper +
  payment_service с idempotence и webhook-обработкой` → push.

## Блок 3. subscription_service

- [ ] **3.1** `src/services/subscription_service.py`:
  - `get_active_subscription(session, tenant)`
  - `create_subscription_from_payment(session, tenant, tariff, payment, method_id)`
  - `cancel_subscription(session, sub)` — auto_renew=False, сохраняем
    доступ до `expires_at`
  - `mark_past_due(session, sub)`
  - `renew_subscription(session, sub)` — попытка `charge_recurrent`
- [ ] **3.2** Lifecycle-тесты подписок (`test_subscription_service.py`):
  оплата → активация → продление → отмена → expired → reactivation.
- [ ] **3.3** `pytest` зелёное → commit `feat(billing): subscription_service
  с lifecycle и автопродлением` → push.

## Блок 4. Scheduler + notifications

- [ ] **4.1** `src/services/notifications.py` + `config/notification_templates.py`:
  9 типов уведомлений (раздел 11.1 ТЗ), безопасная отправка через
  try/except (бот заблокирован клиентом — не падаем).
- [ ] **4.2** `src/services/billing_scheduler.py`: APScheduler-jobs
  - `check_trial_expirations` (каждые 30 мин)
  - `check_subscription_expirations` (каждый час)
  - `try_recurrent_renewals` (каждые 4 часа)
  - `process_past_due` (каждый час)
  - `send_trial_reminders` (раз в день в 10:00 МСК)
- [ ] **4.3** Регистрация scheduler-jobs в `src/main.py`.
- [ ] **4.4** Тесты `test_billing_scheduler.py` через freeze_time/моки.
- [ ] **4.5** `pytest` зелёное → commit `feat(billing): scheduler-jobs
  для trial/подписок + унифицированные уведомления в Telegram` → push.

## Блок 5. API + интеграция в parse

- [ ] **5.1** `src/api/billing_router.py`:
  - `GET /api/billing/plans` — список тарифов и цен
  - `POST /api/billing/create-payment` — создание платежа, возврат
    `confirmation_url`
  - `GET /api/billing/payment-status/{id}` — для client-polling
  - `POST /api/billing/yookassa-webhook` — публичный (без auth),
    валидируется по signature/IP
  - `POST /api/billing/cancel-subscription`
  - `GET /api/billing/payments` — история
  - `GET /api/billing/subscription` — детали активной
- [ ] **5.2** Расширить `src/api/tariff_router.py::get_tariff`:
  поля `trial_expires_at`, `trial_parses_left`, `is_blocked`,
  `subscription` (active sub из БД), `parses_used_period`.
- [ ] **5.3** `src/bot/handlers.py`: перед запуском парсинга вызывать
  `tariff_helpers.is_parsing_available(tenant)` → если False, отвечать
  понятным сообщением и не стартовать.
- [ ] **5.4** `src/services/parse_service.py`: декремент
  `trial_parses_left` (Trial) / инкремент `parses_used_period` (Basic/Pro)
  сразу после `_clamp_max_new`. Компенсация (+1 обратно) при
  `error_message and total_new == 0`.
- [ ] **5.5** Тесты `test_parse_billing_integration.py`: trial-блок,
  компенсация при ошибке, idempotence декремента.
- [ ] **5.6** Nginx: настроить webhook-URL `/api/billing/yookassa-webhook`
  (он публичный, без auth) — допустить proxy_pass без auth-middleware.
  TODO в коде: проверить что middleware пропускает webhook.
- [ ] **5.7** `pytest` зелёное → commit `feat(billing): API эндпоинты,
  блокировка парсинга, интеграция в parse_service + webhook` → push.

## Блок 6. Mini App — кабинет и оплата

- [ ] **6.1** `src/webapp/index.html`:
  - Новая страница `page-cabinet` с динамическим контентом (loadCabinet
    заполнит)
  - Кнопка в bottom-nav `data-page="cabinet"` (видна всем)
  - Banner блокировки на странице «Парсинг» (показывается при
    `is_blocked=true`)
- [ ] **6.2** `src/webapp/app.v2.js`:
  - `loadCabinet()` — `GET /api/billing/subscription`,
    `GET /api/billing/payments`, рендер
  - `renderCabinetTrial(data)` — отображение для Trial
  - `renderCabinetActive(data)` — для активной подписки
  - `renderCabinetBlocked(data)` — для blocked
  - `payTariff(tariff)` — `POST /api/billing/create-payment`,
    `tg.openLink(confirmation_url)`
  - На `?paid=ok` в URL → polling `/api/billing/payment-status/{id}`
    с таймаутом 30 сек
- [ ] **6.3** `src/webapp/style.v2.css`: `cabinet-card`, `cabinet-meter`,
  `banner-blocked`, `pay-button-primary/secondary`,
  `payment-history-row`.
- [ ] **6.4** Cache-bust версию обновить (`sync_webapp_okved.py`
  делает автоматически при деплое).
- [ ] **6.5** Smoke в браузере (Telegram Web + Desktop) →
  commit `feat(ui): личный кабинет с тарифами, оплатой и историей
  платежей` → push.

## Блок 7. ЮKassa тест-режим + smoke в проде

- [ ] **7.1** Завести ЮKassa тест-аккаунт (или взять у клиента, если
  уже есть), получить `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY`
  (тестовые).
- [ ] **7.2** Прописать в `/opt/rusprofile-parser/.env`:
  - `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_WEBHOOK_SECRET`
  - `BASIC_PRICE_RUB`, `PRO_PRICE_RUB`
  - `TRIAL_DAYS=7`, `TRIAL_PARSES_LIMIT=10`
- [ ] **7.3** Зарегистрировать webhook URL в ЮKassa-кабинете:
  `https://parserclients.ru/api/billing/yookassa-webhook`.
- [ ] **7.4** End-to-end тест в проде с тестовой картой ЮKassa
  (`5555 5555 5555 4444` — успешная, `5555 5555 5555 4477` — отказ):
  - Создание нового tenant → trial выдан
  - Оплата Basic → подписка активна
  - Парсинг работает
  - Cancel → доступ до expires_at
  - Истечение → блок + уведомление
- [ ] **7.5** `docs/billing_guide.md` — гид для клиента: как
  оформить ИП в ЮKassa, как переключить из тест-режима в боевой,
  что нужно от клиента для запуска платежей.
- [ ] **7.6** Финальный commit `docs(billing): гид клиента + acceptance
  testing complete` → push.

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
