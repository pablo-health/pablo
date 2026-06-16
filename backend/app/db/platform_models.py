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

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
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
    owner_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
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
    # Async-provisioning gate. ``in_progress`` means the platform row
    # exists but the per-tenant schema DDL hasn't finished yet -- the
    # auth path returns 503 for these so we don't query an empty
    # schema. ``ready`` is the default for pre-existing rows
    # (provisioned the old synchronous way) and the terminal state new
    # rows reach once the background ``provision_tenant`` task succeeds.
    # ``failed`` means the background task raised; operator intervention
    # required.  THERAPY-da7t (and the migration adding it,
    # a4f7e2c81b9d, in the same commit).
    provisioning_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ready", server_default="ready"
    )
    # Business address for the practice (set at professional-info onboarding step).
    address: Mapped[str | None] = mapped_column(String(500))
    # BAA snapshot — written once at acceptance time and immutable thereafter.
    # These are the legal record: who signed, under what credentials, on what text.
    baa_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baa_version: Mapped[str | None] = mapped_column(String(10))
    baa_legal_name: Mapped[str | None] = mapped_column(String(255))
    baa_license_number: Mapped[str | None] = mapped_column(String(100))
    baa_license_state: Mapped[str | None] = mapped_column(String(2))
    baa_practice_name: Mapped[str | None] = mapped_column(String(255))
    baa_business_address: Mapped[str | None] = mapped_column(String(500))
    baa_full_text: Mapped[str | None] = mapped_column(Text)


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

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    picture: Mapped[str | None] = mapped_column(Text)
    # Optional contact number, collected during onboarding. May be used
    # for account recovery or support; never a sole authentication factor.
    phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="approved")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Fast auth gate — kept on the user row so require_baa_acceptance avoids
    # a practice lookup on every PHI request. Written in sync with practice.baa_*.
    baa_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baa_version: Mapped[str | None] = mapped_column(String(10))
    legal_name: Mapped[str | None] = mapped_column(String(255))
    provider_type: Mapped[str | None] = mapped_column(String(32))
    security_guide_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_guide_version: Mapped[str | None] = mapped_column(String(20))
    onboarding_state: Mapped[str | None] = mapped_column(String(20))
    profile_basics_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlatformUserPreferencesRow(PlatformBase):
    __tablename__ = "user_preferences"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
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
        Uuid(as_uuid=False),
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


class LaunchIntentRow(PlatformBase):
    """A single-use launch intent for the web→companion session handoff.

    Created when a therapist clicks "Start Session" on the web dashboard;
    consumed when the desktop companion redeems it at ``/launch/redeem``.
    Bound to the issuing ``user_id`` and an ``appointment_id``; the
    redeem step re-verifies the redeeming token's user against this row.

    Only the SHA-256 hash of the opaque intent id is stored
    (``intent_hash``, the lookup key) — never the raw id, which leaves
    the server exactly once in the issue response. ``consumed_at``
    non-null marks the intent spent (single-use). ``expires_at`` is the
    authoritative 180s expiry; a periodic sweep / TTL backstop reclaims
    rows.

    No PHI: ``appointment_id`` is an opaque pointer; no patient data is
    stored here. Lives in the shared ``platform`` schema (no RLS) — the
    same scope as ``companion_devices``.
    """

    __tablename__ = "launch_intents"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    intent_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The FK to platform.users(id) is declared in the Alembic migration
    # (raw SQL), not here. ``platform_metadata.create_all`` runs at the
    # start of every alembic env bootstrap — before migrations — and an
    # ORM-level ForeignKey would make create_all emit the FK while
    # users.id is transiently ``varchar`` (e.g. mid down/up replay,
    # before c1d7e4a9f2b6 re-converts it to uuid), tripping a
    # uuid↔varchar mismatch. Keeping the constraint migration-only lets
    # create_all build the bare column and the migration add the FK once
    # users.id is uuid. ON DELETE CASCADE is preserved in the migration.
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        nullable=False,
        index=True,
    )
    appointment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformAuditLogRow(PlatformBase):
    __tablename__ = "platform_audit_logs"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Actor identifier as recorded — kept VARCHAR, not native uuid (same
    # capture-over-correctness rationale as audit_logs.user_id / resource_id):
    # platform/system actions may not carry a uuid4 actor, and the audit row
    # must still be writable.
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_schema: Mapped[str | None] = mapped_column(String(128), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


class Icd10CodeRow(PlatformBase):
    """Public-domain ICD-10-CM code catalog (US gov work, NCHS/CMS).

    Reference data shared across all practices: the diagnostic engine offers
    and validates determined codes against this catalog. Seeded from
    ``app.diagnostics.baseline`` (a curated subset for the bundled diagnoses);
    a managed deployment may seed the full catalog. See PABLO-6xj.
    """

    __tablename__ = "icd10_codes"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    category: Mapped[str | None] = mapped_column(String(80))


class DiagnosticDefinitionRow(PlatformBase):
    """A versioned diagnostic-criteria definition (the rubric as data).

    Global reference data: one copy in the platform schema, not per-tenant.
    ``params`` holds the criterion groups, gates, and ICD-10 options the single
    metadata-driven evaluator interprets (see ``app.diagnostics``). Definitions
    are data — adding a disorder or a new version is a row, not code. Seeded
    from ``app.diagnostics.baseline``. See PABLO-6xj.
    """

    __tablename__ = "diagnostic_definitions"
    # SQLAlchemy allows __table_args__ to be either a dict or a tuple-of-
    # constraints-plus-dict; PlatformBase annotates the dict-only shape, so the
    # tuple form (needed for the UniqueConstraint + Index) trips mypy here.
    __table_args__ = (  # type: ignore[assignment]
        UniqueConstraint("code", "version", name="uq_diagnostic_definitions_code_version"),
        Index("ix_diagnostic_definitions_code_active", "code", "active"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Selects the evaluator strategy (e.g. "criteria"). A closed vocabulary
    # implemented in code — not a stored expression language.
    evaluator_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # {criterion_groups:[...], gates:[...], icd10_options:[...]}
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    suggested_icd10: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PasskeyCredentialRow(PlatformBase):
    """A user's registered WebAuthn passkey — a phishing-resistant possession factor.

    One row per authenticator a user enrolls (phone/laptop platform
    authenticator, or a roaming hardware key) — a user may hold several.
    ``credential_id`` is the base64url credential id returned by the
    authenticator and is the natural lookup key on assertion, so it is the
    primary key (mirrors ``companion_devices.install_id``).

    Distinct from ``companion_devices`` by design, despite both storing a
    per-user device public key: a passkey is the *login factor* (verified
    during the WebAuthn ceremony), whereas a companion device key is a
    *post-login* binding for an already-authenticated desktop client. They
    are not interchangeable and must not share a table.

    ``public_key`` is the COSE-encoded public key bytes from registration
    verification (the assertion-verify path consumes COSE directly), which is
    why this stores raw bytes rather than the JWK-as-JSONB shape
    ``companion_devices`` uses. ``sign_count`` is the authenticator's
    signature counter for clone detection (platform authenticators may
    legitimately stay at 0). ``backup_eligible`` / ``backup_state`` are the
    WebAuthn BE/BS flags — whether the credential is a syncable multi-device
    passkey and whether it is currently synced; a device-bound credential
    that is the user's only factor is a recoverability signal for the UX.

    No PHI: authenticator metadata plus a user-chosen label only. Lives in
    the shared ``platform`` schema (no RLS), the same scope as
    ``companion_devices``. See PABLO-4jy.
    """

    __tablename__ = "passkey_credentials"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    credential_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # The FK to platform.users(id) is declared in the Alembic migration (raw
    # SQL), not here — same reason as LaunchIntentRow above:
    # ``PlatformBase.metadata.create_all`` runs before migrations at env
    # bootstrap, and an ORM-level ForeignKey would emit the FK while users.id
    # may be transiently varchar, tripping a uuid<->varchar mismatch.
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    transports: Mapped[list | None] = mapped_column(JSONB)
    aaguid: Mapped[str | None] = mapped_column(String(36))
    # WebAuthn attestation statement format ('packed'/'apple'/'fido-u2f'/'tpm'/
    # 'none') and whether its certificate chain validated to a curated trust
    # root. fmt is informational provenance; attestation_verified gates the
    # "trusted hardware" signal admin enforcement reads. See PABLO-f00.
    fmt: Mapped[str | None] = mapped_column(String(32))
    attestation_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    backup_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    backup_state: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    device_label: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyChallengeRow(PlatformBase):
    """A single-use WebAuthn ceremony challenge (registration or authentication).

    Created when the server issues ceremony options; consumed when the client
    returns the signed response. Only the SHA-256 hash of the challenge is
    stored (``challenge_hash``, the lookup key) — never the raw challenge,
    which leaves the server exactly once in the options response. Modeled on
    ``LaunchIntentRow``'s single-use store.

    ``consumed_at`` non-null marks the challenge spent (single-use).
    ``expires_at`` is the authoritative short expiry, re-checked server-side
    on finish; a periodic sweep / TTL backstop reclaims rows. ``user_id`` is
    nullable: a usernameless (resident-key) authentication ceremony has no
    bound user at begin time.

    No PHI. Shares the ``platform`` schema (no RLS) with the other auth
    tables. See PABLO-4jy.
    """

    __tablename__ = "passkey_challenges"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    challenge_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ceremony: Mapped[str] = mapped_column(String(16), nullable=False)
    # Bare user_id (FK in the migration, see PasskeyCredentialRow). Nullable for
    # usernameless authentication ceremonies.
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyBackupCodeRow(PlatformBase):
    """A single one-time account-recovery backup code (hashed).

    Layer-1 of the recovery model (``docs/security/account-recovery-procedure.md``
    and ``authentication-mfa-policy.md`` §6.4): a set is issued at first-passkey
    enrollment so a user who loses their authenticator can still get in
    self-service. One row per code.

    Only the SHA-256 hash of the code is stored — never the plaintext, which is
    shown to the user exactly once at issuance. Codes are high-entropy
    (``secrets``), so a fast one-way hash is sufficient (same rationale as
    ``PasskeyChallengeRow.challenge_hash``). ``consumed_at`` non-null marks a
    code spent (single-use); regenerating a set revokes the user's prior unused
    codes. A redeemed code is a *second* factor, never a standalone login.

    No PHI. Shared ``platform`` schema (no RLS), same scope as the other auth
    tables. See PABLO-e82.
    """

    __tablename__ = "passkey_backup_codes"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Bare user_id (FK declared in the migration, see PasskeyCredentialRow).
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
