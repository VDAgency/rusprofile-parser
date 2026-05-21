"""Тесты lifecycle подписок.

Покрывают: ручную активацию (новая + продление + смена тарифа),
отмену, past_due, expire, помощников find_*. С реальной in-memory
БД через db_session (см. conftest).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.db.models import (
    BlockedReason,
    Subscription,
    SubscriptionStatus,
    TariffPlan,
    Tenant,
)
from src.services import subscription_service as sub_svc


@pytest.fixture
def trial_tenant(db_session):
    """Свежий tenant на Trial (как создаёт ensure_tenant)."""
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=42,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now,
        trial_expires_at=now + timedelta(days=7),
        trial_parses_left=10,
    )
    db_session.add(t)
    db_session.flush()
    return t


# ─── activate_subscription_manually: новая ────────────────────────────────


def test_activate_creates_new_subscription(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro", months=1,
        notes="Перевод по СБП",
    )
    assert result.created_new is True
    assert result.subscription.tariff_plan == TariffPlan.PRO.value
    assert result.subscription.status == SubscriptionStatus.ACTIVE.value
    assert result.subscription.price_rub == 2990
    assert result.subscription.notes == "Перевод по СБП"
    assert trial_tenant.tariff_plan == TariffPlan.PRO.value
    assert trial_tenant.active_subscription_id == result.subscription.id
    assert trial_tenant.is_blocked is False


def test_activate_rejects_invalid_tariff(db_session, trial_tenant):
    with pytest.raises(ValueError, match="basic.+pro"):
        sub_svc.activate_subscription_manually(
            db_session, trial_tenant, tariff="trial",
        )


def test_activate_rejects_zero_months(db_session, trial_tenant):
    with pytest.raises(ValueError, match="months"):
        sub_svc.activate_subscription_manually(
            db_session, trial_tenant, tariff="basic", months=0,
        )


def test_activate_clears_blocked_flags(db_session, trial_tenant, monkeypatch):
    """Если tenant был заблокирован (например, trial истёк) — активация
    подписки разблокирует."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    trial_tenant.is_blocked = True
    trial_tenant.blocked_reason = BlockedReason.TRIAL_EXPIRED_DAYS.value
    db_session.flush()

    sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    assert trial_tenant.is_blocked is False
    assert trial_tenant.blocked_reason is None


def test_activate_pro_sets_ai_quotas(db_session, trial_tenant, monkeypatch):
    """Pro-активация должна выставить квоту ИИ из config."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1500, "pro_quota_tokens": 15_000_000},
    )
    sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro",
    )
    assert trial_tenant.ai_quota_companies_monthly == 1500
    assert trial_tenant.ai_quota_tokens_monthly == 15_000_000


def test_activate_basic_zeros_ai_quotas(db_session, trial_tenant, monkeypatch):
    """Basic-активация обнуляет квоты ИИ — ИИ не входит в тариф."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    trial_tenant.ai_quota_companies_monthly = 500   # вдруг был остаток
    trial_tenant.ai_quota_tokens_monthly = 5_000_000
    db_session.flush()

    sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    assert trial_tenant.ai_quota_companies_monthly == 0
    assert trial_tenant.ai_quota_tokens_monthly == 0


def test_activate_resets_period_counters(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    trial_tenant.parses_used_period = 50
    trial_tenant.ai_companies_processed_period = 100
    trial_tenant.ai_tokens_used_period = 500_000
    db_session.flush()

    sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro",
    )
    assert trial_tenant.parses_used_period == 0
    assert trial_tenant.ai_companies_processed_period == 0
    assert trial_tenant.ai_tokens_used_period == 0
    assert trial_tenant.quota_period_start is not None


# ─── activate_subscription_manually: продление ────────────────────────────


def test_activate_extends_same_tariff(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    first = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro", months=1,
    )
    first_expires = first.subscription.expires_at

    second = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro", months=2,
        notes="Продление на 2 мес",
    )
    assert second.created_new is False
    assert second.subscription.id == first.subscription.id
    # +60 дней
    expected_delta = timedelta(days=60)
    actual_delta = second.subscription.expires_at - first_expires
    assert abs(actual_delta - expected_delta) < timedelta(seconds=2)
    # notes склеены
    assert "Продление на 2 мес" in (second.subscription.notes or "")


def test_activate_different_tariff_cancels_old(db_session, trial_tenant, monkeypatch):
    """Был Basic — клиент хочет Pro. Старая подписка отменяется, создаётся
    новая."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    first = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    second = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro",
    )
    assert second.created_new is True
    assert second.subscription.id != first.subscription.id
    # Старая получила CANCELLED
    db_session.refresh(first.subscription)
    assert first.subscription.status == SubscriptionStatus.CANCELLED.value
    assert first.subscription.cancelled_at is not None
    # Tenant перешёл на Pro
    assert trial_tenant.tariff_plan == TariffPlan.PRO.value


# ─── get_active_subscription ──────────────────────────────────────────────


def test_get_active_returns_none_for_trial_tenant(db_session, trial_tenant):
    assert sub_svc.get_active_subscription(db_session, trial_tenant) is None


def test_get_active_returns_subscription(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    active = sub_svc.get_active_subscription(db_session, trial_tenant)
    assert active is not None
    assert active.id == result.subscription.id


def test_get_active_ignores_expired(db_session, trial_tenant):
    """Если status='active', но expires_at в прошлом — не отдаём."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    sub = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=past - timedelta(days=30),
        expires_at=past,
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.flush()
    assert sub_svc.get_active_subscription(db_session, trial_tenant) is None


def test_get_active_ignores_past_due(db_session, trial_tenant):
    sub = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        starts_at=datetime.now(timezone.utc) - timedelta(days=10),
        expires_at=datetime.now(timezone.utc) + timedelta(days=5),
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.flush()
    assert sub_svc.get_active_subscription(db_session, trial_tenant) is None


# ─── cancel_subscription ──────────────────────────────────────────────────


def test_cancel_keeps_access_until_expires(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro",
    )
    sub_svc.cancel_subscription(db_session, result.subscription)
    # Status остаётся ACTIVE
    assert result.subscription.status == SubscriptionStatus.ACTIVE.value
    assert result.subscription.auto_renew is False
    assert result.subscription.cancelled_at is not None
    # И get_active всё ещё возвращает её
    assert sub_svc.get_active_subscription(db_session, trial_tenant) is not None


def test_cancel_is_idempotent_for_already_cancelled(db_session, trial_tenant):
    sub = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.BASIC.value,
        status=SubscriptionStatus.CANCELLED.value,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        price_rub=990,
    )
    db_session.add(sub)
    db_session.flush()
    # Повторный вызов не должен ломать
    sub_svc.cancel_subscription(db_session, sub)
    assert sub.status == SubscriptionStatus.CANCELLED.value


# ─── mark_past_due + block_after_grace ────────────────────────────────────


def test_mark_past_due_changes_status(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    sub_svc.mark_past_due(db_session, result.subscription)
    assert result.subscription.status == SubscriptionStatus.PAST_DUE.value
    # Tenant ещё не заблокирован — даём grace
    assert trial_tenant.is_blocked is False


def test_block_after_grace_blocks_tenant(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    sub_svc.block_after_grace(db_session, result.subscription, trial_tenant)
    assert result.subscription.status == SubscriptionStatus.EXPIRED.value
    assert trial_tenant.is_blocked is True
    assert trial_tenant.blocked_reason == BlockedReason.SUBSCRIPTION_BLOCKED.value


def test_expire_subscription_blocks_tenant(db_session, trial_tenant, monkeypatch):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    result = sub_svc.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    sub_svc.expire_subscription(db_session, result.subscription, trial_tenant)
    assert result.subscription.status == SubscriptionStatus.EXPIRED.value
    assert trial_tenant.is_blocked is True
    assert trial_tenant.tariff_plan == TariffPlan.TRIAL_EXPIRED.value


# ─── find_expiring_soon / find_past_due_overdue ───────────────────────────


def test_find_expiring_soon(db_session, trial_tenant):
    now = datetime.now(timezone.utc)
    # Активная, истекает завтра — попадёт
    sub1 = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now - timedelta(days=29), expires_at=now + timedelta(days=1),
        price_rub=2990,
    )
    # Активная, истекает через 10 дней — НЕ попадёт (within=3)
    sub2 = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.BASIC.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=now - timedelta(days=20), expires_at=now + timedelta(days=10),
        price_rub=990,
    )
    # PAST_DUE — НЕ попадёт (только ACTIVE)
    sub3 = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        starts_at=now - timedelta(days=30), expires_at=now + timedelta(days=1),
        price_rub=2990,
    )
    db_session.add_all([sub1, sub2, sub3])
    db_session.flush()

    found = sub_svc.find_expiring_soon(db_session, within_days=3)
    ids = {s.id for s in found}
    assert sub1.id in ids
    assert sub2.id not in ids
    assert sub3.id not in ids


def test_find_past_due_overdue(db_session, trial_tenant):
    now = datetime.now(timezone.utc)
    # PAST_DUE, истёк 10 дней назад → grace 5 дней превышен → попадёт
    sub_overdue = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        starts_at=now - timedelta(days=40), expires_at=now - timedelta(days=10),
        price_rub=2990,
    )
    # PAST_DUE, истёк 2 дня назад → ещё в grace → НЕ попадёт
    sub_in_grace = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.BASIC.value,
        status=SubscriptionStatus.PAST_DUE.value,
        starts_at=now - timedelta(days=32), expires_at=now - timedelta(days=2),
        price_rub=990,
    )
    db_session.add_all([sub_overdue, sub_in_grace])
    db_session.flush()

    found = sub_svc.find_past_due_overdue(db_session, grace_days=5)
    ids = {s.id for s in found}
    assert sub_overdue.id in ids
    assert sub_in_grace.id not in ids


# ─── renew_subscription_stub ──────────────────────────────────────────────


def test_renew_stub_returns_false(db_session, trial_tenant):
    sub = Subscription(
        tenant_id=trial_tenant.id,
        tariff_plan=TariffPlan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        price_rub=2990,
    )
    db_session.add(sub)
    db_session.flush()
    # Заглушка всегда возвращает False — это поведение мы регрессионно
    # проверяем, чтобы после подключения ЮKassa не забыть переделать.
    assert sub_svc.renew_subscription_stub(db_session, sub) is False


# ─── list_tenant_subscriptions ────────────────────────────────────────────


def test_list_tenant_subscriptions_orders_by_created_desc(
    db_session, trial_tenant, monkeypatch,
):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    # Создаём две подписки (вторая поверх первой → первая отменится)
    sub_svc.activate_subscription_manually(db_session, trial_tenant, tariff="basic")
    sub_svc.activate_subscription_manually(db_session, trial_tenant, tariff="pro")
    rows = sub_svc.list_tenant_subscriptions(db_session, trial_tenant)
    assert len(rows) == 2
    # Свежая (Pro) — первая
    assert rows[0].tariff_plan == TariffPlan.PRO.value
    assert rows[1].tariff_plan == TariffPlan.BASIC.value
