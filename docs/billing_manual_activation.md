# Ручная активация подписок до подключения ЮKassa

> Короткий гид для админа на период, пока эквайринг ещё не оформлен.
> Клиент оплачивает «вне системы» (банковский перевод, СБП по реквизитам),
> админ активирует тариф через CLI на сервере.

---

## 0. Контекст

С 2026-05-20 в боте включена тарифная модель:
- **Trial** — 7 дней / 10 парсингов (выдаётся новым tenant'ам автоматически).
- **Basic / Pro** — платные подписки (см. ТЗ [tz_billing_tariffs.md](./tz_billing_tariffs.md)).

Кнопка «Оплатить» в Mini App пока ведёт на **stub-страницу**
(`/app/payment_stub.html`) с текстом «Сервис платежей ещё в разработке»
и кнопкой «Связаться с поддержкой». После эквайринга stub будет
заменён на реальный платёж через ЮKassa.

**На переходный период** клиент платит вне системы, потом админ
активирует подписку этим CLI.

---

## 1. Как пользователь сообщает об оплате

В Mini App → **👤 Кабинет** → выбирает тариф → жмёт «Оплатить
(в разработке)» → попадает на stub-страницу → жмёт **«Связаться с
поддержкой»** → открывается чат с ботом.

Дальше — переписка вне системы:
1. Админ присылает реквизиты для оплаты.
2. Клиент платит и присылает подтверждение.
3. Админ активирует подписку (см. ниже).

---

## 2. Активация подписки (CLI)

Подключаемся к серверу:

```bash
ssh -i ~/.ssh/id_beget root@77.73.238.195
cd /opt/rusprofile-parser
source venv/bin/activate
```

### 2.1 Узнать `telegram_user_id` клиента

В переписке с ботом клиент шлёт **любое сообщение**. Смотрим логи:

```bash
tail -50 logs/systemd.log | grep "from_user"
# или
journalctl -u rusprofile-bot -n 50 | grep "from_user"
```

Альтернатива — посмотреть в БД:

```bash
python -c "
import sqlite3
c = sqlite3.connect('data/parser.db')
print('telegram_user_id | username | tariff_plan')
for r in c.execute('SELECT telegram_user_id, username, tariff_plan FROM tenants').fetchall():
    print(' | '.join(str(x) for x in r))
"
```

### 2.2 Активировать

```bash
# Pro на 1 месяц
python scripts/activate_subscription.py \
  --uid 546373554 \
  --tariff pro \
  --months 1 \
  --notes "Перевод по СБП 22.05.2026, чек #12345" \
  --notify

# Basic на 3 месяца с уведомлением в Telegram
python scripts/activate_subscription.py \
  --uid 546373554 \
  --tariff basic \
  --months 3 \
  --notes "Оплата на расчётный счёт" \
  --notify
```

### 2.3 Что делает скрипт

- Если tenant'а нет в БД — создаёт его (как Trial), сразу переключает на платный.
- Если уже есть **активная подписка того же тарифа** → продлевает на
  `months × 30 дней`.
- Если есть **подписка другого тарифа** (например, был Basic, оплатил
  Pro) → старую помечает `cancelled`, создаёт новую Pro.
- Снимает `is_blocked` и `blocked_reason` (если были).
- Сбрасывает счётчики периода (`parses_used_period=0`,
  `ai_companies_processed_period=0`, `ai_tokens_used_period=0`).
- Активирует `auto_renew=True` (но автосписание пока stub — после
  истечения подписка пойдёт в `past_due`).
- При `--notify` шлёт в Telegram сообщение «Оплата принята, подписка
  активна до DD.MM.YYYY».

---

## 3. Отмена подписки

Клиент может сам отменить автопродление через Mini App → Кабинет →
«Отменить автопродление». Доступ сохраняется до даты окончания.

Админ может полностью **погасить** подписку, поменяв в БД:

```bash
python -c "
from src.db import get_session
from src.db.models import Tenant, SubscriptionStatus, TariffPlan
from src.services.subscription_service import get_active_subscription, expire_subscription
from sqlalchemy import select
with get_session() as s:
    t = s.scalar(select(Tenant).where(Tenant.telegram_user_id == 546373554))
    sub = get_active_subscription(s, t)
    if sub:
        expire_subscription(s, sub, t)
        print(f'subscription {sub.id} expired, tenant blocked')
    else:
        print('no active subscription')
"
```

---

## 4. Проверка состояния

```bash
python -c "
import sqlite3
c = sqlite3.connect('data/parser.db')
print('--- Tenants ---')
for r in c.execute('SELECT telegram_user_id, tariff_plan, is_blocked, blocked_reason, trial_parses_left FROM tenants').fetchall():
    print(r)
print('--- Subscriptions ---')
for r in c.execute('SELECT id, tenant_id, tariff_plan, status, expires_at, auto_renew FROM subscriptions').fetchall():
    print(r)
"
```

---

## 5. Что когда подключим ЮKassa

1. Клиент получит **рабочую** кнопку «Оплатить» — без вмешательства админа.
2. Этот CLI **останется** — пригодится для:
   - бонусной активации (промо/тестового доступа),
   - компенсации (если webhook не дошёл),
   - корпоративных клиентов которые платят на счёт.

---

## 6. Полезные команды

```bash
# Полная проверка работоспособности (тарифы + ИИ + парсинг)
curl -s https://parserclients.ru/api/healthz   # → {"ok": true}

# Логи бота
tail -f /opt/rusprofile-parser/logs/systemd.log
journalctl -u rusprofile-bot -f

# Перезапуск (после правок кода)
systemctl restart rusprofile-bot
systemctl status rusprofile-bot --no-pager
```

См. также:
- [tz_billing_tariffs.md](./tz_billing_tariffs.md) — полное ТЗ
- [roadmap_billing_tariffs.md](./roadmap_billing_tariffs.md) — статус реализации
- [deploy.md в памяти Claude] — общий чеклист деплоя
