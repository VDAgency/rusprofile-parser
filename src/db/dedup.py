"""Сервис: дедуп компаний, темы, пользователи.

Тема — уникальная комбинация фильтров. Дедуп идёт в рамках темы.
Алгоритм гибридный: ИНН/ОГРН — главные ключи; телефон — fallback
**только** если у обеих сторон ИНН и ОГРН пустые (не склеиваем
холдинги с общей приёмной).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Company, ParseRun, RunCompany, Tenant, Theme
from src.db.normalize import normalize_phone


# ---------------------------------------------------------------------------
# Канонизация фильтров и хеш темы
# ---------------------------------------------------------------------------


def canonicalize_filters(filters: dict | None) -> dict:
    """Приводит фильтры к каноничному виду (для устойчивого хеша).

    * Ключи сортируются (через json.dumps(sort_keys=True) на выходе).
    * Списки сортируются и из них удаляются пустые/None.
    * Пустые строки и None — выкидываются.
    * Булевы False/0 — выкидываются (флажок не выставлен ≡ ключа нет).
    """
    if not filters:
        return {}

    out: dict = {}
    for k, v in filters.items():
        if k is None or k == "":
            continue
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
            out[k] = v
        elif isinstance(v, bool):
            if v:
                out[k] = True
        elif isinstance(v, (int, float)):
            if v != 0:
                out[k] = v
        elif isinstance(v, (list, tuple, set)):
            cleaned = sorted(
                {str(x).strip() for x in v if x not in (None, "")}
            )
            if cleaned:
                out[k] = cleaned
        elif isinstance(v, dict):
            sub = canonicalize_filters(v)
            if sub:
                out[k] = sub
        else:
            out[k] = v
    return out


def filters_hash(source: str, filters: dict | None) -> str:
    """SHA-256 от канонизированного `source + filters`."""
    payload = {
        "source": str(source).strip().lower(),
        "filters": canonicalize_filters(filters),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def humanize_filters(source: str, filters: dict | None) -> str:
    """Короткий человекочитаемый заголовок темы."""
    canon = canonicalize_filters(filters)
    parts: list[str] = []

    if source == "yandex_maps":
        region = canon.get("region")
        category = canon.get("category")
        if region:
            parts.append(str(region))
        if category:
            parts.append(str(category))
        return " · ".join(parts) if parts else "Яндекс.Карты"

    # rusprofile
    query = canon.get("query")
    if query:
        parts.append(f"\"{query}\"")
    region = canon.get("region")
    if region:
        if isinstance(region, list):
            parts.append("Регион: " + ", ".join(map(str, region[:2])))
        else:
            parts.append(f"Регион: {region}")
    okved = canon.get("okved")
    if okved:
        if isinstance(okved, list):
            head = ", ".join(map(str, okved[:3]))
            tail = f" +{len(okved) - 3}" if len(okved) > 3 else ""
            parts.append(f"ОКВЭД: {head}{tail}")
        else:
            parts.append(f"ОКВЭД: {okved}")

    return " · ".join(parts) if parts else "Все компании"


# ---------------------------------------------------------------------------
# Tenant / Theme upsert
# ---------------------------------------------------------------------------


def ensure_tenant(
    session: Session, telegram_user_id: int, username: str | None = None
) -> Tenant:
    tenant = session.scalar(
        select(Tenant).where(Tenant.telegram_user_id == telegram_user_id)
    )
    if tenant is None:
        # Этап 3: новый tenant получает Trial (7 дней / 10 парсингов).
        # Существующих tenant'ов миграция НЕ трогает — они остаются на
        # legacy `simple` / `ai` (бессрочно).
        from datetime import timedelta
        from src.config import TRIAL_DAYS, TRIAL_PARSES_LIMIT
        from src.db.models import TariffPlan

        now = datetime.now(timezone.utc)
        tenant = Tenant(
            telegram_user_id=telegram_user_id,
            username=username,
            tariff_plan=TariffPlan.TRIAL.value,
            trial_started_at=now,
            trial_expires_at=now + timedelta(days=TRIAL_DAYS),
            trial_parses_left=TRIAL_PARSES_LIMIT,
        )
        session.add(tenant)
        session.flush()
    elif username and tenant.username != username:
        tenant.username = username
    return tenant


def ensure_theme(
    session: Session,
    tenant: Tenant,
    source: str,
    filters: dict | None,
) -> Theme:
    fh = filters_hash(source, filters)
    theme = session.scalar(
        select(Theme).where(
            Theme.tenant_id == tenant.id,
            Theme.filters_hash == fh,
        )
    )
    if theme is None:
        theme = Theme(
            tenant_id=tenant.id,
            source=source,
            filters_hash=fh,
            filters_json=canonicalize_filters(filters),
            title=humanize_filters(source, filters),
        )
        session.add(theme)
        session.flush()
    else:
        theme.last_used_at = datetime.now(timezone.utc)
        # Title мог обновиться (например, добавили region) — переписываем,
        # это безопасно, т. к. он чисто отображательный.
        new_title = humanize_filters(source, filters)
        if new_title and theme.title != new_title:
            theme.title = new_title
    return theme


# ---------------------------------------------------------------------------
# Дедуп
# ---------------------------------------------------------------------------


@dataclass
class KnownKeys:
    inn: set[str]
    ogrn: set[str]
    phone: set[str]


def load_known_keys(session: Session, theme: Theme) -> KnownKeys:
    """Загружает ИНН/ОГРН/нормализованные телефоны компаний, которые
    уже встречались в этой теме (через run_companies).

    Один SQL вместо проверки каждой компании отдельно — O(N) вместо
    O(N×M). Возвращает `KnownKeys`, который потом обновляется в
    Python без походов в БД.
    """
    rows = session.execute(
        select(Company.inn, Company.ogrn, Company.phone_normalized)
        .join(RunCompany, RunCompany.company_id == Company.id)
        .join(ParseRun, ParseRun.id == RunCompany.run_id)
        .where(ParseRun.theme_id == theme.id)
        .distinct()
    ).all()

    inn = {r[0] for r in rows if r[0]}
    ogrn = {r[1] for r in rows if r[1]}
    phone = {r[2] for r in rows if r[2]}
    return KnownKeys(inn=inn, ogrn=ogrn, phone=phone)


def is_duplicate(
    *,
    inn: str | None,
    ogrn: str | None,
    phone_normalized: str | None,
    known: KnownKeys,
) -> bool:
    """Гибридный дедуп.

    1. Если ИНН известен — он решает.
    2. Если ОГРН известен — он решает.
    3. Если ИНН/ОГРН **обоих** нет — пробуем телефон.
    """
    if inn and inn in known.inn:
        return True
    if ogrn and ogrn in known.ogrn:
        return True
    if not inn and not ogrn:
        if phone_normalized and phone_normalized in known.phone:
            return True
    return False


def update_known(known: KnownKeys, company: Company) -> None:
    """Дописывает ключи свежеиспечённой компании в локальный набор."""
    if company.inn:
        known.inn.add(company.inn)
    if company.ogrn:
        known.ogrn.add(company.ogrn)
    if company.phone_normalized:
        known.phone.add(company.phone_normalized)


# ---------------------------------------------------------------------------
# Upsert компании внутри tenant'а
# ---------------------------------------------------------------------------


def _find_existing_company(
    session: Session,
    tenant_id: int,
    inn: str | None,
    ogrn: str | None,
    phone_norm: str | None,
) -> Company | None:
    """Ищет уже существующую компанию у tenant'а по реквизитам."""
    if inn:
        c = session.scalar(
            select(Company).where(
                Company.tenant_id == tenant_id, Company.inn == inn
            )
        )
        if c:
            return c
    if ogrn:
        c = session.scalar(
            select(Company).where(
                Company.tenant_id == tenant_id, Company.ogrn == ogrn
            )
        )
        if c:
            return c
    if phone_norm and not inn and not ogrn:
        c = session.scalar(
            select(Company).where(
                Company.tenant_id == tenant_id,
                Company.phone_normalized == phone_norm,
                Company.inn.is_(None),
                Company.ogrn.is_(None),
            )
        )
        if c:
            return c
    return None


def upsert_company(
    session: Session,
    tenant: Tenant,
    *,
    name: str,
    source: str,
    inn: str | None = None,
    ogrn: str | None = None,
    phone: str | None = None,
    region: str | None = None,
    address: str | None = None,
    okved: str | None = None,
    revenue: str | None = None,
    profit: str | None = None,
    email: str | None = None,
    site: str | None = None,
    status: str | None = None,
    raw: dict | None = None,
) -> tuple[Company, bool]:
    """Возвращает (company, created): если уже была — обновляет
    last_seen_at и пустые поля; если новая — создаёт.
    """
    phone_norm = normalize_phone(phone)
    existing = _find_existing_company(
        session,
        tenant_id=tenant.id,
        inn=(inn or None) or None,
        ogrn=(ogrn or None) or None,
        phone_norm=phone_norm,
    )

    if existing:
        # «Дозаливаем» поля, которых раньше не было.
        if not existing.phone and phone:
            existing.phone = phone
            existing.phone_normalized = phone_norm
        if not existing.email and email:
            existing.email = email
        if not existing.site and site:
            existing.site = site
        if not existing.address and address:
            existing.address = address
        if not existing.region and region:
            existing.region = region
        if not existing.okved and okved:
            existing.okved = okved
        if revenue and existing.revenue != revenue:
            existing.revenue = revenue
        if profit and existing.profit != profit:
            existing.profit = profit
        if status and existing.status != status:
            existing.status = status
        if raw:
            # Аккуратно мерджим: новые ключи добавляем, существующие
            # перезаписываем свежими значениями.
            existing.raw_json = {**(existing.raw_json or {}), **raw}
        existing.last_seen_at = datetime.now(timezone.utc)
        return existing, False

    company = Company(
        tenant_id=tenant.id,
        name=name,
        source=source,
        inn=inn or None,
        ogrn=ogrn or None,
        phone=phone or None,
        phone_normalized=phone_norm,
        region=region,
        address=address,
        okved=okved,
        revenue=revenue,
        profit=profit,
        email=email,
        site=site,
        status=status,
        raw_json=raw,
    )
    session.add(company)
    session.flush()
    return company, True
