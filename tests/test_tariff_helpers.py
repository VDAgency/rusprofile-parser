"""Тесты helper-функций тарифной логики.

Без БД, без I/O — только проверка чистой логики на Tenant-объектах.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.db.models import BlockedReason, TariffPlan, Tenant
from src.services.tariff_helpers import (
    get_trial_snapshot,
    is_ai_available,
    is_developer,
    is_paid_plan,
    is_parsing_available,
    is_trial,
)


def _tenant(**overrides) -> Tenant:
    """Создаёт Tenant-объект (без БД) с дефолтами."""
    defaults = dict(
        telegram_user_id=42,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=10,
        parses_used_period=0,
        ai_quota_companies_monthly=0,
        ai_quota_tokens_monthly=0,
        ai_companies_processed_period=0,
        ai_tokens_used_period=0,
        is_blocked=False,
        blocked_reason=None,
        trial_started_at=None,
        trial_expires_at=None,
    )
    defaults.update(overrides)
    t = Tenant(**defaults)
    return t


# ─── is_paid_plan ────────────────────────────────────────────────────────


@pytest.mark.parametrize("plan,expected", [
    (TariffPlan.SIMPLE.value, True),    # legacy
    (TariffPlan.AI.value, True),        # legacy
    (TariffPlan.BASIC.value, True),
    (TariffPlan.PRO.value, True),
    (TariffPlan.TRIAL.value, False),
    (TariffPlan.TRIAL_EXPIRED.value, False),
])
def test_is_paid_plan(plan, expected):
    assert is_paid_plan(_tenant(tariff_plan=plan)) is expected


# ─── is_ai_available ──────────────────────────────────────────────────────


@pytest.mark.parametrize("plan,expected", [
    (TariffPlan.SIMPLE.value, False),
    (TariffPlan.AI.value, True),        # legacy: ai = pro
    (TariffPlan.BASIC.value, False),
    (TariffPlan.PRO.value, True),
    (TariffPlan.TRIAL.value, False),
    (TariffPlan.TRIAL_EXPIRED.value, False),
])
def test_is_ai_available(plan, expected):
    assert is_ai_available(_tenant(tariff_plan=plan)) is expected


def test_is_ai_unavailable_when_blocked():
    """Даже Pro-tenant с is_blocked=True не может использовать ИИ."""
    t = _tenant(tariff_plan=TariffPlan.PRO.value, is_blocked=True)
    assert is_ai_available(t) is False


# ─── is_trial ────────────────────────────────────────────────────────────


def test_is_trial_true_for_trial():
    assert is_trial(_tenant(tariff_plan=TariffPlan.TRIAL.value)) is True


def test_is_trial_false_for_basic():
    assert is_trial(_tenant(tariff_plan=TariffPlan.BASIC.value)) is False


# ─── is_parsing_available ────────────────────────────────────────────────


def test_basic_user_can_parse():
    t = _tenant(tariff_plan=TariffPlan.BASIC.value, trial_parses_left=0)
    res = is_parsing_available(t)
    assert res.ok is True
    assert res.reason is None


def test_pro_user_can_parse():
    t = _tenant(tariff_plan=TariffPlan.PRO.value, trial_parses_left=0)
    assert is_parsing_available(t).ok is True


def test_legacy_simple_can_parse():
    t = _tenant(tariff_plan=TariffPlan.SIMPLE.value, trial_parses_left=0)
    assert is_parsing_available(t).ok is True


def test_trial_with_days_and_parses_can_parse():
    t = _tenant(
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=5,
        trial_expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    assert is_parsing_available(t).ok is True


def test_trial_no_parses_left_blocked():
    t = _tenant(
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=0,
        trial_expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    res = is_parsing_available(t)
    assert res.ok is False
    assert res.reason == BlockedReason.TRIAL_EXPIRED_PARSES.value
    assert "парсинги" in res.message.lower()


def test_trial_days_expired_blocked():
    t = _tenant(
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=5,
        trial_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    res = is_parsing_available(t)
    assert res.ok is False
    assert res.reason == BlockedReason.TRIAL_EXPIRED_DAYS.value


def test_trial_expired_tariff_blocked():
    t = _tenant(tariff_plan=TariffPlan.TRIAL_EXPIRED.value)
    res = is_parsing_available(t)
    assert res.ok is False
    assert res.reason == BlockedReason.TRIAL_EXPIRED_DAYS.value


def test_is_blocked_flag_blocks_all():
    t = _tenant(
        tariff_plan=TariffPlan.PRO.value, is_blocked=True,
        blocked_reason=BlockedReason.SUBSCRIPTION_PAST_DUE.value,
    )
    res = is_parsing_available(t)
    assert res.ok is False
    assert res.reason == BlockedReason.SUBSCRIPTION_PAST_DUE.value
    assert "автосписание" in res.message.lower()


def test_is_blocked_without_reason_falls_back():
    t = _tenant(
        tariff_plan=TariffPlan.PRO.value, is_blocked=True,
        blocked_reason=None,
    )
    res = is_parsing_available(t)
    assert res.ok is False
    # Должен взять дефолтное сообщение блокировки.
    assert res.message is not None


# ─── get_trial_snapshot ──────────────────────────────────────────────────


def test_snapshot_for_non_trial_is_empty():
    snap = get_trial_snapshot(_tenant(tariff_plan=TariffPlan.PRO.value))
    assert snap.is_trial is False
    assert snap.days_left is None
    assert snap.parses_left is None


def test_snapshot_for_active_trial():
    now = datetime.now(timezone.utc)
    t = _tenant(
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=7,
        trial_started_at=now - timedelta(days=2),
        trial_expires_at=now + timedelta(days=5),
    )
    snap = get_trial_snapshot(t)
    assert snap.is_trial is True
    assert snap.parses_left == 7
    assert snap.days_left in (5, 6)   # граница округления


def test_snapshot_for_expired_trial_days_zero():
    now = datetime.now(timezone.utc)
    t = _tenant(
        tariff_plan=TariffPlan.TRIAL.value,
        trial_parses_left=3,
        trial_expires_at=now - timedelta(days=1),
    )
    snap = get_trial_snapshot(t)
    assert snap.days_left == 0


# ─── is_developer / dev-bypass ───────────────────────────────────────────


@pytest.fixture
def dev_whitelist(monkeypatch):
    """Подменяет DEV_USER_IDS/DEV_USERNAMES в config на детерм. наборы."""
    from src import config
    monkeypatch.setattr(config, "DEV_USER_IDS", {111222333}, raising=False)
    monkeypatch.setattr(config, "DEV_USERNAMES", {"vdagency"}, raising=False)


def test_is_developer_by_user_id(dev_whitelist):
    t = _tenant(telegram_user_id=111222333, tariff_plan=TariffPlan.TRIAL.value)
    assert is_developer(t) is True


def test_is_developer_by_username_case_insensitive(dev_whitelist):
    t = _tenant(
        telegram_user_id=999, username="VDAgency",
        tariff_plan=TariffPlan.TRIAL.value,
    )
    assert is_developer(t) is True


def test_is_developer_username_with_at_sign(dev_whitelist):
    """username с @ в начале — тоже валиден (на всякий случай)."""
    t = _tenant(telegram_user_id=999, username="@vdagency")
    assert is_developer(t) is True


def test_is_developer_false_for_random_user(dev_whitelist):
    t = _tenant(telegram_user_id=888, username="random_user")
    assert is_developer(t) is False


def test_is_developer_false_when_whitelist_empty(monkeypatch):
    from src import config
    monkeypatch.setattr(config, "DEV_USER_IDS", set(), raising=False)
    monkeypatch.setattr(config, "DEV_USERNAMES", set(), raising=False)
    t = _tenant(telegram_user_id=111222333, username="vdagency")
    assert is_developer(t) is False


def test_dev_ai_available_on_any_tariff(dev_whitelist):
    """Dev получает ИИ даже на Trial / TRIAL_EXPIRED."""
    for plan in (
        TariffPlan.TRIAL.value, TariffPlan.TRIAL_EXPIRED.value,
        TariffPlan.SIMPLE.value, TariffPlan.BASIC.value,
    ):
        t = _tenant(telegram_user_id=111222333, tariff_plan=plan)
        assert is_ai_available(t) is True, f"plan={plan}"


def test_dev_ai_available_even_when_blocked(dev_whitelist):
    """Dev игнорирует is_blocked (мы не должны блокировать самих себя)."""
    t = _tenant(
        telegram_user_id=111222333,
        tariff_plan=TariffPlan.TRIAL_EXPIRED.value,
        is_blocked=True,
        blocked_reason=BlockedReason.SUBSCRIPTION_BLOCKED.value,
    )
    assert is_ai_available(t) is True


def test_dev_parsing_available_when_trial_expired(dev_whitelist):
    """Dev на TRIAL_EXPIRED с просроченными датами — всё равно ok."""
    t = _tenant(
        telegram_user_id=111222333,
        tariff_plan=TariffPlan.TRIAL_EXPIRED.value,
        trial_parses_left=0,
        trial_expires_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    res = is_parsing_available(t)
    assert res.ok is True
    assert res.reason is None


def test_dev_parsing_available_when_blocked(dev_whitelist):
    t = _tenant(
        telegram_user_id=111222333,
        tariff_plan=TariffPlan.PRO.value,
        is_blocked=True,
        blocked_reason=BlockedReason.SUBSCRIPTION_BLOCKED.value,
    )
    assert is_parsing_available(t).ok is True


def test_dev_is_paid_plan(dev_whitelist):
    """Для UI Кабинета: dev считается paid даже на Trial."""
    t = _tenant(
        telegram_user_id=111222333, tariff_plan=TariffPlan.TRIAL.value,
    )
    assert is_paid_plan(t) is True
