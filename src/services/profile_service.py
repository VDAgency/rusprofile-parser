"""CRUD-сервис ИИ-профилей с кешированием по ``brief_hash``.

См. ТЗ v3, разделы 5/8/15.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai.profile_extractor import ExtractedProfile, extract_profile_from_brief
from src.ai.llm_client import AsyncLLMClient
from src.db.models import AIProfile, Tenant

logger = logging.getLogger(__name__)


def compute_brief_hash(brief: str) -> str:
    """SHA-1 от нормализованного брифа.

    Нормализация: trim, lowercase, схлопывание пробелов. Это нужно, чтобы
    «  Я продаю...   » и «Я продаю...» считались одним и тем же.
    """
    if not brief:
        return ""
    norm = " ".join(brief.lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def find_cached_profile(
    session: Session, tenant: Tenant, brief: str
) -> AIProfile | None:
    """Ищет активный профиль с таким же ``brief_hash`` у tenant'а."""
    h = compute_brief_hash(brief)
    if not h:
        return None
    return session.scalar(
        select(AIProfile).where(
            AIProfile.tenant_id == tenant.id,
            AIProfile.brief_hash == h,
            AIProfile.is_active == True,  # noqa: E712
        )
    )


async def extract_or_get_cached(
    *,
    session: Session,
    tenant: Tenant,
    brief: str,
    client: AsyncLLMClient | None,
) -> tuple[ExtractedProfile, AIProfile | None]:
    """Возвращает (data, cached_profile_or_None).

    Если по brief_hash уже есть профиль — берём его (без LLM-вызова).
    Иначе вызываем Stage A. Сохранение в БД делается в ``save_profile``
    (двухшаговый UX: сначала показываем клиенту, потом сохраняем).
    """
    cached = find_cached_profile(session, tenant, brief)
    if cached:
        logger.info(
            "Profile cache hit for tenant=%s brief_hash=%s (id=%s)",
            tenant.id, cached.brief_hash, cached.id,
        )
        data = ExtractedProfile(
            icp_description=cached.icp_description,
            semantic_criteria=list(cached.semantic_criteria or []),
            keywords_positive=list(cached.keywords_positive or []),
            keywords_negative=list(cached.keywords_negative or []),
            extraction_model=cached.extraction_model or "",
            extraction_tokens_used=0,  # из кеша — без новых трат
        )
        return data, cached

    if client is None:
        return ExtractedProfile(
            icp_description="", error="no_llm_client",
        ), None

    data = await extract_profile_from_brief(client=client, brief=brief)
    return data, None


def save_profile(
    *,
    session: Session,
    tenant: Tenant,
    name: str,
    brief: str,
    data: ExtractedProfile,
) -> AIProfile:
    """Создаёт новый ``AIProfile`` или обновляет существующий с тем же hash.

    Не коммитит — это зона вызывающего.
    """
    h = compute_brief_hash(brief)
    profile = find_cached_profile(session, tenant, brief)
    now = datetime.now(timezone.utc)

    if profile is None:
        profile = AIProfile(
            tenant_id=tenant.id,
            name=name.strip()[:200] or "Без названия",
            brief=brief,
            brief_hash=h,
            icp_description=data.icp_description,
            semantic_criteria=data.semantic_criteria,
            keywords_positive=data.keywords_positive,
            keywords_negative=data.keywords_negative,
            extraction_model=data.extraction_model or None,
            extraction_tokens_used=data.extraction_tokens_used,
            is_active=True,
        )
        session.add(profile)
    else:
        profile.name = name.strip()[:200] or profile.name
        profile.icp_description = data.icp_description or profile.icp_description
        if data.semantic_criteria:
            profile.semantic_criteria = data.semantic_criteria
        if data.keywords_positive:
            profile.keywords_positive = data.keywords_positive
        if data.keywords_negative:
            profile.keywords_negative = data.keywords_negative
        if data.extraction_model:
            profile.extraction_model = data.extraction_model
        if data.extraction_tokens_used:
            profile.extraction_tokens_used = data.extraction_tokens_used
        profile.is_active = True
        profile.updated_at = now

    session.flush()
    return profile


def list_profiles(
    session: Session, tenant: Tenant, *, include_inactive: bool = False
) -> list[AIProfile]:
    q = select(AIProfile).where(AIProfile.tenant_id == tenant.id)
    if not include_inactive:
        q = q.where(AIProfile.is_active == True)  # noqa: E712
    q = q.order_by(AIProfile.updated_at.desc())
    return list(session.scalars(q).all())


def get_profile(session: Session, tenant: Tenant, profile_id: int) -> AIProfile | None:
    return session.scalar(
        select(AIProfile).where(
            AIProfile.id == profile_id,
            AIProfile.tenant_id == tenant.id,
        )
    )


def update_profile_keywords(
    *,
    session: Session,
    profile: AIProfile,
    keywords_positive: list[str] | None = None,
    keywords_negative: list[str] | None = None,
    name: str | None = None,
) -> AIProfile:
    """Правка ключевых слов / имени из UI (Mini App)."""
    if keywords_positive is not None:
        profile.keywords_positive = [
            k.strip().lower().replace("ё", "е")
            for k in keywords_positive if k and k.strip()
        ]
    if keywords_negative is not None:
        profile.keywords_negative = [
            k.strip().lower().replace("ё", "е")
            for k in keywords_negative if k and k.strip()
        ]
    if name is not None and name.strip():
        profile.name = name.strip()[:200]
    profile.updated_at = datetime.now(timezone.utc)
    session.flush()
    return profile


def soft_delete(session: Session, profile: AIProfile) -> None:
    """is_active=False — профиль скрывается из списков, но остаётся в БД."""
    profile.is_active = False
    profile.updated_at = datetime.now(timezone.utc)
    session.flush()
