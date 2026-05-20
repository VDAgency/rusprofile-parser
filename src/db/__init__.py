"""Доступ к локальной БД (SQLite сейчас, PostgreSQL позже).

Хранит историю всех найденных компаний и запусков парсинга для
дедупликации. Multi-tenancy — `tenant_id` в каждой таблице, под
будущий SaaS.
"""

from src.db.session import Base, engine, SessionLocal, get_session, init_db
from src.db.models import (
    Tenant,
    Theme,
    Company,
    ParseRun,
    RunCompany,
    RunStatus,
    Source,
    AIProfile,
    AIStatus,
    TariffPlan,
    Subscription,
    Payment,
    SubscriptionStatus,
    PaymentStatus,
    BlockedReason,
)
from src.db.normalize import normalize_phone, format_phone_for_display
from src.db.dedup import (
    canonicalize_filters,
    filters_hash,
    humanize_filters,
    ensure_tenant,
    ensure_theme,
    load_known_keys,
    is_duplicate,
    upsert_company,
)

__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "get_session",
    "init_db",
    "Tenant",
    "Theme",
    "Company",
    "ParseRun",
    "RunCompany",
    "RunStatus",
    "Source",
    "AIProfile",
    "AIStatus",
    "TariffPlan",
    "Subscription",
    "Payment",
    "SubscriptionStatus",
    "PaymentStatus",
    "BlockedReason",
    "normalize_phone",
    "format_phone_for_display",
    "canonicalize_filters",
    "filters_hash",
    "humanize_filters",
    "ensure_tenant",
    "ensure_theme",
    "load_known_keys",
    "is_duplicate",
    "upsert_company",
]
