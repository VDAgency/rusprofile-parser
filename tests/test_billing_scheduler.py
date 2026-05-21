"""Тесты scheduler-jobs биллинга.

Без реального APScheduler: вызываем функции напрямую и проверяем
эффекты в БД + моки бот.send_message.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.db.models import (
    BlockedReason,
    Subscription,
    SubscriptionStatus,
    TariffPlan,
    Tenant,
)
from src.services import billing_scheduler as bs


# ─── Фикстуры ─────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_trial(db_session):
    """Trial-tenant, истекает через 5 дней, 7 парсингов осталось."""
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=1001,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now - timedelta(days=2),
        trial_expires_at=now + timedelta(days=5),
        trial_parses_left=7,
    )
    db_session.add(t)
    db_session.commit()
    return t


@pytest.fixture
def expired_trial(db_session):
    """Trial, у которого истёк срок дней (но статус ещё trial)."""
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=1002,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now - timedelta(days=10),
        trial_expires_at=now - timedelta(days=3),
        trial_parses_left=5,
    )
    db_session.add(t)
    db_session.commit()
    return t


@pytest.fixture
def last_day_trial(db_session):
    """Trial, остался ровно 1 день."""
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=1003,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now - timedelta(days=6),
        trial_expires_at=now + timedelta(days=1, hours=2),
        trial_parses_left=8,
    )
    db_session.add(t)
    db_session.commit()
    return t


@pytest.fixture
def few_parses_trial(db_session):
    """Trial с 2 парсингами оставшимися."""
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=1004,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now - timedelta(days=2),
        trial_expires_at=now + timedelta(days=5),
        trial_parses_left=2,
    )
    db_session.add(t)
    db_session.commit()
    return t


# ─── check_trial_expirations ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_check_trial_expirations_marks_expired(db_session, expired_trial):
    bot = AsyncMock()
    stats = await bs.check_trial_expirations(bot=bot)
    db_session.commit()  # фикстура была в этой же сессии — обновим
    db_session.refresh(expired_trial)
    assert expired_trial.tariff_plan == TariffPlan.TRIAL_EXPIRED.value
    assert expired_trial.is_blocked is True
    assert expired_trial.blocked_reason == BlockedReason.TRIAL_EXPIRED_DAYS.value
    assert stats["expired"] >= 1
    bot.send_message.assert_called()


@pytest.mark.anyio
async def test_check_trial_expirations_skips_active(db_session, fresh_trial):
    bot = AsyncMock()
    await bs.check_trial_expirations(bot=bot)
    db_session.refresh(fresh_trial)
    assert fresh_trial.tariff_plan == TariffPlan.TRIAL.value
    assert fresh_trial.is_blocked is False


@pytest.mark.anyio
async def test_check_trial_expirations_works_without_bot(db_session, expired_trial):
    """Job без bot=None всё равно отрабатывает."""
    stats = await bs.check_trial_expirations(bot=None)
    db_session.refresh(expired_trial)
    assert expired_trial.tariff_plan == TariffPlan.TRIAL_EXPIRED.value
    assert stats["expired"] >= 1


# ─── check_subscription_expirations ───────────────────────────────────────


@pytest.mark.anyio
async def test_check_subscription_expirations_expires(db_session, fresh_trial):
    """Подписка ACTIVE, auto_renew=False, expires_at в прошлом → EXPIRED + блок."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=fresh_trial.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now - timedelta(days=30), expires_at=now - timedelta(hours=1),
        auto_renew=False,
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.commit()

    bot = AsyncMock()
    stats = await bs.check_subscription_expirations(bot=bot)
    db_session.refresh(sub)
    db_session.refresh(fresh_trial)
    assert sub.status == SubscriptionStatus.EXPIRED.value
    assert fresh_trial.is_blocked is True
    assert stats["expired"] >= 1


@pytest.mark.anyio
async def test_check_subscription_expirations_skips_auto_renew(db_session, fresh_trial):
    """Подписка с auto_renew=True и истёкшая — НЕ трогается (это работа
    try_recurrent_renewals)."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=fresh_trial.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now - timedelta(days=30), expires_at=now - timedelta(hours=1),
        auto_renew=True,
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.commit()

    await bs.check_subscription_expirations(bot=None)
    db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.ACTIVE.value


# ─── try_recurrent_renewals ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_try_recurrent_marks_past_due_when_stub(db_session, fresh_trial):
    """renew_subscription_stub всегда возвращает False → подписка
    помечается past_due, уведомление отправляется."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=fresh_trial.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now - timedelta(days=30),
        expires_at=now + timedelta(hours=12),
        auto_renew=True,
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.commit()

    bot = AsyncMock()
    stats = await bs.try_recurrent_renewals(bot=bot)
    db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.PAST_DUE.value
    assert stats["past_due"] >= 1
    bot.send_message.assert_called()


# ─── process_past_due ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_process_past_due_blocks_overdue(db_session, fresh_trial, monkeypatch):
    """PAST_DUE с истёкшим grace → block_after_grace."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=fresh_trial.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        starts_at=now - timedelta(days=40),
        expires_at=now - timedelta(days=10),  # grace=5, истёк
        auto_renew=True,
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.commit()

    bot = AsyncMock()
    stats = await bs.process_past_due(bot=bot)
    db_session.refresh(sub)
    db_session.refresh(fresh_trial)
    assert sub.status == SubscriptionStatus.EXPIRED.value
    assert fresh_trial.is_blocked is True
    assert stats["blocked"] >= 1


# ─── send_trial_reminders ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_reminders_last_day(db_session, last_day_trial):
    bot = AsyncMock()
    stats = await bs.send_trial_reminders(bot=bot)
    assert stats["reminded"] >= 1
    call = bot.send_message.call_args
    assert "завтра" in call.kwargs["text"].lower() or "пробный" in call.kwargs["text"].lower()


@pytest.mark.anyio
async def test_reminders_few_parses(db_session, few_parses_trial):
    bot = AsyncMock()
    stats = await bs.send_trial_reminders(bot=bot)
    assert stats["reminded"] >= 1
    call = bot.send_message.call_args
    assert "парсинг" in call.kwargs["text"].lower()


@pytest.mark.anyio
async def test_reminders_skips_healthy_trial(db_session, fresh_trial):
    """5 дней / 7 парсингов — никаких напоминаний."""
    bot = AsyncMock()
    stats = await bs.send_trial_reminders(bot=bot)
    assert stats["reminded"] == 0
    bot.send_message.assert_not_called()


# ─── register_billing_jobs (smoke) ────────────────────────────────────────


def test_register_billing_jobs_smoke():
    """Просто проверяем что регистрация не падает и добавляет 5 задач."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="UTC")
    bs.register_billing_jobs(scheduler, bot=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "check_trial_expirations",
        "check_subscription_expirations",
        "try_recurrent_renewals",
        "process_past_due",
        "send_trial_reminders",
    }
