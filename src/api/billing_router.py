"""HTTP API для биллинга и подписок.

Endpoints:
- ``GET    /api/billing/plans``                  — список тарифов с ценами
- ``POST   /api/billing/create-payment``         — создать платёж (stub-режим)
- ``GET    /api/billing/payment-status/{id}``    — статус платежа (для polling)
- ``POST   /api/billing/cancel-subscription``    — отменить автопродление
- ``GET    /api/billing/payments``               — история платежей
- ``GET    /api/billing/subscription``           — детали активной подписки

ВРЕМЕННАЯ РЕАЛИЗАЦИЯ (Этап 3, пока нет эквайринга):
- ``create-payment`` отдаёт URL stub-страницы вместо ЮKassa.
- ``payment-status`` всегда возвращает ``pending`` (stub).
- Записи в БД ``payments`` не создаются — они появятся после
  подключения SDK.
- ``cancel-subscription`` и ``payments``/``subscription`` уже работают
  с реальными данными — клиент может отменить подписку, которую
  активировал админ вручную через CLI.

Все эндпоинты (кроме webhook'а, которого пока нет) требуют валидный
Telegram WebApp initData через ``auth_middleware``.
"""

from __future__ import annotations

import logging

from aiohttp import web
from sqlalchemy import select

from src.db import get_session
from src.db.models import (
    Payment,
    Subscription,
    TariffPlan,
    Tenant,
)
from src.services import payment_service, subscription_service

logger = logging.getLogger(__name__)


def _tenant_by_user(session, telegram_user_id: int) -> Tenant | None:
    return session.scalar(
        select(Tenant).where(Tenant.telegram_user_id == telegram_user_id)
    )


def _to_aware_iso(ts) -> str | None:
    if ts is None:
        return None
    from datetime import timezone as _tz
    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    return ts.isoformat()


# ─── GET /api/billing/plans ──────────────────────────────────────────────


async def get_plans(request: web.Request) -> web.Response:
    """Список тарифов с ценами для UI."""
    from src import config
    return web.json_response({
        "billing_configured": payment_service.is_billing_configured(),
        "currency": "RUB",
        "trial": {
            "days": config.TRIAL_DAYS,
            "parses": config.TRIAL_PARSES_LIMIT,
        },
        "plans": [
            {
                "id": TariffPlan.BASIC.value,
                "name": "Basic",
                "description": (
                    "Парсинг без ограничений, кросс-обогащение источников, "
                    "выгрузка в Sheets/Excel."
                ),
                "price_rub": config.BASIC_PRICE_RUB,
                "period_days": config.SUBSCRIPTION_PERIOD_DAYS,
                "has_ai": False,
            },
            {
                "id": TariffPlan.PRO.value,
                "name": "Pro",
                "description": (
                    "Всё из Basic + ИИ-квалификация компаний "
                    "по сайтам (квота 1000 компаний/мес)."
                ),
                "price_rub": config.PRO_PRICE_RUB,
                "period_days": config.SUBSCRIPTION_PERIOD_DAYS,
                "has_ai": True,
            },
        ],
    })


# ─── POST /api/billing/create-payment ────────────────────────────────────


async def create_payment(request: web.Request) -> web.Response:
    """Создаёт запрос на оплату. Stub-режим: возвращает URL заглушки.

    Body: ``{"tariff": "basic" | "pro"}``.

    После подключения ЮKassa здесь будет реальный ``createPayment``
    + запись в БД ``payments`` (status=pending). UI сможет polling'ить
    ``GET /api/billing/payment-status/{id}``.
    """
    user_id = request["user_id"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    tariff = (body.get("tariff") or "").strip().lower()
    if tariff not in (TariffPlan.BASIC.value, TariffPlan.PRO.value):
        return web.json_response(
            {
                "error": "invalid_tariff",
                "message": "tariff must be 'basic' or 'pro'",
            },
            status=400,
        )

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        tenant_id = tenant.id if tenant else None

    try:
        url = payment_service.create_stub_payment_url(
            tariff=tariff,
            tenant_id=tenant_id,
            base_url="/app/payment_stub.html",
        )
    except payment_service.PaymentNotConfiguredError as e:
        return web.json_response({"error": "payment_not_configured", "message": str(e)}, status=400)

    return web.json_response({
        "billing_configured": payment_service.is_billing_configured(),
        "confirmation_url": url,
        # В stub-режиме payment_id фейковый — UI не должен на него
        # polling'ить (нечего проверять). После реального SDK здесь
        # будет yookassa_payment_id.
        "payment_id": None,
        "status": "stub",
        "message": (
            "Сервис платежей ещё в разработке. После перехода по ссылке "
            "напишите в поддержку — мы активируем тариф вручную."
        ),
    })


# ─── GET /api/billing/payment-status/{id} ────────────────────────────────


async def get_payment_status(request: web.Request) -> web.Response:
    """В stub-режиме возвращает 404 — реального платежа нет."""
    payment_id = request.match_info.get("payment_id", "")
    # В реальном режиме — ищем в БД и возвращаем статус. Сейчас всегда
    # 404, чтобы UI понял что polling бессмыслен.
    return web.json_response(
        {
            "error": "payment_not_found",
            "message": (
                "В stub-режиме платежи не сохраняются. После подключения "
                "ЮKassa этот эндпоинт начнёт возвращать реальный статус."
            ),
            "payment_id": payment_id,
        },
        status=404,
    )


# ─── POST /api/billing/cancel-subscription ───────────────────────────────


async def cancel_subscription(request: web.Request) -> web.Response:
    """Отменяет автопродление активной подписки. Доступ сохраняется
    до ``expires_at``.
    """
    user_id = request["user_id"]
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if tenant is None:
            return web.json_response({"error": "tenant_not_found"}, status=404)
        active = subscription_service.get_active_subscription(session, tenant)
        if active is None:
            return web.json_response(
                {"error": "no_active_subscription"}, status=404,
            )
        subscription_service.cancel_subscription(session, active)
        # session.commit() произойдёт автоматически при выходе из get_session
        return web.json_response({
            "ok": True,
            "subscription_id": active.id,
            "auto_renew": False,
            "access_until": _to_aware_iso(active.expires_at),
        })


# ─── GET /api/billing/payments ───────────────────────────────────────────


async def list_payments(request: web.Request) -> web.Response:
    """История платежей tenant'а. В stub-режиме всегда пустая."""
    user_id = request["user_id"]
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if tenant is None:
            return web.json_response({"payments": []})
        rows = session.execute(
            select(Payment)
            .where(Payment.tenant_id == tenant.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(50)
        ).scalars().all()
        return web.json_response({
            "payments": [
                {
                    "id": p.id,
                    "amount_rub": p.amount_rub,
                    "currency": p.currency,
                    "description": p.description,
                    "status": p.yookassa_status,
                    "created_at": _to_aware_iso(p.created_at),
                    "paid_at": _to_aware_iso(p.paid_at),
                }
                for p in rows
            ],
        })


# ─── GET /api/billing/subscription ───────────────────────────────────────


async def get_subscription_detail(request: web.Request) -> web.Response:
    """Детали активной подписки + история подписок tenant'а."""
    user_id = request["user_id"]
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if tenant is None:
            return web.json_response({"active": None, "history": []})
        active = subscription_service.get_active_subscription(session, tenant)
        history = subscription_service.list_tenant_subscriptions(session, tenant)
        return web.json_response({
            "active": _sub_to_dict(active) if active else None,
            "history": [_sub_to_dict(s) for s in history],
        })


def _sub_to_dict(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "tariff_plan": sub.tariff_plan,
        "status": sub.status,
        "starts_at": _to_aware_iso(sub.starts_at),
        "expires_at": _to_aware_iso(sub.expires_at),
        "cancelled_at": _to_aware_iso(sub.cancelled_at),
        "auto_renew": sub.auto_renew,
        "price_rub": sub.price_rub,
        "currency": sub.currency,
    }


# ─── Регистрация в aiohttp Application ────────────────────────────────────


def register_billing_routes(app: web.Application) -> None:
    app.router.add_get("/api/billing/plans", get_plans)
    app.router.add_post("/api/billing/create-payment", create_payment)
    app.router.add_get(
        "/api/billing/payment-status/{payment_id}", get_payment_status,
    )
    app.router.add_post(
        "/api/billing/cancel-subscription", cancel_subscription,
    )
    app.router.add_get("/api/billing/payments", list_payments)
    app.router.add_get("/api/billing/subscription", get_subscription_detail)
