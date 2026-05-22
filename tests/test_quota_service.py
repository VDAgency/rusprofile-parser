"""Тесты учёта квот ИИ-квалификации."""

from datetime import datetime, timedelta, timezone

import pytest

from src.db.models import TariffPlan, Tenant
from src.services import quota_service


@pytest.fixture
def simple_tenant(db_session):
    """Tenant на тарифе Simple — ИИ недоступен."""
    t = Tenant(
        telegram_user_id=42,
        tariff_plan=TariffPlan.SIMPLE.value,
        ai_quota_companies_monthly=0,
        ai_quota_tokens_monthly=0,
    )
    db_session.add(t)
    db_session.flush()
    return t


@pytest.fixture
def ai_tenant(db_session):
    """Tenant на тарифе AI с задaнной квотой."""
    t = Tenant(
        telegram_user_id=43,
        tariff_plan=TariffPlan.AI.value,
        ai_quota_companies_monthly=100,
        ai_quota_tokens_monthly=1_000_000,
        quota_period_start=datetime.now(timezone.utc),
    )
    db_session.add(t)
    db_session.flush()
    return t


def test_simple_tariff_cannot_use_ai(simple_tenant):
    ok, reason = quota_service.check_can_use_ai(simple_tenant)
    assert ok is False
    assert reason == "tariff_not_ai"


def test_ai_tariff_within_quota(ai_tenant):
    ok, reason = quota_service.check_can_use_ai(ai_tenant)
    assert ok is True
    assert reason is None


def test_companies_quota_exceeded(db_session, ai_tenant):
    ai_tenant.ai_companies_processed_period = 100
    db_session.flush()
    ok, reason = quota_service.check_can_use_ai(ai_tenant)
    assert ok is False
    assert reason == "companies_quota_exceeded"


def test_tokens_quota_exceeded(db_session, ai_tenant):
    ai_tenant.ai_tokens_used_period = 1_000_001
    db_session.flush()
    ok, reason = quota_service.check_can_use_ai(ai_tenant)
    assert ok is False
    assert reason == "tokens_quota_exceeded"


def test_increment_companies(db_session, ai_tenant):
    quota_service.increment_companies(db_session, ai_tenant, 5)
    quota_service.increment_companies(db_session, ai_tenant, 3)
    db_session.flush()
    assert ai_tenant.ai_companies_processed_period == 8


def test_increment_tokens(db_session, ai_tenant):
    quota_service.increment_tokens(db_session, ai_tenant, 1500)
    quota_service.increment_tokens(db_session, ai_tenant, 500)
    db_session.flush()
    assert ai_tenant.ai_tokens_used_period == 2000


def test_increment_tokens_ignores_nonpositive(db_session, ai_tenant):
    quota_service.increment_tokens(db_session, ai_tenant, 0)
    quota_service.increment_tokens(db_session, ai_tenant, -1)
    assert ai_tenant.ai_tokens_used_period == 0


def test_reset_if_period_expired_resets(db_session, ai_tenant):
    """Старый period (40 дней назад) — сброс счётчиков."""
    ai_tenant.quota_period_start = datetime.now(timezone.utc) - timedelta(days=40)
    ai_tenant.ai_companies_processed_period = 50
    ai_tenant.ai_tokens_used_period = 500_000
    db_session.flush()

    was_reset = quota_service.reset_if_period_expired(db_session, ai_tenant)
    assert was_reset is True
    assert ai_tenant.ai_companies_processed_period == 0
    assert ai_tenant.ai_tokens_used_period == 0


def test_reset_if_period_not_expired_keeps(db_session, ai_tenant):
    """Свежий period — счётчики не сбрасываются."""
    ai_tenant.quota_period_start = datetime.now(timezone.utc) - timedelta(days=5)
    ai_tenant.ai_companies_processed_period = 50
    db_session.flush()

    was_reset = quota_service.reset_if_period_expired(db_session, ai_tenant)
    assert was_reset is False
    assert ai_tenant.ai_companies_processed_period == 50


def test_get_usage_basics(ai_tenant):
    ai_tenant.ai_companies_processed_period = 25
    ai_tenant.ai_tokens_used_period = 250_000
    usage = quota_service.get_usage(ai_tenant)
    assert usage.tariff_plan == "ai"
    assert usage.quota_companies == 100
    assert usage.used_companies == 25
    assert usage.companies_percent == 25
    assert usage.tokens_percent == 25
    assert usage.days_remaining is not None and usage.days_remaining <= 30


def test_get_usage_handles_zero_quota(simple_tenant):
    """Simple-тариф: квоты нулевые, percent должен быть 0 без zero-division."""
    usage = quota_service.get_usage(simple_tenant)
    assert usage.companies_percent == 0
    assert usage.tokens_percent == 0


def test_usage_warning_threshold(ai_tenant):
    ai_tenant.ai_companies_processed_period = 80
    usage = quota_service.get_usage(ai_tenant)
    assert quota_service.usage_warning_threshold(usage) is True

    ai_tenant.ai_companies_processed_period = 70
    usage = quota_service.get_usage(ai_tenant)
    assert quota_service.usage_warning_threshold(usage) is False


# --- Dev-whitelist bypass ----------------------------------------------


@pytest.fixture
def dev_tenant(db_session, monkeypatch):
    """Tenant в dev-whitelist — на любом тарифе должен пройти проверку."""
    from src import config
    monkeypatch.setattr(config, "DEV_USER_IDS", {999000}, raising=False)
    monkeypatch.setattr(config, "DEV_USERNAMES", set(), raising=False)
    t = Tenant(
        telegram_user_id=999000,
        tariff_plan=TariffPlan.TRIAL.value,   # ИИ обычно недоступен на Trial
        ai_quota_companies_monthly=0,
        ai_quota_tokens_monthly=0,
    )
    db_session.add(t)
    db_session.flush()
    return t


def test_dev_bypasses_tariff_check(dev_tenant):
    """Trial-tenant в dev-whitelist получает ok=True вместо tariff_not_ai."""
    ok, reason = quota_service.check_can_use_ai(dev_tenant)
    assert ok is True
    assert reason is None


def test_dev_bypasses_quota_exceeded(db_session, dev_tenant):
    """Dev не блокируется даже при «исчерпанной» квоте — у него её нет."""
    dev_tenant.tariff_plan = TariffPlan.PRO.value
    dev_tenant.ai_quota_companies_monthly = 10
    dev_tenant.ai_companies_processed_period = 100   # «исчерпано»
    db_session.flush()
    ok, reason = quota_service.check_can_use_ai(dev_tenant)
    assert ok is True
    assert reason is None
