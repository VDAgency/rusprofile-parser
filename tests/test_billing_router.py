"""Smoke-тесты billing_router без поднятия aiohttp-сервера.

Вызываем handler-функции напрямую с моком request. Цель —
проверить bizlogic (планы, отказ для не-payable тарифа, отмена),
а не весь HTTP-стек.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api import billing_router as br
from src.db.models import (
    Subscription,
    SubscriptionStatus,
    TariffPlan,
    Tenant,
)


def _make_request(*, user_id: int = 42, body: dict | None = None,
                  match_info: dict | None = None):
    """Минимальный фейк aiohttp.Request — поддерживает __getitem__
    (req['user_id']), match_info, и async json()."""
    class _Req:
        def __init__(self):
            self._data = {"user_id": user_id}
            self.match_info = match_info or {}
            self._body = body

        def __getitem__(self, key):
            return self._data[key]

        def __setitem__(self, key, value):
            self._data[key] = value

        async def json(self):
            if self._body is None:
                raise ValueError("no body")
            return self._body

    return _Req()


@pytest.fixture
def trial_tenant(db_session):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    t = Tenant(
        telegram_user_id=42,
        tariff_plan=TariffPlan.TRIAL.value,
        trial_started_at=now,
        trial_expires_at=now + timedelta(days=7),
        trial_parses_left=10,
    )
    db_session.add(t)
    db_session.commit()
    return t


# ─── GET /api/billing/plans ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_plans_returns_two_tariffs():
    req = _make_request()
    resp = await br.get_plans(req)
    data = json.loads(resp.body.decode())
    plan_ids = {p["id"] for p in data["plans"]}
    assert plan_ids == {"basic", "pro"}
    assert data["billing_configured"] is False  # пока нет эквайринга
    assert "trial" in data
    assert data["trial"]["parses"] >= 1


@pytest.mark.anyio
async def test_get_plans_basic_has_no_ai():
    req = _make_request()
    resp = await br.get_plans(req)
    data = json.loads(resp.body.decode())
    basic = next(p for p in data["plans"] if p["id"] == "basic")
    pro = next(p for p in data["plans"] if p["id"] == "pro")
    assert basic["has_ai"] is False
    assert pro["has_ai"] is True


# ─── POST /api/billing/create-payment ────────────────────────────────────


@pytest.mark.anyio
async def test_create_payment_stub_for_basic(trial_tenant):
    req = _make_request(user_id=42, body={"tariff": "basic"})
    resp = await br.create_payment(req)
    data = json.loads(resp.body.decode())
    assert data["billing_configured"] is False
    assert data["status"] == "stub"
    assert data["confirmation_url"].startswith("/app/payment_stub.html?")
    assert "tariff=basic" in data["confirmation_url"]


@pytest.mark.anyio
async def test_create_payment_for_pro(trial_tenant):
    req = _make_request(user_id=42, body={"tariff": "pro"})
    resp = await br.create_payment(req)
    data = json.loads(resp.body.decode())
    assert "tariff=pro" in data["confirmation_url"]


@pytest.mark.anyio
async def test_create_payment_rejects_invalid_tariff(trial_tenant):
    req = _make_request(user_id=42, body={"tariff": "ultra"})
    resp = await br.create_payment(req)
    assert resp.status == 400
    data = json.loads(resp.body.decode())
    assert data["error"] == "invalid_tariff"


@pytest.mark.anyio
async def test_create_payment_rejects_trial_tariff(trial_tenant):
    """Trial не payable — отказ."""
    req = _make_request(user_id=42, body={"tariff": "trial"})
    resp = await br.create_payment(req)
    assert resp.status == 400


# ─── GET /api/billing/payment-status/{id} ────────────────────────────────


@pytest.mark.anyio
async def test_payment_status_always_404_in_stub():
    req = _make_request(user_id=42, match_info={"payment_id": "stub-123"})
    resp = await br.get_payment_status(req)
    assert resp.status == 404


# ─── POST /api/billing/cancel-subscription ───────────────────────────────


@pytest.mark.anyio
async def test_cancel_subscription_returns_404_without_active(trial_tenant):
    req = _make_request(user_id=42)
    resp = await br.cancel_subscription(req)
    assert resp.status == 404


@pytest.mark.anyio
async def test_cancel_subscription_sets_auto_renew_false(
    db_session, trial_tenant, monkeypatch,
):
    """Активируем подписку → отменяем → auto_renew=False, status остаётся ACTIVE."""
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    from src.services import subscription_service
    result = subscription_service.activate_subscription_manually(
        db_session, trial_tenant, tariff="pro",
    )
    db_session.commit()

    req = _make_request(user_id=42)
    resp = await br.cancel_subscription(req)
    assert resp.status == 200
    data = json.loads(resp.body.decode())
    assert data["ok"] is True
    assert data["auto_renew"] is False

    # И в БД
    db_session.refresh(result.subscription)
    assert result.subscription.auto_renew is False
    assert result.subscription.status == SubscriptionStatus.ACTIVE.value


# ─── GET /api/billing/payments ───────────────────────────────────────────


@pytest.mark.anyio
async def test_list_payments_empty_in_stub(trial_tenant):
    req = _make_request(user_id=42)
    resp = await br.list_payments(req)
    data = json.loads(resp.body.decode())
    assert data["payments"] == []


# ─── GET /api/billing/subscription ───────────────────────────────────────


@pytest.mark.anyio
async def test_subscription_detail_for_trial_user(trial_tenant):
    req = _make_request(user_id=42)
    resp = await br.get_subscription_detail(req)
    data = json.loads(resp.body.decode())
    assert data["active"] is None
    assert data["history"] == []


@pytest.mark.anyio
async def test_subscription_detail_after_activation(
    db_session, trial_tenant, monkeypatch,
):
    monkeypatch.setattr(
        "src.services.subscription_service._settings",
        lambda: {"period_days": 30, "grace_days": 5, "renewal_reminder_days": 3,
                 "basic_price": 990, "pro_price": 2990,
                 "pro_quota_companies": 1000, "pro_quota_tokens": 10_000_000},
    )
    from src.services import subscription_service
    subscription_service.activate_subscription_manually(
        db_session, trial_tenant, tariff="basic",
    )
    db_session.commit()

    req = _make_request(user_id=42)
    resp = await br.get_subscription_detail(req)
    data = json.loads(resp.body.decode())
    assert data["active"] is not None
    assert data["active"]["tariff_plan"] == "basic"
    assert data["active"]["auto_renew"] is True
    assert len(data["history"]) == 1
