"""Тесты интеграции биллинга в parse_service.

Проверяют:
- декремент trial_parses_left для Trial,
- инкремент parses_used_period для всех,
- компенсация при ошибке парсинга (через _compensate_parses_counter).

Не запускаем реальный парсер — тестируем только helper-функции
учёта, чтобы было быстро и без Playwright.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.db.models import TariffPlan, Tenant
from src.services.parse_service import (
    _compensate_parses_counter,
    _decrement_parses_counter,
)


# ─── _decrement_parses_counter ────────────────────────────────────────────


def test_decrement_trial_consumes_parse():
    t = Tenant(
        telegram_user_id=1,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=10,
        parses_used_period=0,
    )
    _decrement_parses_counter(t)
    assert t.trial_parses_left == 9
    assert t.parses_used_period == 1


def test_decrement_trial_does_not_go_negative():
    t = Tenant(
        telegram_user_id=1,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=0,
        parses_used_period=10,
    )
    _decrement_parses_counter(t)
    # Trial-счётчик не уходит в минус (защита от race condition).
    assert t.trial_parses_left == 0
    # parses_used_period всё равно растёт — это soft-метрика.
    assert t.parses_used_period == 11


def test_decrement_basic_only_increments_period():
    t = Tenant(
        telegram_user_id=1,
        tariff_plan=TariffPlan.BASIC.value,
        trial_parses_left=0,
        parses_used_period=42,
    )
    _decrement_parses_counter(t)
    assert t.trial_parses_left == 0
    assert t.parses_used_period == 43


def test_decrement_pro_only_increments_period():
    t = Tenant(
        telegram_user_id=1,
        tariff_plan=TariffPlan.PRO.value,
        trial_parses_left=0,
        parses_used_period=5,
    )
    _decrement_parses_counter(t)
    assert t.parses_used_period == 6


def test_decrement_legacy_simple_only_increments_period():
    t = Tenant(
        telegram_user_id=1,
        tariff_plan=TariffPlan.SIMPLE.value,
        trial_parses_left=0,
        parses_used_period=0,
    )
    _decrement_parses_counter(t)
    assert t.parses_used_period == 1


# ─── _compensate_parses_counter ───────────────────────────────────────────


def test_compensate_trial_restores_parse(db_session):
    t = Tenant(
        telegram_user_id=2001,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=9,
        parses_used_period=1,
    )
    db_session.add(t)
    db_session.commit()

    _compensate_parses_counter(t.id)

    db_session.refresh(t)
    assert t.trial_parses_left == 10
    assert t.parses_used_period == 0


def test_compensate_basic_only_decrements_period(db_session):
    t = Tenant(
        telegram_user_id=2002,
        tariff_plan=TariffPlan.BASIC.value,
        trial_parses_left=0,
        parses_used_period=5,
    )
    db_session.add(t)
    db_session.commit()

    _compensate_parses_counter(t.id)

    db_session.refresh(t)
    assert t.parses_used_period == 4
    # trial-счётчик не трогается, остаётся 0
    assert t.trial_parses_left == 0


def test_compensate_safe_when_counter_already_zero(db_session):
    """Если кто-то вызвал compensate когда счётчики 0 — не падаем."""
    t = Tenant(
        telegram_user_id=2003,
        tariff_plan=TariffPlan.BASIC.value,
        trial_parses_left=0,
        parses_used_period=0,
    )
    db_session.add(t)
    db_session.commit()

    _compensate_parses_counter(t.id)  # должно пройти без исключения

    db_session.refresh(t)
    assert t.parses_used_period == 0


def test_compensate_safe_for_unknown_tenant():
    """Несуществующий tenant_id — не падаем (ленивый fail)."""
    _compensate_parses_counter(999999)  # просто не должно бросить
