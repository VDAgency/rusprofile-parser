"""APScheduler-jobs для биллинга и trial.

Регистрируется в ``src/main.py`` через ``register_billing_jobs(scheduler, bot)``.

Jobs:
- ``check_trial_expirations`` (каждые 30 мин): tenant'ы с
  ``tariff='trial'``, у которых истёк срок дней — переводим в
  ``trial_expired`` + уведомление.
- ``check_subscription_expirations`` (каждый час): subscriptions с
  истёкшим ``expires_at`` и ``auto_renew=False`` → expire + блок.
- ``try_recurrent_renewals`` (каждые 4 часа): subscriptions ACTIVE с
  ``expires_at`` через ≤1 день и ``auto_renew=True`` — пытаемся
  продлить (сейчас stub, после ЮKassa — реальный charge).
- ``process_past_due`` (каждый час): subscriptions PAST_DUE, у
  которых grace-период истёк → block_after_grace.
- ``send_trial_reminders`` (раз в день в 10:00 МСК): напоминания
  про trial (1 день / N парсингов).

Все jobs защищены от исключений и не падают целиком — каждый tenant
обрабатывается изолированно.

См. ТЗ v1, раздел 12.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.db import get_session
from src.db.models import (
    BlockedReason,
    Subscription,
    SubscriptionStatus,
    TariffPlan,
    Tenant,
)
from src.services import subscription_service as sub_svc
from src.services.notifications import NotificationKind, send_notification

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _format_dt(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    ts = _ensure_aware(ts)
    return ts.strftime("%d.%m.%Y")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def check_trial_expirations(bot: "Bot | None" = None) -> dict:
    """Trial-tenant'ы, у которых истёк срок дней. Переводим в
    ``trial_expired`` + уведомление."""
    stats = {"checked": 0, "expired": 0, "errors": 0}
    now = _now()
    with get_session() as session:
        rows = session.execute(
            select(Tenant).where(
                Tenant.tariff_plan == TariffPlan.TRIAL.value,
            )
        ).scalars().all()
        for tenant in rows:
            stats["checked"] += 1
            expires = _ensure_aware(tenant.trial_expires_at)
            if expires is None or expires > now:
                continue
            try:
                tenant.tariff_plan = TariffPlan.TRIAL_EXPIRED.value
                tenant.is_blocked = True
                tenant.blocked_reason = BlockedReason.TRIAL_EXPIRED_DAYS.value
                session.flush()
                stats["expired"] += 1
                logger.info("Trial expired for tenant_id=%s", tenant.id)
                if bot is not None:
                    await send_notification(
                        bot, tenant.telegram_user_id,
                        NotificationKind.TRIAL_EXPIRED.value,
                    )
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.exception(
                    "check_trial_expirations: failed for tenant_id=%s: %s",
                    tenant.id, e,
                )
    if stats["expired"]:
        logger.info("check_trial_expirations done: %s", stats)
    return stats


async def check_subscription_expirations(bot: "Bot | None" = None) -> dict:
    """Подписки ACTIVE с истёкшим expires_at И ``auto_renew=False``
    (или не было попытки продлить) → expire + блок."""
    stats = {"checked": 0, "expired": 0, "errors": 0}
    now = _now()
    with get_session() as session:
        rows = session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.expires_at <= now,
            )
        ).scalars().all()
        for sub in rows:
            stats["checked"] += 1
            # Если auto_renew=True и платёж ещё не пробовали — пусть
            # это сделает try_recurrent_renewals. Сюда попадают только
            # те, кто истёк уже без активного автопродления.
            if sub.auto_renew:
                continue
            try:
                tenant = session.get(Tenant, sub.tenant_id)
                if tenant is None:
                    continue
                sub_svc.expire_subscription(session, sub, tenant)
                stats["expired"] += 1
                if bot is not None:
                    await send_notification(
                        bot, tenant.telegram_user_id,
                        NotificationKind.TRIAL_EXPIRED.value,  # тот же текст
                    )
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.exception(
                    "check_subscription_expirations failed for sub_id=%s: %s",
                    sub.id, e,
                )
    if stats["expired"]:
        logger.info("check_subscription_expirations done: %s", stats)
    return stats


async def try_recurrent_renewals(bot: "Bot | None" = None) -> dict:
    """Подписки ACTIVE с ``auto_renew=True`` и истекающие в ближайшие
    1 день — пытаемся продлить.

    Сейчас ``renew_subscription_stub`` всегда возвращает False (нет
    эквайринга), поэтому все подписки помечаются PAST_DUE. Это
    осознанное поведение — клиент увидит уведомление и сможет
    «оплатить вручную» через поддержку.
    """
    stats = {"attempted": 0, "renewed": 0, "past_due": 0, "errors": 0}
    with get_session() as session:
        expiring = sub_svc.find_expiring_soon(session, within_days=1)
        for sub in expiring:
            if not sub.auto_renew:
                continue
            stats["attempted"] += 1
            try:
                renewed = sub_svc.renew_subscription_stub(session, sub)
                if renewed:
                    stats["renewed"] += 1
                    # реальная логика появится с ЮKassa
                else:
                    sub_svc.mark_past_due(session, sub)
                    stats["past_due"] += 1
                    if bot is not None:
                        tenant = session.get(Tenant, sub.tenant_id)
                        if tenant:
                            await send_notification(
                                bot, tenant.telegram_user_id,
                                NotificationKind.SUBSCRIPTION_RENEWAL_FAILED.value,
                                grace_days=_grace_days(),
                            )
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.exception(
                    "try_recurrent_renewals failed for sub_id=%s: %s",
                    sub.id, e,
                )
    if stats["attempted"]:
        logger.info("try_recurrent_renewals done: %s", stats)
    return stats


async def process_past_due(bot: "Bot | None" = None) -> dict:
    """PAST_DUE с истёкшим grace → block_after_grace + уведомление."""
    stats = {"checked": 0, "blocked": 0, "errors": 0}
    with get_session() as session:
        overdue = sub_svc.find_past_due_overdue(session)
        for sub in overdue:
            stats["checked"] += 1
            try:
                tenant = session.get(Tenant, sub.tenant_id)
                if tenant is None:
                    continue
                sub_svc.block_after_grace(session, sub, tenant)
                stats["blocked"] += 1
                if bot is not None:
                    await send_notification(
                        bot, tenant.telegram_user_id,
                        NotificationKind.SUBSCRIPTION_BLOCKED.value,
                    )
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.exception(
                    "process_past_due failed for sub_id=%s: %s", sub.id, e,
                )
    if stats["blocked"]:
        logger.info("process_past_due done: %s", stats)
    return stats


async def send_trial_reminders(bot: "Bot | None" = None) -> dict:
    """Раз в день: напоминания trial-tenant'ам.

    Сценарии:
    - Остался 1 календарный день → TRIAL_LAST_DAY
    - Осталось ≤3 парсингов (и не было ранее) → TRIAL_FEW_PARSES

    Не нужно сложного дедупа — APScheduler настроен на раз в день,
    клиент получит максимум 1 такое сообщение в сутки.
    """
    stats = {"checked": 0, "reminded": 0, "errors": 0}
    now = _now()
    with get_session() as session:
        rows = session.execute(
            select(Tenant).where(Tenant.tariff_plan == TariffPlan.TRIAL.value)
        ).scalars().all()
        for tenant in rows:
            stats["checked"] += 1
            try:
                expires = _ensure_aware(tenant.trial_expires_at)
                days_left = None
                if expires is not None:
                    delta = expires - now
                    days_left = delta.days  # 0 = сегодня закончится
                parses_left = tenant.trial_parses_left or 0

                if bot is None:
                    continue

                # Приоритет — last_day (более срочное).
                if days_left == 0 or days_left == 1:
                    ok = await send_notification(
                        bot, tenant.telegram_user_id,
                        NotificationKind.TRIAL_LAST_DAY.value,
                    )
                    if ok:
                        stats["reminded"] += 1
                elif 0 < parses_left <= 3:
                    ok = await send_notification(
                        bot, tenant.telegram_user_id,
                        NotificationKind.TRIAL_FEW_PARSES.value,
                        parses_left=parses_left,
                    )
                    if ok:
                        stats["reminded"] += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.exception(
                    "send_trial_reminders failed for tenant_id=%s: %s",
                    tenant.id, e,
                )
    if stats["reminded"]:
        logger.info("send_trial_reminders done: %s", stats)
    return stats


def _grace_days() -> int:
    from src.config import SUBSCRIPTION_GRACE_DAYS
    return SUBSCRIPTION_GRACE_DAYS


# ---------------------------------------------------------------------------
# Регистрация в APScheduler
# ---------------------------------------------------------------------------


def register_billing_jobs(
    scheduler: "AsyncIOScheduler", bot: "Bot | None" = None,
) -> None:
    """Регистрирует все jobs в переданном scheduler'е.

    Вызывается из ``main.py`` после создания AsyncIOScheduler и до
    ``scheduler.start()``.
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        check_trial_expirations,
        trigger=IntervalTrigger(minutes=30),
        kwargs={"bot": bot},
        id="check_trial_expirations",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        check_subscription_expirations,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot},
        id="check_subscription_expirations",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        try_recurrent_renewals,
        trigger=IntervalTrigger(hours=4),
        kwargs={"bot": bot},
        id="try_recurrent_renewals",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        process_past_due,
        trigger=IntervalTrigger(hours=1),
        kwargs={"bot": bot},
        id="process_past_due",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        send_trial_reminders,
        # 10:00 МСК = 07:00 UTC.
        trigger=CronTrigger(hour=7, minute=0),
        kwargs={"bot": bot},
        id="send_trial_reminders",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info("Billing scheduler jobs registered (5 шт.)")
