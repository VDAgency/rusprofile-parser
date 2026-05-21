"""aiohttp HTTP API в одном процессе с aiogram-ботом.

Эндпойнты:
* GET  /api/history?limit=50      — список запусков пользователя.
* POST /api/runs/{run_id}/repush  — заново записать компании запуска
                                    в Sheets, вернуть sheet_url.
* GET  /api/runs/{run_id}/xlsx    — скачать Excel.
* GET  /api/healthz               — публичный, для мониторинга.

Все приватные эндпойнты валидируют Telegram WebApp initData,
переданный в заголовке ``X-Telegram-Init-Data`` (Mini App
прокидывает его в каждом запросе).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aiohttp import web
from sqlalchemy import select

from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.api.auth import get_user_id, get_username
from src.api.export_xlsx import export_run_to_xlsx
from src.config import (
    ALLOW_UNSAFE_USER_IDS,
    API_HOST,
    API_PORT,
    SHEET_HEADERS,
    YANDEX_SHEET_HEADERS,
    YANDEX_SHEET_NAME,
)
from src.db import (
    Company,
    ParseRun,
    RunCompany,
    Source,
    Tenant,
    format_phone_for_display,
    get_session,
)
from src.sheets.client import write_companies

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Middleware: валидация initData и подмена user_id в request
# ---------------------------------------------------------------------------


@web.middleware
async def auth_middleware(request: web.Request, handler: Callable):
    """Все /api/* эндпойнты требуют initData, кроме /api/healthz и /api/debug/me."""
    if request.path in ("/api/healthz", "/api/debug/me"):
        return await handler(request)

    # Принимаем initData как из заголовка, так и из query-параметра
    # `?init_data=...`. Telegram WebView на части устройств блокирует
    # кастомные заголовки (приходит preflight на OPTIONS, а CORS
    # отвергает) — поэтому фронт по умолчанию шлёт всё через query.
    init_data = (
        request.headers.get("X-Telegram-Init-Data", "")
        or request.query.get("init_data", "")
    )
    user_id = get_user_id(init_data)
    auth_mode = "initData"

    if not user_id:
        # Fallback: некоторые клиенты (Telegram Desktop под Windows)
        # не передают `tg.initData`. Принимаем заявленный user_id из
        # `X-Telegram-User-Id-Unsafe` ИЛИ из query-параметра `?uid=`,
        # **только** если он в whitelist (см. ALLOW_UNSAFE_USER_IDS в
        # .env). Query-параметр нужен потому, что некоторые WebView
        # фильтруют кастомные заголовки.
        unsafe_raw = (
            request.headers.get("X-Telegram-User-Id-Unsafe", "")
            or request.query.get("uid", "")
        )
        unsafe_source = (
            "header" if request.headers.get("X-Telegram-User-Id-Unsafe")
            else "query"
        )
        if unsafe_raw.isdigit():
            unsafe_id = int(unsafe_raw)
            if unsafe_id in ALLOW_UNSAFE_USER_IDS:
                user_id = unsafe_id
                auth_mode = "unsafe-whitelist"
                logger.warning(
                    "Auth fallback: user_id=%d из whitelist (источник=%s, initData не передана)",
                    user_id, unsafe_source,
                )
            else:
                logger.warning(
                    "Auth fallback отклонён: user_id=%d НЕ в whitelist (source=%s, size=%d)",
                    unsafe_id, unsafe_source, len(ALLOW_UNSAFE_USER_IDS),
                )
        elif unsafe_raw:
            logger.warning("Unsafe id не число: %r (source=%s)",
                           unsafe_raw[:50], unsafe_source)
        else:
            logger.warning(
                "Auth провалился: ни initData, ни header/query unsafe не пришли. "
                "UA=%r path=%s",
                request.headers.get("User-Agent", "")[:80],
                request.path,
            )

    if not user_id:
        return web.json_response(
            {"error": "unauthorized", "detail": "invalid Telegram initData"},
            status=401,
        )

    request["user_id"] = user_id
    request["username"] = get_username(init_data) if auth_mode == "initData" else None
    request["auth_mode"] = auth_mode
    return await handler(request)


# ---------------------------------------------------------------------------
# Debug-эндпойнт: пользователь может открыть его в Mini App, чтобы понять,
# что приходит. Никаких приватных данных не отдаёт — только метаданные
# initData (длину, наличие полей), без её содержимого.
# ---------------------------------------------------------------------------


async def debug_me(request: web.Request) -> web.Response:
    # Принимаем оба источника — header или query.
    init_data = (
        request.headers.get("X-Telegram-Init-Data", "")
        or request.query.get("init_data", "")
    )
    unsafe_raw = (
        request.headers.get("X-Telegram-User-Id-Unsafe", "")
        or request.query.get("uid", "")
    )
    parsed = None
    try:
        from src.api.auth import parse_init_data
        parsed = parse_init_data(init_data) if init_data else None
    except Exception as e:  # noqa: BLE001
        parsed = {"_error": str(e)}

    return web.json_response({
        "init_data_present": bool(init_data),
        "init_data_length": len(init_data),
        "init_data_valid": parsed is not None,
        "unsafe_user_id": unsafe_raw or None,
        "unsafe_user_id_in_whitelist": (
            unsafe_raw.isdigit() and int(unsafe_raw) in ALLOW_UNSAFE_USER_IDS
        ),
        "whitelist_size": len(ALLOW_UNSAFE_USER_IDS),
        "origin": request.headers.get("Origin", ""),
        "user_agent": request.headers.get("User-Agent", "")[:100],
    })


# ---------------------------------------------------------------------------
# Хелпер: tenant по user_id
# ---------------------------------------------------------------------------


def _tenant_by_user(session, telegram_user_id: int) -> Tenant | None:
    return session.scalar(
        select(Tenant).where(Tenant.telegram_user_id == telegram_user_id)
    )


# ---------------------------------------------------------------------------
# Эндпойнты
# ---------------------------------------------------------------------------


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def history(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"runs": []})

        rows = session.execute(
            select(ParseRun)
            .where(ParseRun.tenant_id == tenant.id)
            .order_by(ParseRun.started_at.desc())
            .limit(limit)
        ).scalars().all()

        # Соберём все theme_id одним запросом
        theme_ids = list({r.theme_id for r in rows})
        themes_by_id = {}
        if theme_ids:
            from src.db.models import Theme
            themes = session.execute(
                select(Theme).where(Theme.id.in_(theme_ids))
            ).scalars().all()
            themes_by_id = {t.id: t for t in themes}

        result = []
        for r in rows:
            theme = themes_by_id.get(r.theme_id)
            result.append({
                "id": r.id,
                "source": r.source,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "requested_new": r.requested_new,
                "total_new": r.total_new,
                "total_skipped": r.total_skipped,
                "sheet_url": r.sheet_url,
                "theme_title": theme.title if theme else "",
                "theme_filters": theme.filters_json if theme else {},
            })
    return web.json_response({"runs": result})


async def repush_to_sheets(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    try:
        run_id = int(request.match_info["run_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "bad run_id"}, status=400)

    # 1. Достаём компании запуска под транзакцией
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "no tenant"}, status=404)
        run = session.get(ParseRun, run_id)
        if not run or run.tenant_id != tenant.id:
            return web.json_response({"error": "run not found"}, status=404)
        # ВАЖНО: фильтруем только is_new=True — компании, которые
        # реально являются «новыми» именно для этого запуска. В
        # run_companies могут лежать и записи is_new=False (когда
        # компания после enrich оказалась дубликатом из другой темы),
        # их в Sheets выводить нельзя — иначе при перезаливке будут
        # показаны не те данные.
        rows = session.execute(
            select(Company)
            .join(RunCompany, RunCompany.company_id == Company.id)
            .where(RunCompany.run_id == run.id, RunCompany.is_new == True)  # noqa: E712
            .order_by(Company.id)
        ).scalars().all()
        run_source = run.source

    if not rows:
        return web.json_response({"error": "empty run"}, status=400)

    # 2. Вне сессии конвертируем в объекты с .to_row(), которые ждёт sheets-клиент.
    if run_source == Source.YANDEX_MAPS.value:
        from src.yandex_maps.parser import YandexPlace
        objs = []
        for c in rows:
            raw = c.raw_json or {}
            objs.append(YandexPlace(
                name=c.name or "",
                categories=c.okved or "",
                region=c.region or "",
                address=c.address or "",
                phone=format_phone_for_display(c.phone) if c.phone else "",
                site=c.site or "",
                rating=str(raw.get("rating") or ""),
                reviews_count=str(raw.get("reviews_count") or ""),
                hours=raw.get("hours") or "",
                coordinates=raw.get("coordinates") or "",
                yandex_url=raw.get("yandex_url") or "",
                parse_date=(c.last_seen_at.strftime("%d.%m.%Y")
                            if c.last_seen_at else ""),
            ))
        sheet_url = write_companies(
            objs, sheet_name=YANDEX_SHEET_NAME,
            headers=YANDEX_SHEET_HEADERS, replace=True,
        )
    else:
        from src.rusprofile.parser import Company as RusprofileCompany
        objs = []
        for c in rows:
            objs.append(RusprofileCompany(
                name=c.name or "",
                inn=c.inn or "",
                ogrn=c.ogrn or "",
                region=c.region or "",
                address=c.address or "",
                okved=c.okved or "",
                revenue=c.revenue or "",
                profit=c.profit or "",
                phone=format_phone_for_display(c.phone) if c.phone else "",
                email=c.email or "",
                site=c.site or "",
                status=c.status or "",
                parse_date=(c.last_seen_at.strftime("%d.%m.%Y")
                            if c.last_seen_at else ""),
            ))
        sheet_url = write_companies(objs, replace=True)

    # 3. Запоминаем последний sheet_url у запуска
    with get_session() as session:
        run = session.get(ParseRun, run_id)
        if run:
            run.sheet_url = sheet_url

    return web.json_response({"sheet_url": sheet_url, "exported": len(objs)})


async def download_xlsx(request: web.Request) -> web.Response:
    """Прямое скачивание Excel — для curl/браузера. Mini App не использует
    этот путь, потому что Telegram WebView блокирует binary download через
    `<a download>`. См. send_xlsx_to_chat.
    """
    user_id = request["user_id"]
    try:
        run_id = int(request.match_info["run_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "bad run_id"}, status=400)

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "no tenant"}, status=404)
        run = session.get(ParseRun, run_id)
        if not run or run.tenant_id != tenant.id:
            return web.json_response({"error": "run not found"}, status=404)

        try:
            data, filename = export_run_to_xlsx(session, run_id)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=404)

    return web.Response(
        body=data,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


async def send_xlsx_to_chat(request: web.Request) -> web.Response:
    """Генерирует Excel и отправляет файлом прямо в чат пользователя.

    Telegram WebView не умеет скачивать application/octet-stream через
    `<a download>` — JS падает на blob или не открывает диалог. Поэтому
    Mini App просит сервер отправить файл документом в чат через бота;
    пользователь увидит файл сообщением и сможет его открыть/скачать
    стандартным Telegram-способом.
    """
    user_id = request["user_id"]
    try:
        run_id = int(request.match_info["run_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "bad run_id"}, status=400)

    bot: Bot | None = request.app.get("bot")
    if bot is None:
        return web.json_response(
            {"error": "bot is not configured on the API server"},
            status=500,
        )

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "no tenant"}, status=404)
        run = session.get(ParseRun, run_id)
        if not run or run.tenant_id != tenant.id:
            return web.json_response({"error": "run not found"}, status=404)

        try:
            data, filename = export_run_to_xlsx(session, run_id)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=404)

    try:
        await bot.send_document(
            chat_id=user_id,
            document=BufferedInputFile(data, filename=filename),
            caption=(
                f"Запуск №{run_id}: {run.total_new} компаний "
                if False else f"Excel-файл по запуску №{run_id}"
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("send_document failed")
        return web.json_response(
            {"error": "telegram_send_failed", "detail": str(e)[:200]},
            status=502,
        )

    return web.json_response({
        "ok": True,
        "filename": filename,
        "size": len(data),
        "message": "Файл отправлен в чат с ботом",
    })


# ---------------------------------------------------------------------------
# Сборка приложения
# ---------------------------------------------------------------------------


def build_app(bot: Bot | None = None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    if bot is not None:
        app["bot"] = bot
    app.router.add_get("/api/healthz", healthz)
    app.router.add_get("/api/debug/me", debug_me)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/runs/{run_id}/repush", repush_to_sheets)
    app.router.add_get("/api/runs/{run_id}/xlsx", download_xlsx)
    app.router.add_post("/api/runs/{run_id}/send-xlsx", send_xlsx_to_chat)

    # Этап 2 v3 — ИИ-профили и квалификация.
    from src.api.profiles_router import register_profiles_routes
    from src.api.tariff_router import register_tariff_routes
    register_profiles_routes(app)
    register_tariff_routes(app)
    # Этап 3 — биллинг (stub до подключения эквайринга).
    from src.api.billing_router import register_billing_routes
    register_billing_routes(app)
    return app


async def start_api_server(bot: Bot | None = None) -> web.AppRunner:
    """Запускает aiohttp-сервер. Возвращает runner — его надо не забыть
    закрыть при остановке.

    ``bot`` — экземпляр aiogram-бота для отправки документов в чат
    (используется в /api/runs/{id}/send-xlsx).
    """
    app = build_app(bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    logger.info("HTTP API запущен на http://%s:%d", API_HOST, API_PORT)
    return runner
