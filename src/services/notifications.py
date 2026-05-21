"""Унифицированные уведомления tenant'ам через Telegram-бот.

Все сообщения проходят через ``send_notification(bot, telegram_user_id, kind)``,
который:
- берёт шаблон из ``NOTIFICATION_TEMPLATES``
- подставляет контекст
- безопасно отправляет (try/except — если клиент заблокировал бот,
  не падаем)
- логирует факт отправки/ошибки

См. ТЗ v1, раздел 11.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot

logger = logging.getLogger(__name__)


class NotificationKind(str, Enum):
    """Типы уведомлений для биллинга/тарифов."""

    TRIAL_GRANTED = "trial_granted"
    TRIAL_FEW_PARSES = "trial_few_parses"          # осталось ≤3 парсингов
    TRIAL_LAST_DAY = "trial_last_day"              # остался 1 день
    TRIAL_EXPIRED = "trial_expired"

    PAYMENT_SUCCESS = "payment_success"
    SUBSCRIPTION_RENEWAL_SOON = "subscription_renewal_soon"     # за 3 дня
    SUBSCRIPTION_RENEWAL_FAILED = "subscription_renewal_failed"
    SUBSCRIPTION_BLOCKED = "subscription_blocked"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"


NOTIFICATION_TEMPLATES: dict[str, str] = {
    NotificationKind.TRIAL_GRANTED.value: (
        "🎁 Вам выдан бесплатный пробный период: <b>{trial_days} дней / "
        "{trial_parses} парсингов</b>.\n\n"
        "Откройте Mini App → Кабинет, чтобы посмотреть детали."
    ),
    NotificationKind.TRIAL_FEW_PARSES.value: (
        "⏰ Осталось <b>{parses_left} пробных парсингов</b>.\n\n"
        "Чтобы продолжить — оформите подписку в Кабинете."
    ),
    NotificationKind.TRIAL_LAST_DAY.value: (
        "⏰ Завтра заканчивается пробный период.\n\n"
        "Оформите подписку, чтобы не потерять доступ. Откройте Кабинет."
    ),
    NotificationKind.TRIAL_EXPIRED.value: (
        "❌ Пробный период закончился.\n\n"
        "Чтобы продолжить парсинг — оплатите тариф в Кабинете."
    ),
    NotificationKind.PAYMENT_SUCCESS.value: (
        "✅ Оплата принята! Подписка <b>{tariff_name}</b> активна до "
        "<b>{expires_at}</b>.\n\nСпасибо!"
    ),
    NotificationKind.SUBSCRIPTION_RENEWAL_SOON.value: (
        "⏰ Через <b>{days_left} дн.</b> — автосписание <b>{amount} ₽</b> "
        "за продление подписки {tariff_name}.\n\n"
        "Чтобы отменить — Кабинет → «Отменить автопродление»."
    ),
    NotificationKind.SUBSCRIPTION_RENEWAL_FAILED.value: (
        "⚠ Не удалось автоматически списать оплату.\n\n"
        "У вас <b>{grace_days} дн.</b> grace-периода. Проверьте карту в Кабинете."
    ),
    NotificationKind.SUBSCRIPTION_BLOCKED.value: (
        "❌ Подписка приостановлена из-за непрошедшего платежа.\n\n"
        "Оформите оплату в Кабинете для возобновления."
    ),
    NotificationKind.SUBSCRIPTION_CANCELLED.value: (
        "Подписка отменена. Доступ сохранится до <b>{expires_at}</b>."
    ),
}


def render(kind: str, **context: Any) -> str:
    """Рендерит шаблон. Если context не хватает — кидает KeyError
    в логе и возвращает шаблон как есть (лучше отправить кривое,
    чем ничего)."""
    tpl = NOTIFICATION_TEMPLATES.get(kind)
    if tpl is None:
        raise ValueError(f"Unknown notification kind: {kind!r}")
    try:
        return tpl.format(**context)
    except KeyError as e:
        logger.warning(
            "render(%s) missing context key %s; falling back to raw template",
            kind, e,
        )
        return tpl


async def send_notification(
    bot: "Bot | None",
    telegram_user_id: int,
    kind: str,
    **context: Any,
) -> bool:
    """Безопасно отправляет уведомление пользователю.

    Возвращает True/False. Не бросает исключений — если клиент
    заблокировал бот, удалил чат, или Telegram временно недоступен,
    мы НЕ должны рушить scheduler-job.
    """
    if bot is None:
        logger.info(
            "send_notification(%s → uid=%s): bot is None, skipping",
            kind, telegram_user_id,
        )
        return False
    try:
        text = render(kind, **context)
    except ValueError as e:
        logger.error("Notification kind error: %s", e)
        return False

    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text=text,
        )
        logger.info(
            "Notification sent: kind=%s uid=%s",
            kind, telegram_user_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 — Telegram может бросать что угодно
        logger.warning(
            "Notification failed: kind=%s uid=%s err=%s",
            kind, telegram_user_id, e,
        )
        return False
