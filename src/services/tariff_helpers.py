"""Helper-функции для тарифной логики.

Без I/O, без БД-операций — только чтение полей Tenant. Используются
во всех местах, где нужно решить «доступна ли функция»:
- handlers.py перед запуском парсинга
- qualify_service перед ИИ-стадиями
- API/Mini App для отрисовки доступности кнопок

Принципы:
- Legacy-тарифы (``simple``, ``ai``) трактуются как «бессрочно
  оплаченный Basic/Pro» — за раннюю поддержку.
- ``trial`` — Basic-функционал (без ИИ) с двойным лимитом (дни +
  парсинги).
- ``trial_expired`` — нельзя ничего, кроме истории/скачивания.
- ``basic`` / ``pro`` — активные платные.

См. ТЗ v3, раздел 6 (`tariff_helpers`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.db.models import BlockedReason, TariffPlan, Tenant


# ---------------------------------------------------------------------------
# Группировка тарифов
# ---------------------------------------------------------------------------

# Платные «бессрочные» — legacy + любые активные платные.
_PAID_PLANS = {
    TariffPlan.SIMPLE.value,
    TariffPlan.AI.value,
    TariffPlan.BASIC.value,
    TariffPlan.PRO.value,
}

# Тарифы, где ИИ-квалификация включена.
_AI_PLANS = {
    TariffPlan.AI.value,
    TariffPlan.PRO.value,
}

# Тарифы, где парсинг разрешён в принципе (до проверки квот/блока).
_PARSING_PLANS = {
    TariffPlan.SIMPLE.value,
    TariffPlan.AI.value,
    TariffPlan.TRIAL.value,
    TariffPlan.BASIC.value,
    TariffPlan.PRO.value,
}


@dataclass(frozen=True)
class ParsingAvailability:
    """Результат проверки доступности парсинга для tenant'а."""

    ok: bool
    reason: str | None = None       # машинный код (BlockedReason или другой)
    message: str | None = None      # человекочитаемое объяснение для UI/чата


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(ts: datetime | None) -> datetime | None:
    """SQLite отдаёт naive datetime — приводим к UTC."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ---------------------------------------------------------------------------
# Базовые предикаты
# ---------------------------------------------------------------------------


def is_paid_plan(tenant: Tenant) -> bool:
    """Это «уже оплачено» (legacy simple/ai или активные basic/pro)."""
    return tenant.tariff_plan in _PAID_PLANS


def is_ai_available(tenant: Tenant) -> bool:
    """Доступна ли ИИ-квалификация (Pro / legacy AI).

    Trial и Basic — ИИ недоступен, только парсинг + сорт-скор.
    """
    if tenant.is_blocked:
        return False
    return tenant.tariff_plan in _AI_PLANS


def is_trial(tenant: Tenant) -> bool:
    return tenant.tariff_plan == TariffPlan.TRIAL.value


def is_trial_expired(tenant: Tenant) -> bool:
    return tenant.tariff_plan == TariffPlan.TRIAL_EXPIRED.value


# ---------------------------------------------------------------------------
# Главная проверка перед парсингом
# ---------------------------------------------------------------------------


def is_parsing_available(tenant: Tenant) -> ParsingAvailability:
    """Можно ли запустить парсинг сейчас.

    Логика по приоритету:
    1. Явная блокировка tenant'а (is_blocked) — отказ с blocked_reason.
    2. Тариф вне списка разрешённых (например, TRIAL_EXPIRED) — отказ.
    3. Trial: проверка дней (trial_expires_at < now) и парсингов
       (trial_parses_left <= 0).
    4. Иначе разрешено.

    Возвращает ``ParsingAvailability(ok, reason, message)``.
    """
    if tenant.is_blocked:
        reason = tenant.blocked_reason or BlockedReason.SUBSCRIPTION_BLOCKED.value
        return ParsingAvailability(
            ok=False, reason=reason,
            message=_block_message(reason),
        )

    if tenant.tariff_plan == TariffPlan.TRIAL_EXPIRED.value:
        return ParsingAvailability(
            ok=False,
            reason=BlockedReason.TRIAL_EXPIRED_DAYS.value,
            message=(
                "Пробный период закончился. Чтобы продолжить парсинг — "
                "оплатите тариф в Кабинете."
            ),
        )

    if tenant.tariff_plan not in _PARSING_PLANS:
        return ParsingAvailability(
            ok=False, reason="unknown_tariff",
            message=f"Тариф «{tenant.tariff_plan}» не поддерживает парсинг.",
        )

    if is_trial(tenant):
        # Проверка по дням
        expires = _ensure_aware(tenant.trial_expires_at)
        if expires is not None and _now() >= expires:
            return ParsingAvailability(
                ok=False,
                reason=BlockedReason.TRIAL_EXPIRED_DAYS.value,
                message=(
                    "Срок пробного периода (7 дней) закончился. "
                    "Оплатите тариф в Кабинете."
                ),
            )
        # Проверка по парсингам
        if (tenant.trial_parses_left or 0) <= 0:
            return ParsingAvailability(
                ok=False,
                reason=BlockedReason.TRIAL_EXPIRED_PARSES.value,
                message=(
                    "Закончились бесплатные парсинги (10 шт. в Trial). "
                    "Оплатите тариф в Кабинете."
                ),
            )

    return ParsingAvailability(ok=True)


def _block_message(reason: str) -> str:
    """Человекочитаемое сообщение по коду блокировки."""
    mapping = {
        BlockedReason.TRIAL_EXPIRED_DAYS.value: (
            "Срок пробного периода закончился. Оплатите тариф в Кабинете."
        ),
        BlockedReason.TRIAL_EXPIRED_PARSES.value: (
            "Закончились бесплатные парсинги Trial. Оплатите тариф в Кабинете."
        ),
        BlockedReason.SUBSCRIPTION_PAST_DUE.value: (
            "Не прошло автосписание оплаты. Проверьте карту в Кабинете."
        ),
        BlockedReason.SUBSCRIPTION_BLOCKED.value: (
            "Подписка приостановлена. Оформите оплату в Кабинете для "
            "возобновления."
        ),
        BlockedReason.SUBSCRIPTION_CANCELLED.value: (
            "Подписка отменена. Оформите новую в Кабинете."
        ),
    }
    return mapping.get(reason, "Парсинг недоступен. Откройте Кабинет.")


# ---------------------------------------------------------------------------
# Метаданные для UI/уведомлений
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialSnapshot:
    """Снимок состояния trial — для UI Кабинета и уведомлений."""

    is_trial: bool
    days_left: int | None
    parses_left: int | None
    expires_at: datetime | None
    started_at: datetime | None


def get_trial_snapshot(tenant: Tenant) -> TrialSnapshot:
    """Текущее состояние Trial. Если tenant не на trial — пустой snapshot."""
    if not is_trial(tenant):
        return TrialSnapshot(
            is_trial=False, days_left=None, parses_left=None,
            expires_at=None, started_at=None,
        )
    expires = _ensure_aware(tenant.trial_expires_at)
    days_left: int | None = None
    if expires is not None:
        delta = expires - _now()
        days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
    return TrialSnapshot(
        is_trial=True,
        days_left=days_left,
        parses_left=tenant.trial_parses_left,
        expires_at=expires,
        started_at=_ensure_aware(tenant.trial_started_at),
    )
