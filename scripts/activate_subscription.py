"""CLI: ручная активация платной подписки tenant'а.

Используется СЕЙЧАС админом для активации Basic/Pro после получения
оплаты вне системы (банковский перевод, СБП по реквизитам и т.п.) —
до подключения ЮKassa-эквайринга.

Использование:

    python scripts/activate_subscription.py --uid 546373554 --tariff pro --months 1
    python scripts/activate_subscription.py --uid 546373554 --tariff basic --months 12 --notes "Оплата за год"

Что происходит:
- Если tenant'а с таким telegram_user_id нет — будет создан как Trial,
  затем сразу переключен на платный тариф.
- Если уже есть активная подписка того же тарифа — продлевается
  (expires_at += months × SUBSCRIPTION_PERIOD_DAYS).
- Если активная подписка другого тарифа — старая помечается cancelled,
  создаётся новая.
- В Telegram tenant'у автоматически НЕ отправляется уведомление —
  скрипт предполагает что админ сам сообщит. Если нужно — флаг
  ``--notify`` (см. опции).

Безопасность:
- Скрипт работает напрямую с БД (тот же DATABASE_URL что и у бота).
- Запускайте только на сервере (где есть доступ к /opt/rusprofile-parser/data/parser.db).
- Изменения коммитятся одной транзакцией.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Корень проекта в sys.path (когда запускаем из repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session, ensure_tenant
from src.db.models import TariffPlan
from src.services import subscription_service
from src.services.notifications import NotificationKind, send_notification

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Активировать платную подписку tenant'у вручную "
                    "(до подключения ЮKassa-эквайринга).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--uid", type=int, required=True,
        help="telegram_user_id tenant'а (можно посмотреть в логах бота)",
    )
    parser.add_argument(
        "--tariff", choices=["basic", "pro"], required=True,
        help="Тариф для активации",
    )
    parser.add_argument(
        "--months", type=int, default=1,
        help="На сколько месяцев активировать (default 1)",
    )
    parser.add_argument(
        "--notes", type=str, default=None,
        help="Произвольная заметка для истории (например, способ оплаты)",
    )
    parser.add_argument(
        "--username", type=str, default=None,
        help="Опционально: telegram username (если tenant'а ещё нет в БД)",
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="Отправить уведомление в Telegram (требует TELEGRAM_BOT_TOKEN)",
    )
    return parser.parse_args()


async def _send_payment_success_notification(
    *, telegram_user_id: int, tariff: str, expires_at_iso: str,
) -> None:
    """Отправляет уведомление PAYMENT_SUCCESS через одноразовый Bot.

    Делается отдельным async-блоком, чтобы основная функция оставалась
    синхронной (CLI-сценарий).
    """
    from src.config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN не задан, --notify пропущен")
        return

    from aiogram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        tariff_name = "Pro" if tariff == "pro" else "Basic"
        # Преобразуем ISO → читаемую дату.
        try:
            from datetime import datetime
            d = datetime.fromisoformat(expires_at_iso)
            expires_str = d.strftime("%d.%m.%Y")
        except Exception:  # noqa: BLE001
            expires_str = expires_at_iso

        ok = await send_notification(
            bot, telegram_user_id,
            NotificationKind.PAYMENT_SUCCESS.value,
            tariff_name=tariff_name, expires_at=expires_str,
        )
        if ok:
            print(f"  ✓ Уведомление отправлено пользователю {telegram_user_id}")
        else:
            print(f"  ⚠ Не удалось отправить уведомление "
                  f"(возможно, пользователь заблокировал бот)")
    finally:
        await bot.session.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()

    print(f"\nАктивация подписки:")
    print(f"  telegram_user_id: {args.uid}")
    print(f"  tariff:           {args.tariff}")
    print(f"  months:           {args.months}")
    print(f"  notes:            {args.notes or '(нет)'}")
    print()

    with get_session() as session:
        # ensure_tenant: создаст если нет (как Trial), потом
        # activate_subscription_manually переключит на платный.
        tenant = ensure_tenant(
            session, telegram_user_id=args.uid, username=args.username,
        )
        prev_plan = tenant.tariff_plan
        try:
            result = subscription_service.activate_subscription_manually(
                session, tenant,
                tariff=args.tariff,
                months=args.months,
                notes=args.notes,
            )
        except ValueError as e:
            print(f"❌ Ошибка: {e}")
            return 2

        # Commit делается при выходе из get_session().

    print(f"✓ Подписка {'продлена' if not result.created_new else 'создана'}")
    print(f"  ID подписки:      {result.subscription.id}")
    print(f"  Тариф:            {prev_plan} → {result.subscription.tariff_plan}")
    print(f"  Действует до:     {result.new_expires_at.strftime('%d.%m.%Y %H:%M %Z')}")
    print(f"  auto_renew:       {result.subscription.auto_renew}")

    expires_iso = result.new_expires_at.isoformat()

    if args.notify:
        print(f"\nОтправка уведомления в Telegram…")
        try:
            asyncio.run(_send_payment_success_notification(
                telegram_user_id=args.uid,
                tariff=args.tariff,
                expires_at_iso=expires_iso,
            ))
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ Уведомление упало: {e}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
