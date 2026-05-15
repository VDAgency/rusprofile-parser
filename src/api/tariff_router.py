"""HTTP API для тарифа и ИИ-квалификации запусков.

Endpoints:
- ``GET  /api/tariff``                  — текущий тариф + квоты + использование
- ``POST /api/runs/{run_id}/qualify``   — запустить квалификацию вручную

Все требуют валидный Telegram WebApp initData (через auth_middleware).
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from sqlalchemy import select

from src.db import get_session
from src.db.models import ParseRun, Tenant
from src.services import quota_service

logger = logging.getLogger(__name__)


def _tenant_by_user(session, telegram_user_id: int) -> Tenant | None:
    return session.scalar(
        select(Tenant).where(Tenant.telegram_user_id == telegram_user_id)
    )


# ─── GET /api/tariff ─────────────────────────────────────────────────────


async def get_tariff(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if tenant is None:
            return web.json_response({
                "tariff_plan": "simple",
                "quotas": {"companies_monthly": 0, "tokens_monthly": 0},
                "usage_period": None,
                "usage_current": {
                    "companies_processed": 0, "tokens_used": 0,
                    "companies_percent": 0, "tokens_percent": 0,
                },
            })
        # Сбрасываем счётчики, если период истёк, чтобы клиент видел свежие.
        quota_service.reset_if_period_expired(session, tenant)
        usage = quota_service.get_usage(tenant)

    return web.json_response({
        "tariff_plan": usage.tariff_plan,
        "quotas": {
            "companies_monthly": usage.quota_companies,
            "tokens_monthly": usage.quota_tokens,
        },
        "usage_period": (
            {
                "started": usage.period_started_at.isoformat() if usage.period_started_at else None,
                "ends": usage.period_ends_at.isoformat() if usage.period_ends_at else None,
                "days_remaining": usage.days_remaining,
            } if usage.period_started_at else None
        ),
        "usage_current": {
            "companies_processed": usage.used_companies,
            "tokens_used": usage.used_tokens,
            "companies_percent": usage.companies_percent,
            "tokens_percent": usage.tokens_percent,
        },
    })


# ─── POST /api/runs/{run_id}/qualify ─────────────────────────────────────


async def qualify_run_endpoint(request: web.Request) -> web.Response:
    """Запускает qualify_run в фоне. Сразу возвращает ``{"status":"started"}``.

    Body: ``{"profile_id": 5, "force": false, "enable_cross_enrichment": true}``.
    Если профиль не задан и tenant на AI-тарифе — возвращает 400.
    """
    user_id = request["user_id"]
    try:
        run_id = int(request.match_info["run_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid_run_id"}, status=400)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    profile_id = body.get("profile_id")
    force = bool(body.get("force", False))
    enable_cross_enrichment = bool(body.get("enable_cross_enrichment", True))

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if tenant is None:
            return web.json_response({"error": "tenant_not_found"}, status=404)
        run = session.scalar(
            select(ParseRun).where(
                ParseRun.id == run_id,
                ParseRun.tenant_id == tenant.id,
            )
        )
        if run is None:
            return web.json_response({"error": "run_not_found"}, status=404)
        tenant_id = tenant.id
        tariff_plan = tenant.tariff_plan

    # На AI-тарифе профиль обязателен.
    if profile_id is None and tariff_plan == "ai":
        return web.json_response(
            {
                "error": "profile_required",
                "message": "Для AI-тарифа нужен ai_profile_id",
            },
            status=400,
        )

    # Запускаем в фоне через asyncio.create_task (тот же event loop, что aiogram/aiohttp).
    # Используем тот же helper, что и автозапуск из parse_service —
    # он сам поднимет Playwright + finder-ы для кросс-обогащения, если
    # enable_cross_enrichment=True.
    from src.services.parse_service import _trigger_qualify

    async def _runner():
        try:
            await _trigger_qualify(
                run_id=run_id, tenant_id=tenant_id, profile_id=profile_id,
                enable_cross_enrichment=enable_cross_enrichment,
            )
            # Сводку в Telegram отправляем по факту завершения.
            bot = request.app.get("bot")
            if bot is not None:
                # Перечитываем статистику из БД, т.к. _trigger_qualify
                # её не возвращает.
                from src.db import get_session
                from src.db.models import ParseRun
                with get_session() as session:
                    run = session.get(ParseRun, run_id)
                    stats_dict = (run.ai_qualify_stats or {}) if run else {}

                from types import SimpleNamespace
                stats = SimpleNamespace(
                    by_status=stats_dict.get("by_status", {}),
                    tokens_used_total=stats_dict.get("tokens_used_total", 0),
                    cost_rub_estimate=stats_dict.get("cost_rub_estimate", 0.0),
                )
                await _send_summary_to_bot(bot, user_id, run_id, stats)
        except Exception as e:  # noqa: BLE001
            logger.exception("qualify_run failed: %s", e)

    asyncio.create_task(_runner())
    return web.json_response({"status": "started", "run_id": run_id})


async def _send_summary_to_bot(bot, user_id: int, run_id: int, stats) -> None:
    """Сводное сообщение в чат пользователя."""
    by = stats.by_status or {}
    parts = [
        f"🔥 {by.get('hot', 0)} горячих",
        f"❄ {by.get('cold', 0)} холодных",
        f"⏭ {by.get('skip', 0)} пропущено",
        f"❓ {by.get('unknown', 0)} неопределено",
    ]
    if by.get("quota_exceeded"):
        parts.append(f"🚫 {by['quota_exceeded']} квота")

    text = (
        f"🤖 Квалификация запуска #{run_id} завершена.\n\n"
        + " | ".join(parts)
    )
    if stats.tokens_used_total:
        text += (
            f"\n\nТокенов: {stats.tokens_used_total} "
            f"(~{stats.cost_rub_estimate:.2f} ₽)"
        )
    try:
        await bot.send_message(user_id, text)
    except Exception as e:  # noqa: BLE001
        logger.warning("send_summary_to_bot failed: %s", e)


# ─── Регистрация в aiohttp Application ────────────────────────────────────


def register_tariff_routes(app: web.Application) -> None:
    app.router.add_get("/api/tariff", get_tariff)
    app.router.add_post("/api/runs/{run_id}/qualify", qualify_run_endpoint)
