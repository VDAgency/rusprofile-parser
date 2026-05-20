# ТЗ. Этап 3 — Личный кабинет, тарифы и биллинг

> Документ — техническое задание на превращение парсера в полноценный
> SaaS с тарифной моделью, личным кабинетом пользователя и оплатой
> через ЮKassa.
>
> **Статус:** к реализации.
> **Версия:** v1.
> **Дата:** 2026-05-20.
> **Зависит от:** Этапа 2 v3 ([tz_ai_qualification_v3.md](./tz_ai_qualification_v3.md)) — уже раскатан в проде.

---

## 1. Контекст

После раската Этапа 2 (ИИ-квалификация) парсер технически готов к
монетизации, но **по факту все пользователи на `tariff_plan='simple'`
и пользуются бесплатно без ограничений**. Нужна тарифная модель и
оплата, чтобы превратить инструмент в продукт.

**Бизнес-цель:** клиент платит за пользование, мы автоматически
учитываем тариф, лимиты, продлеваем подписку через ЮKassa.

**Минимальный экономический смысл:**
- Trial — 7 дней или 10 парсингов (что наступит раньше). Достаточно
  чтобы клиент попробовал и оценил.
- Basic — парсинг без ИИ. Для тех, кто хочет руками отбирать лиды.
- Pro — парсинг + ИИ. Для тех, кому важна автоматическая
  квалификация.

---

## 2. Текущее состояние

### 2.1 Что уже есть (Этап 2 v3)

- Таблица `tenants` с полями `tariff_plan` (`simple` / `ai`),
  `ai_quota_companies_monthly`, `ai_quota_tokens_monthly`,
  `ai_companies_processed_period`, `ai_tokens_used_period`,
  `quota_period_start`.
- Сервис `quota_service.py` с `check_can_use_ai`, `reset_if_period_expired`,
  `get_usage`.
- API `GET /api/tariff` возвращает текущий тариф + квоту + использование.
- Плашка тарифа в шапке Mini App.
- Раздел «ИИ-профили» и формы парсинга показываются только при
  `tariff_plan == 'ai'`.

### 2.2 Чего НЕ хватает

- Тарифа `trial` (с двойным лимитом: дни И парсинги).
- Учёта запусков парсинга (сейчас считаем только LLM-вызовы).
- Поля «срок подписки» / «дата окончания» / «ID метода оплаты».
- Таблицы `payments` (история транзакций).
- Таблицы `subscriptions` (активные подписки и их статус).
- ЮKassa SDK + webhook-обработчика.
- Личного кабинета (страница «Кабинет» в Mini App).
- Логики блокировки парсинга по окончанию trial / неоплате.
- Уведомлений в Telegram (trial заканчивается, оплата прошла, баланс низкий).

---

## 3. Тарифные планы

### 3.1 Trial — выдаётся автоматически новым tenant'ам

| Параметр | Значение |
|----------|----------|
| Длительность | 7 дней с момента регистрации (`trial_started_at`) |
| Лимит парсингов | 10 запусков парсинга (любой источник) |
| Окончание | Что наступит раньше: `expires_at` или `parses_left == 0` |
| ИИ-квалификация | **Не доступна** (как в `simple`) |
| Кросс-обогащение | Доступно |

**Трансформация tariff_plan:** добавляем третье значение `trial`. По
умолчанию все НОВЫЕ tenant'ы создаются как `trial`. Существующие
сейчас `simple` остаются `simple` (миграция не меняет).

### 3.2 Basic — платный без ИИ

| Параметр | Значение |
|----------|----------|
| Цена | `BASIC_PRICE_RUB` в `.env` (TODO заполнить перед запуском) |
| Период | 30 дней, автопродление |
| Лимит парсингов | Без жёсткого лимита (но есть soft-лимит 100 запусков/мес для защиты от абуза) |
| ИИ-квалификация | **Не доступна** |
| Кросс-обогащение | Доступно |
| Sheets / Excel / История | Доступно |

### 3.3 Pro — платный с ИИ

| Параметр | Значение |
|----------|----------|
| Цена | `PRO_PRICE_RUB` в `.env` (TODO) |
| Период | 30 дней, автопродление |
| Лимит парсингов | Без жёсткого лимита (soft 300/мес) |
| ИИ-квалификация | Доступна с квотой `ai_quota_companies_monthly` (default 1000) и `ai_quota_tokens_monthly` (default 10_000_000) |
| Кросс-обогащение | Доступно |
| Sheets / Excel / История | Доступно |

### 3.4 «simple» — legacy

Существующие tenant'ы, которые получили `simple` при миграции v3
(сейчас 2 шт.). После Этапа 3:
- В коде `simple` приравнивается к Basic-функционалу (парсинг без ИИ).
- В UI отображается как "Basic (бессрочно)" — это «спасибо за
  раннюю поддержку» статус.
- Не блокируется, не требует оплаты.

---

## 4. Архитектура

```
┌────────────────────────────────────────────────────────────┐
│  Регистрация (первый /start или первое использование)      │
│        ↓                                                   │
│  Tenant создан с tariff_plan='trial', trial_started_at=now │
│  parses_left=10, trial_expires_at=now+7d                   │
└────────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│  Каждый запуск парсинга:                                   │
│    1. check_can_parse(tenant) → True/False + reason        │
│    2. Если True: запускаем, в конце decrement_parses       │
│    3. Если False: возвращаем 403 / показываем баннер       │
└────────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│  Trial истёк → tariff_plan='trial_expired', blocked=True   │
│        ↓                                                   │
│  Mini App показывает банннер «Trial закончился, оплатите»  │
│  Кнопки «Перейти на Basic» / «Перейти на Pro»              │
└────────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│  Клиент жмёт «Оплатить Pro»:                               │
│    1. POST /api/billing/create-payment {tariff: "pro"}     │
│    2. Создаём запись в payments (status='pending')         │
│    3. ЮKassa createPayment → confirmation_url              │
│    4. Открываем confirmation_url в WebView Mini App        │
│    5. ЮKassa делает webhook /api/billing/yookassa-webhook  │
│    6. На webhook: обновляем subscription, активируем тариф │
│    7. Отправляем Telegram-сообщение «Оплата прошла»        │
└────────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│  Подписка активна (expires_at = now + 30d):                │
│  За 3 дня до окончания — напоминание клиенту               │
│  За 1 день — попытка автосписания (ЮKassa recurrent)       │
│  Успех: продлеваем expires_at, статус active               │
│  Неудача: 3 повторных попытки, потом subscription='past_due│
│  past_due > 5 дней → блокировка парсинга                   │
└────────────────────────────────────────────────────────────┘
```

---

## 5. Схема БД

### 5.1 Изменения в `tenants`

```python
op.add_column("tenants", sa.Column("trial_started_at", sa.DateTime()))
op.add_column("tenants", sa.Column("trial_expires_at", sa.DateTime()))
op.add_column("tenants", sa.Column("trial_parses_left", sa.Integer(), default=10))
op.add_column("tenants", sa.Column("parses_used_period", sa.Integer(), default=0))
# Активная подписка (если есть) — внешний ключ на subscriptions.id
op.add_column("tenants", sa.Column("active_subscription_id", sa.Integer(), nullable=True))
# Блокировка работы (trial_expired / past_due / cancelled)
op.add_column("tenants", sa.Column("is_blocked", sa.Boolean(), default=False, server_default="0"))
op.add_column("tenants", sa.Column("blocked_reason", sa.String(50), nullable=True))
```

И обновляем `tariff_plan` enum: добавляем `trial`, `basic`, `pro`,
`trial_expired`. (Старые `simple` и `ai` оставляем для совместимости.)

### 5.2 Новая таблица `subscriptions`

```python
op.create_table(
    "subscriptions",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("tariff_plan", sa.String(20), nullable=False),  # basic / pro
    sa.Column("status", sa.String(20), nullable=False),
    # active / past_due / cancelled / expired
    sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    sa.Column("starts_at", sa.DateTime(), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    sa.Column("auto_renew", sa.Boolean(), default=True),
    # ЮKassa
    sa.Column("yookassa_payment_method_id", sa.String(64), nullable=True),
    # Метаданные
    sa.Column("price_rub", sa.Integer(), nullable=False),
    sa.Column("currency", sa.String(3), default="RUB"),
    sa.Column("notes", sa.Text(), nullable=True),
)
op.create_index("ix_subscriptions_tenant_active", "subscriptions", ["tenant_id", "status"])
op.create_index("ix_subscriptions_expires", "subscriptions", ["expires_at", "status"])
```

### 5.3 Новая таблица `payments`

```python
op.create_table(
    "payments",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("subscription_id", sa.Integer(),
              sa.ForeignKey("subscriptions.id"), nullable=True),
    # ЮKassa
    sa.Column("yookassa_payment_id", sa.String(64), nullable=False, unique=True),
    sa.Column("yookassa_idempotence_key", sa.String(64), nullable=True),
    sa.Column("yookassa_status", sa.String(20), nullable=False),
    # pending / waiting_for_capture / succeeded / canceled
    sa.Column("amount_rub", sa.Integer(), nullable=False),
    sa.Column("currency", sa.String(3), default="RUB"),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("metadata", sa.JSON(), nullable=True),
    sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    sa.Column("paid_at", sa.DateTime(), nullable=True),
    sa.Column("recurrent", sa.Boolean(), default=False),
    sa.Column("error_code", sa.String(50), nullable=True),
    sa.Column("error_message", sa.Text(), nullable=True),
)
op.create_index("ix_payments_tenant", "payments", ["tenant_id"])
```

### 5.4 Новые ENUM в models.py

```python
class TariffPlan(str, Enum):
    SIMPLE = "simple"             # legacy
    AI = "ai"                     # legacy
    TRIAL = "trial"               # 7 дней / 10 парсингов
    TRIAL_EXPIRED = "trial_expired"
    BASIC = "basic"               # платный без ИИ
    PRO = "pro"                   # платный с ИИ


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
```

### 5.5 Helper-функции в `tariff_helpers.py` (новый)

```python
def is_ai_available(tenant: Tenant) -> bool:
    """Доступна ли ИИ-квалификация (Pro / AI / SIMPLE-legacy=False)."""
    return tenant.tariff_plan in (TariffPlan.PRO.value, TariffPlan.AI.value)

def is_parsing_available(tenant: Tenant) -> tuple[bool, str | None]:
    """Можно ли запустить парсинг сейчас. Возвращает (ok, reason)."""

def is_paid_plan(tenant: Tenant) -> bool:
    """Это платный план (Basic / Pro / SIMPLE-legacy=True / AI-legacy=True)."""
```

---

## 6. Сервис подписок — `subscription_service.py`

Файл: `src/services/subscription_service.py`.

```python
def get_active_subscription(session, tenant) -> Subscription | None:
    """Возвращает текущую активную подписку (status='active', не истёкшая)."""

def create_subscription_from_payment(
    session, *, tenant, tariff_plan, payment_method_id, amount_rub,
) -> Subscription:
    """После успешного платежа — создаёт подписку (или продлевает существующую)."""

def cancel_subscription(session, subscription) -> None:
    """Клиент нажал «Отменить подписку» — auto_renew=False, статус сохраняется
    до expires_at, потом expires."""

def mark_past_due(session, subscription) -> None:
    """Автосписание не прошло — переводим в past_due. Через 5 дней
    grace-периода — блок tenant'а."""

def renew_subscription(session, subscription) -> bool:
    """Попытка автосписания через recurrent ЮKassa. True/False."""

def check_expirations_job(session) -> dict:
    """APScheduler-task: раз в час сканирует subscriptions и:
    - помечает expired по истечению срока,
    - запускает renew за 1 день до expires,
    - блокирует tenant'ов чьё past_due > 5 дней.
    Возвращает агрегат для лога."""
```

---

## 7. Интеграция с ЮKassa

### 7.1 Конфигурация

`.env`:
```env
# ЮKassa
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_WEBHOOK_SECRET=          # для проверки IP / signature webhook'ов
# Тарифы — цены в рублях. TODO: заполнить перед запуском.
BASIC_PRICE_RUB=0
PRO_PRICE_RUB=0
# Trial
TRIAL_DAYS=7
TRIAL_PARSES_LIMIT=10
# Grace
SUBSCRIPTION_GRACE_DAYS=5         # сколько дней past_due прежде чем заблокировать
RENEWAL_REMINDER_DAYS=3           # за сколько дней до окончания напоминать
```

### 7.2 Зависимости

```
yookassa>=3.4
```

### 7.3 Wrapper `yookassa_client.py`

Файл: `src/services/yookassa_client.py`.

```python
class YooKassaClient:
    """Тонкая обёртка над yookassa SDK.

    Все методы async через asyncio.to_thread, потому что SDK
    синхронный (requests). Возвращают доменные dataclass'ы, не
    raw response, чтобы изоляция была чище.
    """

    @classmethod
    def from_env(cls) -> "YooKassaClient | None":
        ...

    async def create_payment(
        self, *, amount_rub: int, description: str,
        return_url: str, save_payment_method: bool,
        metadata: dict | None = None,
    ) -> PaymentCreationResult:
        """Создаёт платёж в ЮKassa. Возвращает confirmation_url
        и yookassa_payment_id."""

    async def get_payment(self, yookassa_payment_id: str) -> PaymentInfo:
        """Получить актуальный статус платежа."""

    async def charge_recurrent(
        self, *, amount_rub: int, description: str,
        payment_method_id: str, metadata: dict | None = None,
    ) -> PaymentCreationResult:
        """Автосписание (без редиректа клиента) для recurrent."""

    def parse_webhook(self, body: bytes, signature: str | None) -> dict:
        """Валидирует и парсит webhook от ЮKassa. Возвращает dict
        или бросает WebhookValidationError."""
```

### 7.4 Чеки (54-ФЗ)

Параметр `receipt` обязателен для российских платежей. В `create_payment`
передаём:

```python
receipt = {
    "customer": {"email": tenant.email or "noreply@parserclients.ru"},
    "items": [{
        "description": f"Подписка {tariff_plan}",
        "quantity": "1.00",
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "vat_code": 1,  # без НДС или 20% — уточнить с клиентом
        "payment_subject": "service",
        "payment_mode": "full_prepayment",
    }],
}
```

**TODO:** уточнить с клиентом — есть ли у него ИП/ООО для приёма
платежей, какая система налогообложения, нужны ли чеки. Если нет —
платежи только в режиме «без чека» (только тест-режим ЮKassa).

---

## 8. API эндпоинты

### 8.1 Существующие (расширение)

| Метод | Эндпоинт | Что меняется |
|-------|----------|--------------|
| `GET` | `/api/tariff` | Расширить ответ: `trial_expires_at`, `trial_parses_left`, `is_blocked`, `subscription`, `parses_used_period` |
| `POST` | `/api/runs` (внутренний) | Перед запуском вызывать `is_parsing_available` → 402 если нет |

### 8.2 Новые

| Метод | Эндпоинт | Назначение |
|-------|----------|-----------|
| `GET` | `/api/billing/plans` | Список тарифов с ценами для UI |
| `POST` | `/api/billing/create-payment` | Body: `{tariff: "basic" \| "pro"}`. Создаёт ЮKassa-платёж, возвращает `confirmation_url` |
| `GET` | `/api/billing/payment-status/{payment_id}` | Polling статуса (на случай если webhook задержался) |
| `POST` | `/api/billing/yookassa-webhook` | **Публичный** (без auth), валидируется по signature. Принимает webhook от ЮKassa |
| `POST` | `/api/billing/cancel-subscription` | Отменяет авто-продление текущей подписки |
| `GET` | `/api/billing/payments` | История платежей tenant'а |
| `GET` | `/api/billing/subscription` | Детали текущей подписки |

### 8.3 Безопасность webhook

- ЮKassa шлёт webhook с **публичных IP** — добавить whitelist
  (`YOOKASSA_IP_RANGES` в коде).
- Дополнительно — HMAC-валидация через `YOOKASSA_WEBHOOK_SECRET`,
  если включён.
- Идемпотентность: уже обработанный `yookassa_payment_id` →
  возвращаем 200 без действий.

---

## 9. Личный кабинет в Mini App

### 9.1 Новая вкладка «Кабинет» в bottom-nav

```
🔍 Парсинг | 📜 История | 🤖 ИИ-профили | 👤 Кабинет
```

(`🤖 ИИ-профили` остаётся видимым только для Pro/AI; `👤 Кабинет` —
для всех.)

### 9.2 Структура страницы

```
┌──────────────────────────────────────────────┐
│  👤 Кабинет                                  │
├──────────────────────────────────────────────┤
│  Тариф: Pro                                  │
│  Активна до: 14 июня 2026 (через 25 дн.)     │
│  Автопродление: ✓ включено                   │
│                                              │
│  Использование за период:                    │
│  • Парсингов: 12 / без лимита                │
│  • ИИ-проверок: 234 / 1000 (23%)             │
│  • Токенов: 2.3M / 10M (23%)                 │
│                                              │
│  [Отменить автопродление] [Сменить тариф]    │
├──────────────────────────────────────────────┤
│  История платежей                            │
│  ┌─────────────────────────────────────────┐ │
│  │ 14.05.2026 Pro 30 дн.    2990 ₽ ✓      │ │
│  │ 14.04.2026 Pro 30 дн.    2990 ₽ ✓      │ │
│  └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### 9.3 Состояние для Trial

```
┌──────────────────────────────────────────────┐
│  👤 Кабинет                                  │
├──────────────────────────────────────────────┤
│  Тариф: Trial (бесплатно)                    │
│  ⏰ Осталось: 5 дней, 7 парсингов            │
│                                              │
│  Что попробовать после Trial:                │
│  • Basic — парсинг без ограничений (___ ₽)   │
│  • Pro — то же + ИИ-квалификация (___ ₽)     │
│                                              │
│  [Перейти на Basic] [Перейти на Pro]         │
└──────────────────────────────────────────────┘
```

### 9.4 Состояние «Trial закончился»

```
┌──────────────────────────────────────────────┐
│  ⚠ Trial закончился                          │
│                                              │
│  Парсинг приостановлен. История и Sheets    │
│  остаются доступными.                        │
│                                              │
│  [Оплатить Basic] [Оплатить Pro]             │
└──────────────────────────────────────────────┘
```

(И аналогичный баннер сверху вкладки «Парсинг» с дисейблом кнопок.)

### 9.5 Modal оплаты

При клике «Оплатить Pro»:
1. JS вызывает `POST /api/billing/create-payment {tariff: "pro"}`.
2. Получает `confirmation_url`.
3. Открывает через `tg.openLink(confirmation_url)` — Telegram открывает
   во внешнем браузере или внутреннем WebView (зависит от клиента).
4. После оплаты ЮKassa редиректит на `return_url=https://parserclients.ru/app/?paid=ok`.
5. Mini App при загрузке проверяет `?paid=ok` → POLLING `/api/billing/payment-status/{id}`
   пока не получит `succeeded` или таймаут 30 сек.
6. Показываем сообщение успеха.

(Webhook от ЮKassa отдельно — он надёжный источник правды;
client-side polling — для UX-feedback'а пока webhook ещё в пути.)

---

## 10. Учёт парсингов

### 10.1 Когда списываем

Один **успешный запуск** парсинга = -1 от `trial_parses_left` (для Trial)
или +1 к `parses_used_period` (для Basic/Pro).

**Списывается** в `parse_service.run_rusprofile` / `run_yandex` СРАЗУ
после `_clamp_max_new` и перед открытием Playwright. Идемпотентно:
если парсинг упал с ошибкой и был перезапущен, списание один раз.

Если парсинг отвалился с error_message и `total_new == 0`:
**компенсация** — возвращаем 1 запуск обратно (чтобы клиент не
терял trial-попытки на наших багах).

### 10.2 Когда блокируем

Перед каждым запуском парсинга, в `handle_webapp_data`:

```python
ok, reason = is_parsing_available(tenant)
if not ok:
    await message.answer(f"⚠ Парсинг недоступен: {reason}. Откройте кабинет.")
    return
```

Возможные `reason`:
- `trial_expired_days` — закончились дни trial.
- `trial_expired_parses` — закончились бесплатные парсинги.
- `subscription_past_due` — оплата не прошла, идёт grace.
- `subscription_blocked` — past_due > 5 дней, блок.

### 10.3 Сброс счётчика `parses_used_period`

Раз в 30 дней (по `quota_period_start` для AI) или раз в месяц
подписки (для Basic — точно по дате последней оплаты). Логика —
в `subscription_service.renew_subscription`.

---

## 11. Уведомления в Telegram

### 11.1 Триггеры и тексты

| Событие | Текст |
|---------|-------|
| Регистрация → Trial выдан | «🎁 Вам выдан бесплатный пробный период: 7 дней / 10 парсингов. Откройте Mini App → Кабинет, чтобы посмотреть детали.» |
| Trial: осталось 3 парсинга | «⏰ Осталось 3 пробных парсинга. Чтобы продолжить — оформите подписку: …» |
| Trial: остался 1 день | «⏰ Завтра заканчивается пробный период. Оформите подписку, чтобы не потерять доступ: …» |
| Trial истёк | «❌ Пробный период закончился. Чтобы продолжить парсинг, оплатите тариф в Кабинете.» |
| Оплата успешна | «✅ Оплата принята! Подписка Pro активна до 14 июня 2026. Спасибо!» |
| Подписка: за 3 дня | «⏰ Через 3 дня — автосписание 2990 ₽ за продление Pro. Чтобы отменить — Кабинет → «Отменить автопродление».» |
| Автосписание не прошло | «⚠ Не удалось автоматически списать оплату. У вас 5 дней grace-периода. Проверьте карту в Кабинете.» |
| Past_due > 5 дней → блок | «❌ Подписка приостановлена из-за непрошедшего платежа. Оформите оплату в Кабинете для возобновления.» |
| Подписка отменена клиентом | «Подписка отменена. Доступ сохранится до 14 июня 2026.» |

### 11.2 Реализация

Новый модуль `src/services/notifications.py`:

```python
async def send_notification(bot, telegram_user_id, kind, **context) -> None:
    """Шлёт notification по типу. Тексты в config/notification_templates.py."""
```

---

## 12. Scheduler-задачи (APScheduler)

В проекте уже подключён APScheduler. Добавляем jobs:

| Job | Cron | Назначение |
|-----|------|-----------|
| `check_trial_expirations` | каждые 30 мин | Найти tenant'ов с `tariff='trial'` и `trial_expires_at < now`, перевести в `trial_expired` + уведомление |
| `check_subscription_expirations` | каждый час | Subscription `expires_at < now` и `auto_renew=False` → expired + блок tenant |
| `try_recurrent_renewals` | каждые 4 часа | Subscription с `expires_at` через 1 день и `auto_renew=True` → запускаем `charge_recurrent` |
| `process_past_due` | каждый час | Subscription `status='past_due'` + время > `SUBSCRIPTION_GRACE_DAYS` → блок tenant |
| `send_trial_reminders` | раз в день в 10:00 МСК | Уведомления про trial (за 3 дня / 1 день / 1 час) |

Файл: `src/services/billing_scheduler.py`.

---

## 13. Файлы — создать и изменить

### Создать

| Файл | Назначение |
|------|-----------|
| `src/services/yookassa_client.py` | Wrapper SDK |
| `src/services/subscription_service.py` | CRUD подписок + lifecycle |
| `src/services/payment_service.py` | Создание платежей, обработка webhook |
| `src/services/tariff_helpers.py` | `is_ai_available`, `is_parsing_available`, `is_paid_plan` |
| `src/services/billing_scheduler.py` | APScheduler-jobs |
| `src/services/notifications.py` | Унифицированная отправка уведомлений |
| `src/api/billing_router.py` | Эндпоинты `/api/billing/*` |
| `config/notification_templates.py` | Шаблоны текстов уведомлений |
| `alembic/versions/XXXX_add_billing.py` | Миграция |
| `tests/test_tariff_helpers.py` | Тесты helper-ов |
| `tests/test_subscription_service.py` | Тесты подписок |
| `tests/test_payment_service.py` | Тесты создания платежей и webhook (с моком ЮKassa) |
| `tests/test_yookassa_client.py` | Тесты wrapper'а |
| `tests/test_billing_scheduler.py` | Тесты scheduler-jobs |

### Изменить

| Файл | Что изменить |
|------|-------------|
| `src/db/models.py` | Поля trial/blocked в Tenant, новые модели Subscription, Payment, ENUMs |
| `src/db/__init__.py` | Экспорт новых моделей |
| `src/db/dedup.py::ensure_tenant` | Для нового tenant — выставить `tariff_plan='trial'`, `trial_started_at=now`, `trial_expires_at=now+7d`, `trial_parses_left=10` |
| `src/bot/handlers.py` | Перед запуском парсинга — `is_parsing_available` check |
| `src/services/parse_service.py` | Декремент `trial_parses_left` / инкремент `parses_used_period` после успешного парсинга. Компенсация при ошибке |
| `src/api/server.py` | Подключение `billing_router` |
| `src/api/tariff_router.py::get_tariff` | Расширить ответ полями trial/subscription/blocked |
| `src/webapp/index.html` | Новая страница «Кабинет», банннер блокировки, кнопки оплаты |
| `src/webapp/app.v2.js` | Логика кабинета, modal оплаты, polling статуса |
| `src/webapp/style.v2.css` | Стили cabinet-card, banner-blocked, payment-button |
| `.env.example` | YOOKASSA_*, BASIC_PRICE_RUB, PRO_PRICE_RUB, TRIAL_* |
| `requirements.txt` | yookassa>=3.4 |

---

## 14. План реализации (порядок коммитов)

### Блок 1: БД и helper-ы (1 день)

1. Alembic миграция: поля trial/blocked + таблицы subscriptions/payments.
2. Модели SQLAlchemy: Tenant (extended), Subscription, Payment + ENUMs.
3. `src/services/tariff_helpers.py`: `is_ai_available`, `is_parsing_available`,
   `is_paid_plan` + тесты.
4. `src/db/dedup.py::ensure_tenant`: новый tenant сразу с Trial.

### Блок 2: ЮKassa wrapper + payment_service (1-2 дня)

5. `src/services/yookassa_client.py`: SDK wrapper, `create_payment`,
   `charge_recurrent`, `parse_webhook`. Тесты с моками.
6. `src/services/payment_service.py`: `create_payment_for_tariff`,
   `process_webhook`, `process_recurrent_charge`. Тесты.

### Блок 3: subscription_service (1 день)

7. `src/services/subscription_service.py`: lifecycle подписок,
   `get_active`, `create_from_payment`, `cancel`, `renew`, `mark_past_due`.
8. Тесты subscription flow с моками.

### Блок 4: scheduler + notifications (1 день)

9. `src/services/notifications.py` + `config/notification_templates.py`.
10. `src/services/billing_scheduler.py` + регистрация в `main.py`.
11. Тесты scheduler-jobs (через time-travel mocks).

### Блок 5: API + интеграция в parse (1 день)

12. `src/api/billing_router.py`: все эндпоинты `/api/billing/*`.
13. `src/api/tariff_router.py`: расширение `GET /api/tariff`.
14. `src/bot/handlers.py`: `is_parsing_available` check.
15. `src/services/parse_service.py`: декремент/инкремент парсингов
    с компенсацией.
16. Тесты parse-блокировки.

### Блок 6: Mini App кабинет (1-2 дня)

17. `index.html`: вкладка «Кабинет», banner блокировки.
18. `app.v2.js`: `loadCabinet`, `renderCabinet`, payment flow, polling.
19. `style.v2.css`: стили cabinet/banner/buttons.

### Блок 7: ЮKassa тест-режим + smoke (0.5 дня)

20. Завести ЮKassa тест-аккаунт, добавить ключи в `.env` на сервере.
21. Прогон полного flow: trial → trial_expired → оплата Basic →
    активна → отмена → expired. Через тестовые карты ЮKassa.
22. Документация для клиента (`docs/billing_guide.md`).

**Итого:** ~7-9 рабочих дней.

---

## 15. Риски и митигации

| Риск | Митигация |
|------|-----------|
| ЮKassa webhook не дойдёт (сеть) | Client-side polling `/api/billing/payment-status/{id}` как backup |
| Двойное списание Trial-парсинга при retry | Идемпотентный счётчик: списываем по `run_id`, проверяем `parses_used_log` |
| Webhook от чужого источника | Whitelist по IP-диапазону ЮKassa + HMAC по `YOOKASSA_WEBHOOK_SECRET` |
| Клиент не получает уведомления (бот заблокирован) | Все уведомления оборачиваем `try/except`, статус подписки правит ТОЛЬКО webhook (а не наши notifications) |
| Подписка отменена пока webhook в пути | Очерёдность операций: payment_service сначала проверяет, нет ли activated cancel в БД |
| 54-ФЗ: нет ИП у клиента | Делаем тест-режим без чеков, перед запуском в продакшене — обязательно завести ИП и привязать к ЮKassa |
| Параллельные платежи (клиент жмёт 2 раза) | Idempotence key в ЮKassa (uuid от tenant+tariff+timestamp), на сервере — lock per tenant_id |
| История платежей для legacy `simple` | Helper `is_paid_plan` отдельно учитывает `simple` и `ai` как «уже оплачено навсегда» |

---

## 16. Критерии приёмки

### БД и helper-ы
- [ ] Миграция применяется без ошибок на чистой БД и на проде.
- [ ] Существующие tenant'ы (`simple`, `ai`) остаются работоспособными
  без изменений.
- [ ] Новые tenant'ы создаются с `tariff='trial'`, корректным
  `trial_expires_at`, `trial_parses_left=10`.

### ЮKassa
- [ ] `create_payment` создаёт платёж в тест-режиме, возвращает
  `confirmation_url`.
- [ ] Тестовая оплата картой `5555 5555 5555 4444` → webhook → подписка
  активирована.
- [ ] `charge_recurrent` корректно проходит для tenant'а с
  `payment_method_id`.
- [ ] Webhook отвергается без правильного signature/IP.

### Trial и блокировка
- [ ] Trial истекает по дням → tenant получает уведомление + блок.
- [ ] Trial истекает по парсингам → блок, уведомление.
- [ ] При ошибке парсинга `parses_left` возвращается обратно.
- [ ] После блокировки — `/api/runs` отвечает 402, Mini App показывает
  баннер.

### Подписки
- [ ] Активная подписка истекает → попытка автосписания за 1 день.
- [ ] Успешное автосписание → продление `expires_at`, уведомление.
- [ ] Неудачное автосписание (тестовая карта `5555 5555 5555 4477`) →
  `past_due` + 3 retry + grace 5 дней + блок.
- [ ] Клиент может отменить автопродление, доступ сохраняется до
  `expires_at`.

### Mini App
- [ ] Вкладка «Кабинет» отображается для всех тарифов.
- [ ] Для Trial показывается остаток дней/парсингов и кнопки оплаты.
- [ ] Для активной подписки — детали + история платежей + отмена.
- [ ] После оплаты — polling статуса с feedback в UI.

### Уведомления
- [ ] Все 9 типов сообщений (раздел 11) приходят в правильный момент.
- [ ] Не дублируются (используем флаги `notified_at` в Subscription).

### Безопасность
- [ ] Webhook валидирует source.
- [ ] Idempotence ключи защищают от двойных платежей.
- [ ] Все API `/api/billing/*` (кроме webhook) требуют initData.

---

## 17. Открытые вопросы для клиента

Перед стартом нужно уточнить:

1. **Цены:** `BASIC_PRICE_RUB`, `PRO_PRICE_RUB` (рекомендуем 990 / 2990,
   но клиент решает).
2. **ИП/ООО для ЮKassa:** есть ли у клиента юр. лицо для приёма платежей.
   Без этого — только тест-режим без чеков.
3. **Налоги:** какая система налогообложения (УСН / ОСНО)? Влияет на
   `vat_code` в чеке.
4. **Email для чеков:** обязательное поле в 54-ФЗ. Запрашивать у tenant'а
   перед первой оплатой?
5. **Quota Pro:** оставляем дефолты (1000 компаний / 10M токенов в мес)
   или делаем больше/меньше?
6. **Trial для существующих tenant'ов:** дать им снова 7-дневный
   trial для теста или оставить «бессрочно-бесплатно»?

---

## 18. Стартовый промпт для новой сессии

```
Привет. Работаем над проектом rusprofile-parser. Этап 2 (ИИ-квалификация)
раскатан в проде на 2026-05-20. Сейчас начинаем Этап 3 — личный кабинет,
тарифы (Trial / Basic / Pro) и биллинг через ЮKassa.

Полное ТЗ: docs/tz_billing_tariffs.md
Roadmap (с прогрессом): docs/roadmap_billing_tariffs.md
Архитектура подписок: разделы 5, 6, 7 ТЗ.

Решения, которые уже зафиксированы:
- Платёжная система: ЮKassa
- Trial: 7 дней или 10 парсингов (что раньше)
- Trial-учёт: 1 запуск = -1
- После trial: полный блок
- Платные тарифы: Basic (без ИИ) + Pro (с ИИ)
- Биллинг: подписка с автопродлением
- Кабинет: новая вкладка в bottom-nav
- Цены: заглушки в .env пока

Стартуем по плану из раздела 14. Блок 1 — миграция БД, модели,
tariff_helpers, обновление ensure_tenant.

Код-стиль: БЫЛО/СТАЛО для всех правок, без неутверждённых изменений,
архитектурное согласование до реализации, русский неформальный регистр,
после каждого блока — pytest + commit + push.
```
