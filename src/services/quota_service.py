"""Учёт квот ИИ-квалификации по тарифам.

Каждый tenant имеет два независимых лимита (на 30 дней):
- ``ai_quota_companies_monthly`` — компаний, прошедших Stage 3 (LLM).
- ``ai_quota_tokens_monthly`` — суммарных токенов LLM.

Достижение **любой** из квот блокирует дальнейшие LLM-вызовы до сброса.
Сброс счётчиков — каждые 30 дней от ``quota_period_start``.

См. ТЗ v3, раздел 6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.db.models import TariffPlan, Tenant

logger = logging.getLogger(__name__)

PERIOD_DAYS = 30


@dataclass(frozen=True)
class QuotaUsage:
    """Снимок текущего состояния квоты для UI/API."""
    tariff_plan: str
    quota_companies: int
    quota_tokens: int
    used_companies: int
    used_tokens: int
    period_started_at: datetime | None
    period_ends_at: datetime | None
    days_remaining: int | None
    companies_percent: int  # 0-100, для прогресс-бара
    tokens_percent: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_period(tenant: Tenant) -> None:
    """Если ``quota_period_start`` пуст — выставляем сейчас.

    Для совсем новых tenant'ов или для свежесозданных в тестах.
    """
    if tenant.quota_period_start is None:
        tenant.quota_period_start = _now()


def reset_if_period_expired(session: Session, tenant: Tenant) -> bool:
    """Если 30 дней с ``quota_period_start`` прошло — сбрасывает счётчики.

    Возвращает True, если сброс произошёл. Изменения НЕ коммитит — это
    зона ответственности вызывающего (внешний транзакционный контекст).
    """
    _ensure_period(tenant)
    now = _now()
    period_start = tenant.quota_period_start
    # SQLite возвращает naive datetime; приводим к UTC.
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)

    if now - period_start >= timedelta(days=PERIOD_DAYS):
        tenant.ai_companies_processed_period = 0
        tenant.ai_tokens_used_period = 0
        tenant.quota_period_start = now
        session.add(tenant)
        logger.info(
            "Quota period reset for tenant_id=%s (was started %s)",
            tenant.id, period_start.isoformat(),
        )
        return True
    return False


def check_can_use_ai(tenant: Tenant) -> tuple[bool, str | None]:
    """Возвращает (можно_ли_использовать_ИИ, причина_отказа).

    Не делает I/O, не коммитит. Не сбрасывает period — это зона
    ``reset_if_period_expired``. Поэтому перед каждым вызовом ИИ-стадии
    в qualify_service сначала вызвать reset, потом check.
    """
    # Dev-whitelist: всегда True, минуя тариф и квоты.
    # Счётчики при этом всё равно инкрементируются (см. increment_*),
    # чтобы в Кабинете dev видел реальные расходы LLM.
    from src.services.tariff_helpers import is_developer
    if is_developer(tenant):
        return True, None

    # ИИ доступен на Pro и legacy AI. Trial/Basic/Simple — без ИИ.
    if tenant.tariff_plan not in (TariffPlan.AI.value, TariffPlan.PRO.value):
        return False, "tariff_not_ai"

    if (
        tenant.ai_quota_companies_monthly
        and tenant.ai_companies_processed_period >= tenant.ai_quota_companies_monthly
    ):
        return False, "companies_quota_exceeded"

    if (
        tenant.ai_quota_tokens_monthly
        and tenant.ai_tokens_used_period >= tenant.ai_quota_tokens_monthly
    ):
        return False, "tokens_quota_exceeded"

    return True, None


def increment_companies(session: Session, tenant: Tenant, n: int = 1) -> None:
    """+N к счётчику обработанных через LLM компаний."""
    _ensure_period(tenant)
    tenant.ai_companies_processed_period = (
        (tenant.ai_companies_processed_period or 0) + n
    )
    session.add(tenant)


def increment_tokens(session: Session, tenant: Tenant, tokens: int) -> None:
    """+tokens к счётчику использованных токенов."""
    if tokens <= 0:
        return
    _ensure_period(tenant)
    tenant.ai_tokens_used_period = (tenant.ai_tokens_used_period or 0) + tokens
    session.add(tenant)


def get_usage(tenant: Tenant) -> QuotaUsage:
    """Снимок состояния квоты — для отображения в Mini App."""
    period_start = tenant.quota_period_start
    if period_start is not None and period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)

    period_ends = (
        period_start + timedelta(days=PERIOD_DAYS) if period_start else None
    )
    days_remaining: int | None = None
    if period_ends is not None:
        delta = period_ends - _now()
        days_remaining = max(0, delta.days)

    def _percent(used: int, quota: int) -> int:
        if quota <= 0:
            return 0
        return min(100, int(round(used / quota * 100)))

    return QuotaUsage(
        tariff_plan=tenant.tariff_plan,
        quota_companies=tenant.ai_quota_companies_monthly,
        quota_tokens=tenant.ai_quota_tokens_monthly,
        used_companies=tenant.ai_companies_processed_period,
        used_tokens=tenant.ai_tokens_used_period,
        period_started_at=period_start,
        period_ends_at=period_ends,
        days_remaining=days_remaining,
        companies_percent=_percent(
            tenant.ai_companies_processed_period,
            tenant.ai_quota_companies_monthly,
        ),
        tokens_percent=_percent(
            tenant.ai_tokens_used_period,
            tenant.ai_quota_tokens_monthly,
        ),
    )


def usage_warning_threshold(usage: QuotaUsage, threshold_percent: int = 80) -> bool:
    """True, если хотя бы одна из квот достигла threshold (по умолчанию 80%).

    Используется для отправки превентивного уведомления клиенту.
    """
    return (
        usage.companies_percent >= threshold_percent
        or usage.tokens_percent >= threshold_percent
    )
