"""Модели БД: Tenant / Theme / Company / ParseRun / RunCompany.

Multi-tenancy: каждая запись принадлежит ``tenant`` (Telegram-юзер).
Под SaaS: добавится таблица users-orgs, tenant.id будет тот же.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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

    themes: Mapped[list["Theme"]] = relationship(back_populates="tenant")
    companies: Mapped[list["Company"]] = relationship(back_populates="tenant")
    runs: Mapped[list["ParseRun"]] = relationship(back_populates="tenant")


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

    tenant: Mapped[Tenant] = relationship(back_populates="companies")
    run_links: Mapped[list["RunCompany"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_company_tenant_inn", "tenant_id", "inn"),
        Index("ix_company_tenant_ogrn", "tenant_id", "ogrn"),
        Index("ix_company_tenant_phone", "tenant_id", "phone_normalized"),
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
