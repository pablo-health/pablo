# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the platform schema.

The platform schema stores cross-practice data: practice registry,
email-tenant mappings, and system config. Lives in the same Cloud SQL
instance as practice schemas but is not practice-scoped.

SaaS-specific models (subscriptions, phone numbers, product tiers)
live in saas_models.py.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import PLATFORM_SCHEMA


class PlatformBase(DeclarativeBase):
    """Base class for platform-schema ORM models."""

    __table_args__ = {"schema": PLATFORM_SCHEMA}


class PracticeRow(PlatformBase):
    __tablename__ = "practices"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(128), default="")
    product: Mapped[str] = mapped_column(String(20), default="pablo")
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)


class EmailTenantMappingRow(PlatformBase):
    """Maps email → tenant_id for pre-auth tenant resolution.

    Replaces Firestore email_tenants collection.
    """

    __tablename__ = "email_tenant_mappings"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)


class SystemConfigRow(PlatformBase):
    __tablename__ = "system_config"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str | None] = mapped_column(String(50))
