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

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
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
    # Immutable after INSERT (trigger); requires schema_name matching
    # 'practice_pentest_%' (CHECK). Both enforced at the DB level.
    is_pentest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Per-practice audio retention window (days). DB CHECK enforces
    # 30..2555 (≈7y). Default 365 matches privacy-policy commitment.
    audio_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=365, server_default="365"
    )
    # Tenant offboarding schedule. NULL = active; non-NULL = scheduled
    # offboard at this instant. Cleared by NULL to cancel.
    offboard_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set inside the offboard transaction once the practice schema is
    # dropped. Acts as the "this practice is gone" post-condition;
    # admin queries filter on deleted_at IS NULL.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailTenantMappingRow(PlatformBase):
    """Maps email → tenant_id for pre-auth tenant resolution."""

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
    provider_type: Mapped[str | None] = mapped_column(String(32))
    security_guide_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_guide_version: Mapped[str | None] = mapped_column(String(20))
    onboarding_state: Mapped[str | None] = mapped_column(String(20))
    chat_quality_review_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chat_quality_review_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chat_quality_review_opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_notes_quality_review_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    session_notes_quality_review_opt_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    session_notes_quality_review_opt_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    quality_review_consent_prompted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class UserIdentityRow(PlatformBase):
    """Maps an external auth provider subject to a Pablo-internal user_id.

    Decouples Pablo's storage identity from any single auth provider's
    subject ID. Lets us migrate off Identity Platform later — or link
    multiple providers (Google + password) to the same user — without
    rewriting every user_id FK across every tenant schema.

    Composite PK (provider, subject_id) makes the (provider, subject)
    pair the natural lookup key. user_id is indexed (not unique) so
    one user can hold many provider identities.

    Subject IDs are bounded across providers: Firebase uid 28 chars,
    Auth0 ~40, Google sub 21 digits, Cognito sub 36. 64 covers them
    all with room to spare.
    """

    __tablename__ = "user_identities"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class CompanionDeviceRow(PlatformBase):
    """A user's enrolled native companion install (Mac / Windows desktop app).

    Created at first OAuth code-exchange. ``device_public_key_jwk`` is
    the JWK the companion generated in Secure Enclave (Mac) or TPM 2.0 /
    software-KSP fallback (Windows); ``jkt`` is the RFC 7638 thumbprint
    of that JWK, used as the lookup key by the DPoP middleware
    (THERAPY-6qtr).

    ``key_storage`` distinguishes hardware-backed keys (``hardware``,
    Secure Enclave / TPM) from software-backed fallback (``software``,
    Microsoft Software KSP) on Windows boxes without TPM 2.0. All Macs
    from 2018+ have Secure Enclave so Mac rows are always ``hardware``.

    No PHI: install_id is a random UUID; hostname_hash is the device's
    hostname run through a one-way hash on the client. Refresh tokens
    are not stored here — Firebase manages those.
    """

    __tablename__ = "companion_devices"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    install_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(f"{PLATFORM_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_public_key_jwk: Mapped[dict] = mapped_column(JSONB, nullable=False)
    jkt: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key_storage: Mapped[str] = mapped_column(String(16), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    os_version: Mapped[str | None] = mapped_column(String(64))
    hostname_hash: Mapped[str | None] = mapped_column(String(64))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformAuditLogRow(PlatformBase):
    __tablename__ = "platform_audit_logs"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_schema: Mapped[str | None] = mapped_column(String(128), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
