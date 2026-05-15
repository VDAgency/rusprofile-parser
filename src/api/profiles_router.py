"""HTTP API для ИИ-профилей квалификации.

Endpoints:
- ``POST /api/profiles/extract``  — Stage A: бриф → извлечённый профиль (без сохранения)
- ``POST /api/profiles``           — сохранить профиль (после правок)
- ``GET  /api/profiles``           — список профилей tenant'а
- ``GET  /api/profiles/{id}``      — один профиль
- ``PATCH /api/profiles/{id}``     — правка имени / ключевых слов
- ``DELETE /api/profiles/{id}``    — soft delete

Все требуют валидный Telegram WebApp initData (через auth_middleware).
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web
from sqlalchemy import select

from src.ai.llm_client import AsyncLLMClient
from src.db import get_session
from src.db.models import Tenant
from src.services import profile_service as ps

logger = logging.getLogger(__name__)


def _tenant_by_user(session, telegram_user_id: int) -> Tenant | None:
    return session.scalar(
        select(Tenant).where(Tenant.telegram_user_id == telegram_user_id)
    )


def _ensure_tenant(session, telegram_user_id: int) -> Tenant:
    tenant = _tenant_by_user(session, telegram_user_id)
    if tenant is None:
        tenant = Tenant(telegram_user_id=telegram_user_id)
        session.add(tenant)
        session.flush()
    return tenant


def _profile_to_dict(p) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "brief": p.brief,
        "icp_description": p.icp_description,
        "semantic_criteria": list(p.semantic_criteria or []),
        "keywords_positive": list(p.keywords_positive or []),
        "keywords_negative": list(p.keywords_negative or []),
        "extraction_model": p.extraction_model,
        "extraction_tokens_used": p.extraction_tokens_used,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────


async def extract_profile(request: web.Request) -> web.Response:
    """POST /api/profiles/extract — Stage A.

    Body: ``{"brief": "...", "name": "..."}``.
    Возвращает извлечённый профиль БЕЗ сохранения. Клиент видит результат,
    правит ключевые слова, потом вызывает POST /api/profiles для записи.
    """
    user_id = request["user_id"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    brief = (body.get("brief") or "").strip()
    name = (body.get("name") or "").strip()
    if not brief:
        return web.json_response({"error": "brief_required"}, status=400)

    with get_session() as session:
        tenant = _ensure_tenant(session, user_id)
        client = AsyncLLMClient.from_env()
        data, cached = await ps.extract_or_get_cached(
            session=session, tenant=tenant, brief=brief, client=client,
        )

    if not data.ok:
        return web.json_response(
            {
                "error": data.error or "extraction_failed",
                "message": (
                    "Не удалось получить профиль от LLM. Проверьте AI_PROVIDER "
                    "и API-ключ в настройках сервера."
                ),
            },
            status=502 if data.error in ("no_llm_client", "prompt_file_missing") else 500,
        )

    return web.json_response({
        "name": name,
        "brief": brief,
        "extracted": {
            "icp_description": data.icp_description,
            "semantic_criteria": data.semantic_criteria,
            "keywords_positive": data.keywords_positive,
            "keywords_negative": data.keywords_negative,
        },
        "from_cache": cached is not None,
        "cached_profile_id": cached.id if cached else None,
        "tokens_used": data.extraction_tokens_used,
    })


async def create_profile(request: web.Request) -> web.Response:
    """POST /api/profiles — сохранить профиль (после правок клиента).

    Body: ``{"name", "brief", "icp_description", "semantic_criteria",
    "keywords_positive", "keywords_negative"}``.

    Если профиль с таким же brief_hash уже есть — обновляет его.
    """
    user_id = request["user_id"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    name = (body.get("name") or "").strip()
    brief = (body.get("brief") or "").strip()
    if not brief:
        return web.json_response({"error": "brief_required"}, status=400)

    from src.ai.profile_extractor import ExtractedProfile
    data = ExtractedProfile(
        icp_description=str(body.get("icp_description") or "").strip(),
        semantic_criteria=[
            str(x).strip() for x in (body.get("semantic_criteria") or []) if x
        ],
        keywords_positive=[
            str(x).strip().lower().replace("ё", "е")
            for x in (body.get("keywords_positive") or []) if x
        ],
        keywords_negative=[
            str(x).strip().lower().replace("ё", "е")
            for x in (body.get("keywords_negative") or []) if x
        ],
        extraction_model=str(body.get("extraction_model") or "").strip() or None,
        extraction_tokens_used=int(body.get("extraction_tokens_used") or 0),
    )

    with get_session() as session:
        tenant = _ensure_tenant(session, user_id)
        profile = ps.save_profile(
            session=session, tenant=tenant,
            name=name, brief=brief, data=data,
        )
        return web.json_response(_profile_to_dict(profile))


async def list_profiles_endpoint(request: web.Request) -> web.Response:
    """GET /api/profiles — список профилей."""
    user_id = request["user_id"]
    include_inactive = request.query.get("include_inactive", "").lower() in ("1", "true", "yes")
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"profiles": []})
        profiles = ps.list_profiles(session, tenant, include_inactive=include_inactive)
        return web.json_response({
            "profiles": [_profile_to_dict(p) for p in profiles],
        })


async def get_profile_endpoint(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    try:
        profile_id = int(request.match_info["profile_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid_id"}, status=400)
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "not_found"}, status=404)
        profile = ps.get_profile(session, tenant, profile_id)
        if not profile:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(_profile_to_dict(profile))


async def patch_profile(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    try:
        profile_id = int(request.match_info["profile_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid_id"}, status=400)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "not_found"}, status=404)
        profile = ps.get_profile(session, tenant, profile_id)
        if not profile:
            return web.json_response({"error": "not_found"}, status=404)
        ps.update_profile_keywords(
            session=session, profile=profile,
            keywords_positive=body.get("keywords_positive"),
            keywords_negative=body.get("keywords_negative"),
            name=body.get("name"),
        )
        return web.json_response(_profile_to_dict(profile))


async def delete_profile(request: web.Request) -> web.Response:
    user_id = request["user_id"]
    try:
        profile_id = int(request.match_info["profile_id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "invalid_id"}, status=400)
    with get_session() as session:
        tenant = _tenant_by_user(session, user_id)
        if not tenant:
            return web.json_response({"error": "not_found"}, status=404)
        profile = ps.get_profile(session, tenant, profile_id)
        if not profile:
            return web.json_response({"error": "not_found"}, status=404)
        ps.soft_delete(session, profile)
        return web.json_response({"ok": True})


# ─── Регистрация в aiohttp Application ────────────────────────────────────


def register_profiles_routes(app: web.Application) -> None:
    app.router.add_post("/api/profiles/extract", extract_profile)
    app.router.add_post("/api/profiles", create_profile)
    app.router.add_get("/api/profiles", list_profiles_endpoint)
    app.router.add_get("/api/profiles/{profile_id}", get_profile_endpoint)
    app.router.add_patch("/api/profiles/{profile_id}", patch_profile)
    app.router.add_delete("/api/profiles/{profile_id}", delete_profile)
