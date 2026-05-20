"""Модели БД: Tenant / Theme / Company / ParseRun / RunCompany / AIProfile.

Multi-tenancy: каждая запись принадлежит ``tenant`` (Telegram-юзер).
Под SaaS: добавится таблица users-orgs, tenant.id будет тот же.

Этап 2 (v3) добавил:
- Тарифы и квоты в Tenant (Simple/AI).
- Я.Карты-сигналы и кросс-обогащение в Company.
- ИИ-поля в Company (статус, балл, комментарий, сигналы, hook, токены).
- Связь Company → AIProfile, ParseRun → AIProfile.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.session import Base


class Source(str, Enum):
    RUSPROFILE = "rusprofile"
    YANDEX_MAPS = "yandex_maps"


class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class TariffPlan(str, Enum):
    # Legacy (до Этапа 3) — приравниваются к BASIC / PRO «бессрочно»:
    SIMPLE = "simple"
    AI = "ai"
    # Этап 3:
    TRIAL = "trial"
    TRIAL_EXPIRED = "trial_expired"
    BASIC = "basic"
    PRO = "pro"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class BlockedReason(str, Enum):
    TRIAL_EXPIRED_DAYS = "trial_expired_days"
    TRIAL_EXPIRED_PARSES = "trial_expired_parses"
    SUBSCRIPTION_PAST_DUE = "subscription_past_due"
    SUBSCRIPTION_BLOCKED = "subscription_blocked"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"


class AIStatus(str, Enum):
    HOT = "hot"
    COLD = "cold"
    SKIP = "skip"
    UNKNOWN = "unknown"
    QUOTA_EXCEEDED = "quota_exceeded"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[str | None] = mapped_column(String(64))
    # Свой Sheet ID для SaaS-режима. Пока NULL — используем
    # глобальный из .env.
    google_sheet_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # ─── Тариф и квоты (Этап 2 v3) ────────────────────────────────────
    tariff_plan: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TariffPlan.SIMPLE.value,
        server_default="simple",
    )
    ai_quota_companies_monthly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ai_quota_tokens_monthly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ai_companies_processed_period: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ai_tokens_used_period: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    quota_period_start: Mapped[datetime | None] = mapped_column(DateTime)

    # ─── Этап 3 v1: Trial и блокировка ────────────────────────────────
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    trial_parses_left: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    parses_used_period: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            ondelete="SET NULL",
            name="fk_tenants_active_subscription_id",
        )
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    blocked_reason: Mapped[str | None] = mapped_column(String(50))

    themes: Mapped[list["Theme"]] = relationship(back_populates="tenant")
    companies: Mapped[list["Company"]] = relationship(back_populates="tenant")
    runs: Mapped[list["ParseRun"]] = relationship(back_populates="tenant")
    ai_profiles: Mapped[list["AIProfile"]] = relationship(back_populates="tenant")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="tenant",
        foreign_keys="Subscription.tenant_id",
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="tenant")


class Theme(Base):
    """Уникальная комбинация фильтров пользователя.

    Дедуп идёт в рамках темы: одна и та же компания в новой теме
    считается новой.
    """

    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    filters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    filters_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="themes")
    runs: Mapped[list["ParseRun"]] = relationship(back_populates="theme")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "filters_hash", name="uq_theme_tenant_hash"
        ),
        Index("ix_theme_tenant_last_used", "tenant_id", "last_used_at"),
    )


class AIProfile(Base):
    """Профиль квалификации — бриф клиента + извлечённые из него
    ICP, критерии и ключевые слова.

    Создаётся через Stage A (LLM-извлечение). Кешируется по `brief_hash`,
    чтобы повторное создание профиля с тем же текстом не вызывало LLM.
    """

    __tablename__ = "ai_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    brief_hash: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    icp_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    semantic_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    keywords_positive: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    keywords_negative: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    extraction_model: Mapped[str | None] = mapped_column(String(50))
    extraction_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    tenant: Mapped[Tenant] = relationship(back_populates="ai_profiles")

    __table_args__ = (
        Index("ix_ai_profiles_tenant_active", "tenant_id", "is_active"),
    )


class Company(Base):
    """Карточка компании, найденной парсером.

    Глобальный upsert: по реквизитам внутри tenant. Одна и та же
    компания в разных темах одного tenant'а — одна запись здесь;
    связь с темами — через ``run_companies``.
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    inn: Mapped[str | None] = mapped_column(String(12))
    ogrn: Mapped[str | None] = mapped_column(String(15))
    phone_normalized: Mapped[str | None] = mapped_column(String(20))

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    region: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    okved: Mapped[str | None] = mapped_column(Text)
    revenue: Mapped[str | None] = mapped_column(Text)
    profit: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    site: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # ─── Я.Карты-сигналы (Этап 2 v3) ──────────────────────────────────
    yandex_rating: Mapped[float | None] = mapped_column(Float)
    yandex_reviews_count: Mapped[int | None] = mapped_column(Integer)
    yandex_last_review_date: Mapped[date | None] = mapped_column(Date)
    yandex_hours_filled: Mapped[bool | None] = mapped_column(Boolean)
    yandex_coordinates_filled: Mapped[bool | None] = mapped_column(Boolean)
    yandex_url: Mapped[str | None] = mapped_column(String(500))
    yandex_operating_status: Mapped[str | None] = mapped_column(String(30))

    # ─── Кросс-обогащение источников ─────────────────────────────────
    cross_enriched_at: Mapped[datetime | None] = mapped_column(DateTime)
    cross_enrichment_source: Mapped[str | None] = mapped_column(String(20))
    cross_match_confidence: Mapped[float | None] = mapped_column(Float)

    # ─── ИИ-квалификация ─────────────────────────────────────────────
    ai_score: Mapped[int | None] = mapped_column(Integer)
    ai_status: Mapped[str | None] = mapped_column(String(20))
    ai_comment: Mapped[str | None] = mapped_column(Text)
    ai_signals: Mapped[list | None] = mapped_column(JSON)
    ai_hook: Mapped[str | None] = mapped_column(Text)
    ai_keyword_matches_positive: Mapped[list | None] = mapped_column(JSON)
    ai_keyword_matches_negative: Mapped[list | None] = mapped_column(JSON)
    ai_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ai_qualified_at: Mapped[datetime | None] = mapped_column(DateTime)
    ai_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="SET NULL")
    )

    tenant: Mapped[Tenant] = relationship(back_populates="companies")
    run_links: Mapped[list["RunCompany"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_company_tenant_inn", "tenant_id", "inn"),
        Index("ix_company_tenant_ogrn", "tenant_id", "ogrn"),
        Index("ix_company_tenant_phone", "tenant_id", "phone_normalized"),
        Index("ix_company_ai_status", "tenant_id", "ai_status"),
        Index("ix_company_yandex_url", "yandex_url"),
    )


class ParseRun(Base):
    __tablename__ = "parse_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RunStatus.RUNNING.value
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    requested_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_found_in_source: Mapped[int | None] = mapped_column(Integer)
    total_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sheet_url: Mapped[str | None] = mapped_column(Text)

    # ─── Этап 2 v3: квалификация ──────────────────────────────────────
    ai_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="SET NULL")
    )
    ai_qualify_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    ai_qualify_stats: Mapped[dict | None] = mapped_column(JSON)
    enable_cross_enrichment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    tenant: Mapped[Tenant] = relationship(back_populates="runs")
    theme: Mapped[Theme] = relationship(back_populates="runs")
    company_links: Mapped[list["RunCompany"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunCompany(Base):
    __tablename__ = "run_companies"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("parse_runs.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    run: Mapped[ParseRun] = relationship(back_populates="company_links")
    company: Mapped[Company] = relationship(back_populates="run_links")

    __table_args__ = (
        Index("ix_runcompany_company", "company_id"),
    )


# ---------------------------------------------------------------------------
# Этап 3 v1: Подписки и платежи
# ---------------------------------------------------------------------------


class Subscription(Base):
    """Подписка на платный тариф (Basic / Pro).

    Lifecycle: ACTIVE → (auto_renew=True → renew) или EXPIRED.
    Если автосписание не прошло — PAST_DUE на grace-период, затем
    EXPIRED + блок tenant'а.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="CASCADE",
            name="fk_subscriptions_tenant_id",
        ),
        nullable=False, index=True,
    )

    tariff_plan: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    # ЮKassa
    yookassa_payment_method_id: Mapped[str | None] = mapped_column(String(64))

    # Метаданные
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default="RUB"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    tenant: Mapped["Tenant"] = relationship(
        back_populates="subscriptions",
        foreign_keys=[tenant_id],
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="subscription")

    __table_args__ = (
        Index("ix_subscriptions_tenant_active", "tenant_id", "status"),
        Index("ix_subscriptions_expires", "expires_at", "status"),
    )


class Payment(Base):
    """Один платёж через ЮKassa.

    Может быть привязан к subscription (продление подписки или первичная
    оплата) или быть отдельным (например, разовый).
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="CASCADE",
            name="fk_payments_tenant_id",
        ),
        nullable=False, index=True,
    )
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "subscriptions.id", ondelete="SET NULL",
            name="fk_payments_subscription_id",
        )
    )

    # ЮKassa
    yookassa_payment_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    yookassa_idempotence_key: Mapped[str | None] = mapped_column(String(64))
    yookassa_status: Mapped[str] = mapped_column(String(20), nullable=False)

    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default="RUB"
    )
    description: Mapped[str | None] = mapped_column(Text)
    # Имя `payment_metadata` (а не `metadata`) — потому что `metadata`
    # зарезервировано в SQLAlchemy DeclarativeBase.
    payment_metadata: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    recurrent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)

    tenant: Mapped["Tenant"] = relationship(back_populates="payments")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="payments")
