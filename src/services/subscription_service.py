"""Сервис управления подписками — lifecycle Basic/Pro.

Реализует CRUD + lifecycle подписок:
- ``get_active_subscription`` — текущая активная (active, не истекла)
- ``activate_subscription_manually`` — ручная активация (нужна СЕЙЧАС
  пока нет эквайринга; админ принимает оплату вне системы и активирует
  подписку этим методом). После подключения ЮKassa этот же метод будет
  переиспользован в ``create_subscription_from_payment``.
- ``cancel_subscription`` — клиент отменяет автопродление; доступ
  сохраняется до ``expires_at``.
- ``mark_past_due`` — автосписание не прошло, идёт grace.
- ``expire_subscription`` — срок истёк, переводим в EXPIRED + блок tenant.
- ``find_expiring_soon`` / ``find_past_due_overdue`` — для scheduler-jobs.

Не делает реальных вызовов ЮKassa — `renew_subscription` пока stub,
вернётся к нему в отложенном Блоке R.

См. ТЗ v1, раздел 6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    BlockedReason,
    Subscription,
    SubscriptionStatus,
    TariffPlan,
    Tenant,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Константы из config (lazy-загружаются, чтобы тестам было удобнее)
# ---------------------------------------------------------------------------


def _settings():
    from src import config
    return {
        "period_days": config.SUBSCRIPTION_PERIOD_DAYS,
        "grace_days": int(getattr(config, "SUBSCRIPTION_GRACE_DAYS", 5)),
        "renewal_reminder_days": int(getattr(config, "RENEWAL_REMINDER_DAYS", 3)),
        "basic_price": config.BASIC_PRICE_RUB,
        "pro_price": config.PRO_PRICE_RUB,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ---------------------------------------------------------------------------
# Запросы
# ---------------------------------------------------------------------------


def get_active_subscription(session: Session, tenant: Tenant) -> Subscription | None:
    """Возвращает активную подписку tenant'а (status='active', не истекла).

    Если подписка в past_due — НЕ возвращается (это уже не «активная»).
    Если истекла по сроку, но статус не обновлён — тоже не возвращается.
    """
    sub = session.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant.id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
        .order_by(Subscription.expires_at.desc())
    )
    if sub is None:
        return None
    if _ensure_aware(sub.expires_at) <= _now():
        return None  # истекла, статус ещё не обновлён scheduler'ом
    return sub


def list_tenant_subscriptions(
    session: Session, tenant: Tenant, *, limit: int = 50,
) -> list[Subscription]:
    """История всех подписок tenant'а — для UI Кабинета.

    Сортировка: по ``created_at`` desc; вторичная — по ``id`` desc, чтобы
    обеспечить стабильный порядок при равных метках (SQLite даёт
    CURRENT_TIMESTAMP с секундной точностью).
    """
    rows = session.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant.id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .limit(limit)
    ).scalars().all()
    return list(rows)


def find_expiring_soon(
    session: Session, *, within_days: int,
) -> list[Subscription]:
    """Подписки ACTIVE, которые истекут в течение N дней. Для scheduler:
    отправить напоминание / попытаться auto-renew."""
    cutoff = _now() + timedelta(days=within_days)
    return list(session.execute(
        select(Subscription).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.expires_at <= cutoff,
        )
    ).scalars().all())


def find_past_due_overdue(
    session: Session, *, grace_days: int | None = None,
) -> list[Subscription]:
    """Подписки PAST_DUE, у которых grace-период истёк → пора блокировать
    tenant и переводить в EXPIRED.
    """
    grace = grace_days if grace_days is not None else _settings()["grace_days"]
    cutoff = _now() - timedelta(days=grace)
    return list(session.execute(
        select(Subscription).where(
            Subscription.status == SubscriptionStatus.PAST_DUE.value,
            Subscription.expires_at <= cutoff,
        )
    ).scalars().all())


# ---------------------------------------------------------------------------
# Активация и продление
# ---------------------------------------------------------------------------


@dataclass
class ActivationResult:
    subscription: Subscription
    created_new: bool       # True — создали; False — продлили существующую
    new_expires_at: datetime


def activate_subscription_manually(
    session: Session,
    tenant: Tenant,
    *,
    tariff: str,
    months: int = 1,
    notes: str | None = None,
    yookassa_payment_method_id: str | None = None,
) -> ActivationResult:
    """Ручная активация подписки — для использования админом
    (CLI-скрипт) после приёма оплаты вне системы (банковский перевод
    и т.п.). Та же логика будет вызвана из payment-webhook после
    подключения эквайринга.

    Логика:
    - Если у tenant'а уже есть активная подписка того же тарифа →
      ПРОДЛЕВАЕМ (expires_at += months * SUBSCRIPTION_PERIOD_DAYS).
    - Если активная подписка другого тарифа (например, был Basic,
      покупает Pro) → старую помечаем CANCELLED + создаём новую.
    - Если активной нет → создаём.

    Дополнительно:
    - Переводим tenant.tariff_plan в новый тариф.
    - Снимаем is_blocked / blocked_reason если они были.
    - Сбрасываем счётчики periodа парсингов.
    - НЕ коммитит — это зона вызывающего.
    """
    s = _settings()
    if tariff not in (TariffPlan.BASIC.value, TariffPlan.PRO.value):
        raise ValueError(
            f"Tariff must be 'basic' or 'pro' (got {tariff!r})"
        )
    if months < 1:
        raise ValueError(f"months must be >= 1 (got {months})")

    price_rub = s["basic_price"] if tariff == TariffPlan.BASIC.value else s["pro_price"]
    period_total_days = s["period_days"] * months

    existing = get_active_subscription(session, tenant)
    now = _now()

    if existing is not None and existing.tariff_plan == tariff:
        # Продлеваем существующую: expires_at += period
        new_expires = _ensure_aware(existing.expires_at) + timedelta(days=period_total_days)
        existing.expires_at = new_expires
        existing.auto_renew = True
        if yookassa_payment_method_id:
            existing.yookassa_payment_method_id = yookassa_payment_method_id
        if notes:
            existing.notes = (existing.notes or "") + f"\n[{now.isoformat()}] {notes}"
        # tariff_plan tenant'а уже корректен — но на всякий случай:
        tenant.tariff_plan = tariff
        _unblock_tenant(tenant)
        _reset_period_counters(tenant)
        session.flush()
        logger.info(
            "Subscription extended for tenant_id=%s: tariff=%s, +%d days, "
            "new expires=%s",
            tenant.id, tariff, period_total_days, new_expires.isoformat(),
        )
        return ActivationResult(
            subscription=existing,
            created_new=False,
            new_expires_at=new_expires,
        )

    # Активной нет или другой тариф — создаём новую
    if existing is not None and existing.tariff_plan != tariff:
        existing.status = SubscriptionStatus.CANCELLED.value
        existing.cancelled_at = now
        existing.auto_renew = False
        logger.info(
            "Old subscription %s cancelled in favor of new %s",
            existing.tariff_plan, tariff,
        )

    new_expires = now + timedelta(days=period_total_days)
    sub = Subscription(
        tenant_id=tenant.id,
        tariff_plan=tariff,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now,
        expires_at=new_expires,
        auto_renew=True,
        yookassa_payment_method_id=yookassa_payment_method_id,
        price_rub=price_rub,
        currency="RUB",
        notes=notes,
    )
    session.add(sub)
    session.flush()

    tenant.tariff_plan = tariff
    tenant.active_subscription_id = sub.id
    _unblock_tenant(tenant)
    _reset_period_counters(tenant)
    session.flush()

    logger.info(
        "Subscription created for tenant_id=%s: tariff=%s, expires=%s",
        tenant.id, tariff, new_expires.isoformat(),
    )
    return ActivationResult(
        subscription=sub,
        created_new=True,
        new_expires_at=new_expires,
    )


# ---------------------------------------------------------------------------
# Lifecycle: отмена, past_due, expire
# ---------------------------------------------------------------------------


def cancel_subscription(
    session: Session, subscription: Subscription,
) -> Subscription:
    """Клиент нажал «Отменить автопродление».

    Status остаётся ACTIVE (доступ сохраняется до expires_at), но
    auto_renew=False — scheduler не будет пытаться продлить.
    cancelled_at фиксируется для UI.
    """
    if subscription.status != SubscriptionStatus.ACTIVE.value:
        logger.info(
            "cancel_subscription: subscription %s status=%s — пропускаем",
            subscription.id, subscription.status,
        )
        return subscription

    subscription.auto_renew = False
    subscription.cancelled_at = _now()
    session.flush()
    logger.info(
        "Subscription %s cancelled by tenant — access until %s",
        subscription.id,
        _ensure_aware(subscription.expires_at).isoformat(),
    )
    return subscription


def mark_past_due(
    session: Session, subscription: Subscription,
) -> Subscription:
    """Попытка автосписания не прошла. Переводим в PAST_DUE.

    Tenant НЕ блокируется сразу — даём grace-период. Блок применяется
    отдельным методом ``block_after_grace`` через scheduler.
    """
    subscription.status = SubscriptionStatus.PAST_DUE.value
    session.flush()
    logger.warning(
        "Subscription %s marked PAST_DUE (grace until %s)",
        subscription.id,
        (_ensure_aware(subscription.expires_at)
         + timedelta(days=_settings()["grace_days"])).isoformat(),
    )
    return subscription


def block_after_grace(
    session: Session, subscription: Subscription, tenant: Tenant,
) -> None:
    """Grace-период истёк. Переводим подписку в EXPIRED и блокируем tenant'а.

    Tenant.tariff_plan МЕНЯЕМ на TRIAL_EXPIRED (специально, чтобы код
    блокировки парсинга уже отрабатывал).
    """
    subscription.status = SubscriptionStatus.EXPIRED.value
    tenant.is_blocked = True
    tenant.blocked_reason = BlockedReason.SUBSCRIPTION_BLOCKED.value
    tenant.active_subscription_id = None
    # Не меняем tariff_plan: пусть остаётся `basic`/`pro` для истории,
    # но is_blocked=True перекрывает всё.
    session.flush()
    logger.warning(
        "Subscription %s EXPIRED, tenant_id=%s BLOCKED",
        subscription.id, tenant.id,
    )


def expire_subscription(
    session: Session, subscription: Subscription, tenant: Tenant,
) -> None:
    """Подписка закончилась по сроку (без past_due — клиент отменил
    автопродление или платёж не был оплачен).

    Tenant переводится в TRIAL_EXPIRED + блок.
    """
    subscription.status = SubscriptionStatus.EXPIRED.value
    tenant.is_blocked = True
    tenant.blocked_reason = BlockedReason.SUBSCRIPTION_CANCELLED.value
    tenant.active_subscription_id = None
    tenant.tariff_plan = TariffPlan.TRIAL_EXPIRED.value
    session.flush()
    logger.info(
        "Subscription %s expired naturally, tenant_id=%s BLOCKED",
        subscription.id, tenant.id,
    )


# ---------------------------------------------------------------------------
# Stub для автопродления — реальная логика придёт с ЮKassa
# ---------------------------------------------------------------------------


def renew_subscription_stub(
    session: Session, subscription: Subscription,
) -> bool:
    """Заглушка автопродления — пока эквайринг не подключён, всегда
    возвращает False (не можем списать → подписка пойдёт в past_due).

    После подключения ЮKassa этот метод будет переделан: вызов
    ``yookassa_client.charge_recurrent(...)`` и создание Payment-записи.
    """
    logger.info(
        "renew_subscription_stub: subscription %s — billing not configured, "
        "skipping renewal (will go to past_due)",
        subscription.id,
    )
    return False


# ---------------------------------------------------------------------------
# Внутренние хелперы
# ---------------------------------------------------------------------------


def _unblock_tenant(tenant: Tenant) -> None:
    tenant.is_blocked = False
    tenant.blocked_reason = None


def _reset_period_counters(tenant: Tenant) -> None:
    """При активации/продлении подписки сбрасываем счётчики периода:
    парсинги, ИИ-компании, токены — всё с нуля. quota_period_start —
    в текущий момент.
    """
    tenant.parses_used_period = 0
    tenant.ai_companies_processed_period = 0
    tenant.ai_tokens_used_period = 0
    tenant.quota_period_start = _now()
