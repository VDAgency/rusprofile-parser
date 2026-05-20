"""Тесты: новый tenant создаётся с Trial-полями.

Регрессионная страховка для Этапа 3: ensure_tenant выставляет
`tariff_plan='trial'`, `trial_started_at=now`,
`trial_expires_at=now+TRIAL_DAYS`, `trial_parses_left=TRIAL_PARSES_LIMIT`.

Существующие tenant'ы (созданные до Этапа 3) НЕ трогаются — они
остаются на legacy `simple`/`ai` или своём текущем тарифе.
"""

from datetime import datetime, timezone

from src.db.dedup import ensure_tenant
from src.db.models import TariffPlan, Tenant


def test_new_tenant_gets_trial(db_session):
    tenant = ensure_tenant(db_session, telegram_user_id=12345)
    assert tenant.tariff_plan == TariffPlan.TRIAL.value
    assert tenant.trial_started_at is not None
    assert tenant.trial_expires_at is not None
    assert tenant.trial_parses_left == 10  # default TRIAL_PARSES_LIMIT


def test_new_tenant_trial_expires_in_future(db_session):
    tenant = ensure_tenant(db_session, telegram_user_id=12345)
    expires = tenant.trial_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    assert expires > datetime.now(timezone.utc)


def test_existing_simple_tenant_not_touched(db_session):
    """Существующий tenant на 'simple' не должен переключаться на Trial
    при повторном вызове ensure_tenant."""
    t = Tenant(
        telegram_user_id=99999,
        tariff_plan=TariffPlan.SIMPLE.value,
    )
    db_session.add(t)
    db_session.flush()

    re_fetched = ensure_tenant(db_session, telegram_user_id=99999)
    assert re_fetched.tariff_plan == TariffPlan.SIMPLE.value
    assert re_fetched.trial_started_at is None
    assert re_fetched.trial_parses_left == 0


def test_existing_ai_tenant_not_touched(db_session):
    """Аналогично для legacy 'ai'."""
    t = Tenant(
        telegram_user_id=88888,
        tariff_plan=TariffPlan.AI.value,
    )
    db_session.add(t)
    db_session.flush()

    re_fetched = ensure_tenant(db_session, telegram_user_id=88888)
    assert re_fetched.tariff_plan == TariffPlan.AI.value


def test_ensure_tenant_updates_username(db_session):
    """Старое поведение — username обновляется при повторном вызове —
    не должно сломаться."""
    ensure_tenant(db_session, telegram_user_id=12345, username="old_name")
    t = ensure_tenant(db_session, telegram_user_id=12345, username="new_name")
    assert t.username == "new_name"
    # Trial-поля при этом не пересоздаются.
    assert t.trial_parses_left == 10
