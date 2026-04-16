# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the platform schema.

The platform schema stores cross-practice data: practice registry,
email-tenant mappings, and system config. Lives in the same Cloud SQL
instance as practice schemas but is not practice-scoped.

SaaS-specific models (subscriptions, phone numbers, product tiers)
live in saas_models.py.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailTenantMappingRow(PlatformBase):
    """Maps email → tenant_id for pre-auth tenant resolution.

    Replaces Firestore email_tenants collection.
    """

    __tablename__ = "email_tenant_mappings"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SetupTokenRow(PlatformBase):
    """Short-lived token to pass email from marketing signup to login page.

    Single-use, expires after 10 minutes. No PII in URL — just an opaque token.
    """

    __tablename__ = "setup_tokens"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemConfigRow(PlatformBase):
    __tablename__ = "system_config"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformUserRow(PlatformBase):
    __tablename__ = "users"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    picture: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    baa_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baa_version: Mapped[str | None] = mapped_column(String(10))
    baa_legal_name: Mapped[str | None] = mapped_column(String(255))
    baa_license_number: Mapped[str | None] = mapped_column(String(100))
    baa_license_state: Mapped[str | None] = mapped_column(String(2))
    baa_practice_name: Mapped[str | None] = mapped_column(String(255))
    baa_business_address: Mapped[str | None] = mapped_column(String(500))
    baa_full_text: Mapped[str | None] = mapped_column(Text)


class PlatformUserPreferencesRow(PlatformBase):
    __tablename__ = "user_preferences"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PlatformAllowedEmailRow(PlatformBase):
    __tablename__ = "allowed_emails"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    practice_id: Mapped[str | None] = mapped_column(String(128))
    added_by: Mapped[str] = mapped_column(String(255), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
