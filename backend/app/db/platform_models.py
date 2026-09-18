# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the platform schema.

The platform schema stores cross-practice data: practice registry,
email-tenant mappings, and system config. Lives in the same Cloud SQL
instance as practice schemas but is not practice-scoped.

SaaS-specific models (subscriptions, phone numbers, product tiers)
live in saas_models.py.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models.enums import PracticeEdition
from . import PLATFORM_SCHEMA


class PlatformBase(DeclarativeBase):
    """Base class for platform-schema ORM models."""

    __table_args__ = {"schema": PLATFORM_SCHEMA}


class PracticeRow(PlatformBase):
    __tablename__ = "practices"

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
    # Practice phone number (set at professional-info onboarding step, or later
    # via the Profile settings page). No format validation at this layer.
    phone: Mapped[str | None] = mapped_column(String(50))
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
    # What kind of operator this practice is — distinct from ``product``,
    # which is the SKU (always "pablo" today). Everything downstream
    # (patients, appointments, notes, charts) currently assumes
    # THERAPIST; PERSONAL marks a non-clinical operator so those
    # surfaces can branch on a declared fact instead of inferring one
    # from empty tables. String + CHECK, not a native enum, so adding an
    # edition later is a constraint swap, not an ``ALTER TYPE``. See
    # :class:`app.models.enums.PracticeEdition` for the Python-side type.
    edition: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PracticeEdition.THERAPIST.value,
        server_default=PracticeEdition.THERAPIST.value,
    )

    # PlatformBase annotates __table_args__ as the dict-only shape; the
    # tuple form (needed for the CheckConstraint) trips mypy here, same
    # as DiagnosticDefinitionRow below.
    #
    # Everything below the edition CHECK already exists in every database and
    # did NOT exist here, because the platform schema had no migration chain of
    # its own: it was built by ``create_all`` from these models, and evolved by
    # raw SQL in the *tenant* chain, so anything the raw SQL added was invisible
    # to the model that supposedly described the table. Declaring them changes no
    # DDL — the platform chain's baseline already carries them, captured — it
    # makes ``alembic -n platform check`` able to pass, which is what turns
    # "models and schema agree" into something CI can assert. See PABLO-k7it.
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            "edition IN ('therapist', 'personal')",
            name="ck_practices_edition",
        ),
        # Added by ``a4f7e2c81b9d``.
        CheckConstraint(
            "provisioning_status IN ('in_progress', 'ready', 'failed')",
            name="practices_provisioning_status_chk",
        ),
        # Added by ``d7a3f1c8e2b4``. Thirty days to seven years.
        CheckConstraint(
            "audio_retention_days >= 30 AND audio_retention_days <= 2555",
            name="ck_practices_audio_retention_days_range",
        ),
        # Added by ``f1c8d4a92b65`` alongside the immutability trigger on
        # ``is_pentest``; widened to an equivalence by ``d8e4a6b02f19``. A
        # pentest practice must live in a schema whose name says so, so that a
        # guard reading the name cannot be fooled by a flag — and a schema so
        # named must carry the flag, so that the many consumers which exclude
        # synthetic tenants by FLAG cannot be fooled by a name. Held one way
        # only, the second case went unnoticed: tenants predating the column
        # took ``false`` from its default and were reviewed as real practices.
        # ``like_escape`` is how Postgres renders the escaped LIKE this
        # compares with; spelled the same way here so the stored and declared
        # forms match rather than looking like a drift.
        CheckConstraint(
            r"is_pentest = (schema_name LIKE 'practice\_pentest\_%' ESCAPE '\')",
            name="practices_pentest_schema_name",
        ),
        # Partial, because both columns are NULL for practically every row: the
        # queries that use them are looking for the handful that are not.
        # Created by ``d4f8a1c92e35`` and ``d7a3f1c8e2b4`` respectively.
        Index(
            "idx_practices_deleted_at",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
        Index(
            "idx_practices_offboard_scheduled_at",
            "offboard_scheduled_at",
            postgresql_where=text("offboard_scheduled_at IS NOT NULL"),
        ),
        {"schema": PLATFORM_SCHEMA},
    )


class EmailTenantMappingRow(PlatformBase):
    """Maps email → tenant_id for pre-auth tenant resolution."""

    __tablename__ = "email_tenant_mappings"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimRouteRow(PlatformBase):
    """Which practice filed the claim carrying this control number.

    A clearinghouse webhook names a transaction and nothing else. Without this
    the receiver has to ASK every practice in turn whether it can see the claim
    — a scan whose cost grows with the customer list and which is capped, so
    past the cap a delivery reports "unmatched" forever and nothing alerts,
    because "unmatched" is also what a delivery for somebody else's claim says
    (PABLO-ffw8: measured on dev, where the practice holding the claims ranked
    70th of 78 against a cap of 50).

    Deliberately the smallest thing that answers the routing question: a
    control number and a practice id. No PHI, no clinical content, no patient
    identifier — the same class of object as ``email_tenant_mappings``, which
    also lives outside the practice schemas for the same reason. Anything more
    belongs in the tenant.

    It names the CLINICIAN as well as the practice, because the practice alone
    does not finish the job. Claims are row-policied: a tenant session sees a
    clinician's claims only when it is armed as that clinician, so a receiver
    that knew only the practice still had to open a session per clinician and
    ask each in turn whether the claim was theirs — a scan inside the tenant,
    replacing the scan across tenants. Filing knows exactly whose claim it is;
    recording it turns the last search into a lookup too.

    The primary key is the point as much as the lookup is: two practices cannot
    both claim one control number, so a collision is refused at write time
    rather than resolved by whichever practice a search happened to visit first
    — which would have posted a payer's money to the wrong practice.
    """

    __tablename__ = "claim_routes"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    control_number: Mapped[str] = mapped_column(String(17), primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimReviewRow(PlatformBase):
    """The claims waiting to be read before filing, listable without a search.

    A claim held for review lives in its practice's schema, row-policied to the
    clinician who owns it, which makes "show me everything waiting on a
    reviewer" the same problem ``claim_routes`` was built to solve for inbound
    webhooks: answerable only by opening every practice in turn, and therefore
    bounded, and therefore wrong past the bound. A reviewer's list that quietly
    stops at the fiftieth practice is worse than no list — the claims it omits
    are the ones nobody knows to release.

    So the same shape as ``claim_routes``: the smallest row that answers the
    question, outside the practice schemas because the question spans them.

    NO PHI, and the boundary is worth stating because this row is read by
    surfaces that must not carry clinical detail. It holds the claim's id and
    control number, whose practice and clinician it belongs to, the PAYER's
    name, why it is waiting, and when. A payer is an insurance company, not a
    person. Nothing here names a client, a diagnosis, a service or an amount —
    a reviewer who needs those opens the claim in its own tenant session, which
    is where the row policy can still see them.

    ``payer_name`` is denormalised on purpose rather than joined. It is the
    field that makes the list useful at a glance — "first claim to Carelon" is
    the thing a reviewer reacts to — and the payer lives in the tenant, so a
    list that had to join for it would be back to opening every schema.

    **An index, never the authority.** The claim's own ``in_review`` state is
    the truth; this table only makes the set findable. Writes swallow their
    failures the way ``app.claims.routing`` does, for the same reason: a
    bookkeeping error must not fail a claim. A lost row costs a claim that is
    held but missing from the queue — still on the filing clock, still
    escalated by the watchdog (``app.claims.watchdog.OPEN_STATES``), so a
    person still hears about it. A write that could fail the hold would cost
    the hold itself.
    """

    __tablename__ = "claim_reviews"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    #: The claim's own id, so releasing it is a delete by key and a claim can
    #: never appear in the queue twice.
    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    control_number: Mapped[str] = mapped_column(String(17), nullable=False)
    #: The insurance company, not a person. Drives the list and the "a payer
    #: nobody has billed before is about to be billed" signal.
    payer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Comma-joined reason codes from ``app.claims.prefiling`` — codes only, so
    #: this column can be shown anywhere the row can.
    reasons: Mapped[str] = mapped_column(String(255), nullable=False)
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    inbox_quality_review_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    inbox_quality_review_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inbox_quality_review_opt_out_at: Mapped[datetime | None] = mapped_column(
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


#: Where an application to join a payer's panel stands.
#:
#: Not the same thing as ``payers.enrollment_status``, and the two are easy to
#: confuse. That one is the ELECTRONIC connection — whether 837/835/270 can be
#: exchanged with a payer she is already contracted with. This one is whether
#: the payer will contract with her at all. A clinician can be enrolled for
#: transactions with a payer whose panel she is not on, and vice versa.
#:
#: ``info_requested`` is the load-bearing one. A payer info request carries a
#: 30-60 day fuse and a missed one kills the application outright, which is
#: why it is the status whose deadline matters most.
PANEL_APPLICATION_STATUSES: tuple[str, ...] = (
    "researching",
    "caqh_ready",
    "submitted",
    "in_review",
    "info_requested",
    "contract_received",
    "effective",
    "closed_panel_appeal",
    "denied",
    "recredentialing",
)

#: Whose move it is. The column the concierge model turns on: with Pablo
#: running the applications, the default owner is ``pablo`` and the clinician
#: hears from us only when she genuinely has to act. Without it the board can
#: only nag her about everything, which is the process she was trying to stop
#: carrying.
PANEL_ACTION_OWNERS: tuple[str, ...] = ("pablo", "therapist")


# --- The credentialing vocabulary ---------------------------------------
#
# Moved here with the tables that use them. They are referenced by the
# CHECK constraints below and by ``app.credentialing``; nothing in the
# tenant models uses them any more.

#: How a stored credential fact was established. ``self`` is what she told us
#: and the default for anything typed into a form; ``nppes`` and ``board``
#: mean a public source was read and agreed. Only the latter two are worth
#: anything to a payer, so provenance is a column, not an assumption.
CREDENTIAL_VERIFICATION_SOURCES: tuple[str, ...] = ("self", "nppes", "board")

#: Where a licence stands with its issuing board — distinct from whether the
#: expiry date has passed. A licence can be ``active`` with a date in the past
#: while a renewal processes, and ``suspended`` with a date years out.
CREDENTIAL_LICENSE_STATUSES: tuple[str, ...] = (
    "active",
    "inactive",
    "expired",
    "suspended",
    "revoked",
)

#: Which number the clinician files taxes under. Mirrors
#: ``practice_billing_profile.tax_id_type`` — the practice has a billing
#: identity and each clinician has her own, which for a solo practice is the
#: same number in two places.
CREDENTIAL_TAX_ID_TYPES: tuple[str, ...] = ("ein", "ssn")

#: What kind of account EFT lands in — the one field a payer's enrollment form
#: asks for that cannot be read off a voided cheque.
CREDENTIAL_BANK_ACCOUNT_TYPES: tuple[str, ...] = ("checking", "savings")

#: Whether she practises on her own licence or under someone else's. Not a
#: detail of the licence: an associate is a different applicant, most payers
#: will not panel her at all, and the ones that do credential her supervisor
#: alongside her. The intake asks it before anything else for that reason.
CREDENTIAL_SUPERVISION_STATUSES: tuple[str, ...] = ("independent", "supervised")

#: Where a pre-filled value came from, for the fields the intake confirms
#: rather than asks. A payer application distinguishes self-reported from
#: verified, so the provenance is worth as much as the value — the same reason
#: ``credential_licenses.verification_source`` exists.
CREDENTIAL_CONFIRMATION_SOURCES: tuple[str, ...] = (
    "nppes",
    "pecos_public_file",
    "leie_sam",
    "clinician_profiles",
    "practice_billing_profile",
)


def _sql_in_list(values: tuple[str, ...]) -> str:
    """Render a tuple as a SQL IN-list for a CHECK constraint.

    A local copy rather than an import from ``models``: the platform models
    must not depend on the tenant models, and this is one line.
    """
    return ", ".join(f"'{v}'" for v in values)


class PlatformPanelApplicationRow(PlatformBase):
    """One application to join a payer's panel, and whose move it is.

    **Platform-scoped, and the only platform table with row-level security.**

    It lived in each practice schema first, which was the wrong shape for what
    reads it. Pablo runs the applications, so the primary consumer is an
    operator working across every practice at once — and a per-tenant table
    makes that a scan of every schema in the database. On an environment with
    a hundred-odd practices that is a catalog scan plus a union with a branch
    per schema, to answer a question about a few dozen rows.

    Moving it here makes that one indexed query. The isolation it had as a
    per-tenant table is kept rather than traded away: RLS is enabled below
    with the same ``app.current_user_id`` predicate the practice schemas use,
    so a clinician still sees only her own applications and the database is
    still the thing enforcing it. The operator reaches across by a policy
    naming ``pablo_credentialing_ops`` — one role, one table, NOBYPASSRLS.

    That isolation is not decoration. This table accumulates exactly the facts
    somebody would rather their colleagues did not browse: which panels
    rejected her, what she is appealing, how long she has been waiting.

    PHI-free — an application is about a clinician and an insurer, and no
    patient appears in it.

    ``payer_id`` refers to a row in the PRACTICE schema's ``payers`` table and
    therefore carries no foreign key: a platform table cannot reference a
    per-tenant one. ``practice_id`` is what makes that resolvable — it says
    which schema the payer lives in, and it is what the operator surface
    groups by to resolve names for the practices that actually have
    applications rather than for every practice that exists.
    """

    __tablename__ = "panel_applications"
    # PlatformBase annotates __table_args__ as the dict-only shape; the tuple
    # form (needed for the constraints and indexes) trips mypy here, same as
    # PracticeRow above.
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"status IN ({_sql_in_list(PANEL_APPLICATION_STATUSES)})",
            name="ck_panel_applications_status",
        ),
        CheckConstraint(
            f"action_owner IN ({_sql_in_list(PANEL_ACTION_OWNERS)})",
            name="ck_panel_applications_action_owner",
        ),
        # One live application per payer per clinician. A second one is a
        # recredentialing years later, not a duplicate — so this is not unique.
        Index("ix_panel_applications_user_id", "user_id"),
        Index("ix_panel_applications_practice_id", "practice_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    #: Whose panel application this is, and what RLS scopes on. A group
    #: practice credentials each clinician separately.
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice, so ``payer_id`` can be resolved in the right schema.
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: A ``payers.id`` in that practice's schema. No FK — see the class
    #: docstring.
    payer_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="researching")
    action_owner: Mapped[str] = mapped_column(String(16), nullable=False, default="pablo")
    #: What the current status is waiting on, and by when. NULL when nothing
    #: is pending — a submitted application with no answer yet is waiting on
    #: the payer's own clock, not on a date we set.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: What she is waiting for, in words she can act on. Shown to her verbatim
    #: when the owner is hers, so it is written for her, not for us.
    awaiting: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The payer's own application or reference number, which is what any
    #: phone call about it will start by asking for.
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class BookingLinkRow(PlatformBase):
    """A clinician's public booking link (see docs/design/public-booking.md).

    Platform-scoped because slug resolution must happen before a tenant
    schema can be selected. Stores no PHI: slug, owner, display copy, and
    the id of the appointment type it books. ``practice_id`` is NULL in
    single-schema deployments. Inactive links 404 on the public surface
    but stay listed for the owner.
    """

    __tablename__ = "booking_links"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The FK to platform.users(id) is declared in the Alembic migration (raw
    # SQL), not here — same reason as LaunchIntentRow and PasskeyCredentialRow
    # above: ``PlatformBase.metadata.create_all`` runs before migrations at env
    # bootstrap, and an ORM-level ForeignKey would emit the FK while users.id
    # may be transiently varchar, tripping a uuid<->varchar mismatch. This
    # table is the one that makes that failure reachable: its migration's
    # downgrade drops it outright, so a down/up replay has create_all rebuild
    # it from scratch against a schema state where c1d7e4a9f2b6 has not yet
    # re-converted users.id.
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        nullable=False,
        index=True,
    )
    practice_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey(f"{PLATFORM_SCHEMA}.practices.id", ondelete="CASCADE")
    )
    host_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: The appointment type this link books, by id. Required: a link with
    #: no type is a link nothing can gate.
    #:
    #: ``appointment_types`` is per-tenant and this table is platform-scoped
    #: (see the class docstring: a public slug must resolve before a tenant
    #: schema can be selected). A platform table cannot hold a foreign key
    #: into one of N tenant schemas, so this is a plain value, validated
    #: against the owner's own types when the link is written and resolved
    #: again after the tenant is known. A type that has since been deleted
    #: makes the link non-bookable rather than a hard error. Length is not
    #: stored here at all; the type is the one place it lives.
    appointment_type_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Enforced at the database layer, not here: no Python ``default``, so an
    # INSERT that omits this column gets ``true`` from Postgres itself. No
    # setting, no API field — relaxing a link is a direct UPDATE an operator
    # runs by hand (docs/design/public-booking.md).
    require_email_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Tombstone timestamp. NULL means live. A tombstoned row is never
    # deleted -- its slug stays claimed via the UNIQUE(slug) constraint
    # above, forever, for every caller including the original owner.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = {"schema": PLATFORM_SCHEMA}


class ProcessedPaymentEventRow(PlatformBase):
    """One row per card-processor webhook event this deployment has handled.

    The dedupe ledger behind ``app.routes.payment_webhooks``. Stripe redelivers
    an event until it gets a 2xx, so without this a retry would re-apply the
    same outcome; with it, a redelivery short-circuits before any practice
    schema is touched.

    Platform-scoped rather than per-practice for two reasons. Most events on
    the deployment's Stripe account are not this application's business at all
    (a charge the practice raised in the Stripe dashboard, a payment link, an
    invoice) and carry nothing that names a practice — those still have to be
    recorded so Stripe stops retrying them, and there is no practice schema to
    record them in. And an event id is unique across the whole account, so one
    table with the id as its primary key is the shape that actually enforces
    "handled once".

    ``practice_id`` is therefore nullable: set when the event belonged to one
    of this deployment's charges, NULL when it did not.

    A row here is a promise that redelivery may stop, so it is written only
    once the event has genuinely been dealt with — see the receiver's module
    docstring for the one case that is deliberately left unrecorded.
    """

    __tablename__ = "processed_payment_events"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    #: The processor's own event id (``evt_…``) — the idempotency key.
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    practice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: When the processor says the event happened, when it told us.
    event_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- The credentialing record ----------------------------------------------
#
# Pablo runs credentialing as a concierge service, so the operator has to read
# a clinician's licences, education, employment and panel participations to
# file an application on her behalf. Inside each practice schema that is a scan
# of every schema in the database to answer a question about one person;
# ``panel_applications`` moved here first for the same reason and these are the
# rest of the same surface.
#
# Every one is row-scoped by ``user_id`` and therefore rides the existing
# ``app.current_user_id`` GUC exactly as ``panel_applications`` does — ENABLE
# plus FORCE plus an owner policy, with the operator reaching across by a
# second policy naming ``pablo_credentialing_ops``. The isolation is not
# decoration: these hold what somebody would least like a colleague to browse
# — a disclosure, a malpractice history, which panels said no.
#
# PHI-free by construction. Every row is about a clinician; no patient appears
# in any of them.
#
# Only three of them carry ``practice_id``, and that is deliberate. It exists
# to say which practice schema resolves a ``document_id``, because the vault
# stayed per-tenant and a platform table cannot reference one — so the column
# is on the three that point at a document and nowhere else.
#
# The temptation is to stamp it on all of them for symmetry. That would put the
# practice back into the identity of a record whose whole argument is that it
# belongs to the clinician: her degree was not "filed under" a practice, and
# when she works at a second one the question has no answer. A column with no
# job is not free — it invites a query that scopes by it and a reader who
# believes that scoping means something.
#
# All three lose it when the vault follows and the foreign key comes back
# properly (PABLO-g7oe).


class CredentialEducationRow(PlatformBase):
    """One degree: where, in what, and when.

    The professional degree is the one a payer verifies with the school; the
    undergraduate one is asked for and rarely checked. Both are rows.
    """

    __tablename__ = "credential_education"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_education_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    degree: Mapped[str | None] = mapped_column(String(100), nullable=True)
    field_of_study: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ISO 3166-1 alpha-2. Asked for because a degree earned abroad routes the
    # application differently.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialTrainingRow(PlatformBase):
    """Post-degree training: internship, practicum, residency, fellowship.

    Separate from ``credential_education`` because the questions differ — a
    training entry names a supervisor and a specialty — and because a payer's
    form separates them too.
    """

    __tablename__ = "credential_training"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_training_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    # Free text, same reason as ``license_type``.
    program_type: Mapped[str] = mapped_column(String(50), nullable=False)
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    specialty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    supervisor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialEmploymentRow(PlatformBase):
    """Work history, which a payer reads as a continuous timeline.

    ``end_date`` NULL means current. ``preceding_gap_explanation`` explains the
    gap immediately BEFORE this row's ``start_date``.

    There is no ``has_gap`` flag. Whether a gap exists is a fact about two
    dates, so it is derived from the ordered rows on every ask
    (``app.credentialing.employment.gaps``) — a stored copy drifts the first
    time someone corrects a date, leaving an explanation attached to a gap
    that is no longer there.
    """

    __tablename__ = "credential_employment"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_employment_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    employer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preceding_gap_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialReferenceRow(PlatformBase):
    """A professional reference. Payers ask for three, and contact them.

    ``years_known`` is a column because a reference of under a year is
    routinely rejected — catching that before the application goes out is the
    difference between a fixable form and a sixty-day stall.
    """

    __tablename__ = "credential_references"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_references_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credential: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(100), nullable=True)
    years_known: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialDisclosureRow(PlatformBase):
    """One answered disclosure question, pinned to the wording she answered.

    The attestation questions — malpractice history, licence action, criminal
    history — get reworded by the bodies that ask them. ``question_key`` names
    the question, ``question_version`` names the wording, and the pair is what
    she attested to. Without the version a rewording silently changes the
    meaning of a stored ``true``. So two versions of one key coexist rather
    than the new one replacing the old.

    A ``true`` answer always carries an explanation, enforced in the schema:
    an unexplained yes is not an answer a payer accepts, and learning that at
    submission time costs a review cycle.
    """

    __tablename__ = "credential_disclosures"
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            "answer IS NOT TRUE OR explanation IS NOT NULL",
            name="ck_credential_disclosures_explained",
        ),
        UniqueConstraint(
            "user_id",
            "question_key",
            "question_version",
            name="ux_credential_disclosures_user_key_version",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    question_key: Mapped[str] = mapped_column(String(64), nullable=False)
    question_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    answer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialGovernmentIdRow(PlatformBase):
    """The clinician's government identifiers — one row per clinician.

    Deliberately its own table rather than columns on ``clinician_profiles``:
    SSN, date of birth and tax id are the highest-sensitivity fields in the
    schema, and isolating them gives the class exactly one access path to
    audit. Everything that reads a value here goes through
    ``app.credentialing.government_ids``, which records the read.

    Encrypted with the same AES-256-GCM helper the calendar tokens and the
    practice's billing tax id already use (``app.services.token_encryption``).
    The ``*_last4`` columns are in the clear on purpose: a form needs to show
    which number is on file, and four digits are not the identifier. There is
    no ``dob_last4`` — a partial date of birth is either the whole fact or
    useless, so seeing it means decrypting it, which is audited.

    ``business_structure`` and ``sole_proprietor`` look like one question and
    are two: the first is the entity type on the tax return, the second a
    filing status a payer's W-9 section asks about independently — and a
    single-member LLC answers yes to it.
    """

    __tablename__ = "credential_government_ids"
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"tax_id_type IS NULL OR tax_id_type IN ({_sql_in_list(CREDENTIAL_TAX_ID_TYPES)})",
            name="ck_credential_government_ids_tax_id_type",
        ),
        CheckConstraint(
            "supervision_status IS NULL OR supervision_status IN "
            f"({_sql_in_list(CREDENTIAL_SUPERVISION_STATUSES)})",
            name="ck_credential_government_ids_supervision_status",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    ssn_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssn_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    dob_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_id_type: Mapped[str | None] = mapped_column(String(3), nullable=True)
    tax_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_id_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # The organisation NPI, when the clinician bills as an entity rather than
    # as herself. The individual (type 1) NPI lives on clinician_profiles.
    type2_npi: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_structure: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sole_proprietor: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The supervision fork's answer, and it has to live somewhere a
    # supervision_relationships row does not: the intake asks it first, before
    # there is a supervisor to name, precisely so the rest of the question set
    # can branch on it.
    supervision_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Unencrypted on purpose — a CAQH number identifies a profile in a
    # directory the payers already read, not the clinician.
    caqh_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Intent, not enrollment status. Enrollment is a payer_participations row
    # with dates; these two say only that she wants the application filed, and
    # they are what turns a checklist on.
    medicare_intent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    medicaid_intent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialLicenseRow(PlatformBase):
    """Every licence the clinician holds, in every state.

    ``clinician_profiles.license_number`` / ``license_state`` remain the
    primary licence and the one a claim is filed under; this holds the full
    set, with the primary mirrored as ``is_primary``. Multi-state is ordinary
    — telehealth and the compacts — and an application asks for all of them.

    ``expiration_date`` is what the ``license`` compliance clock derives FROM,
    never the reverse. ``app.credentialing.clocks`` proposes; she confirms.
    """

    __tablename__ = "credential_licenses"
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"status IN ({_sql_in_list(CREDENTIAL_LICENSE_STATUSES)})",
            name="ck_credential_licenses_status",
        ),
        CheckConstraint(
            f"verification_source IN ({_sql_in_list(CREDENTIAL_VERIFICATION_SOURCES)})",
            name="ck_credential_licenses_verification_source",
        ),
        UniqueConstraint(
            "user_id",
            "state",
            "license_number",
            name="ux_credential_licenses_user_state_number",
        ),
        # Partial, because a unique constraint on (user_id, is_primary) would
        # also forbid a second NON-primary licence — the ordinary case.
        Index(
            "ux_credential_licenses_one_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_credential_licenses_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice schema resolves ``document_id``. The vault stayed
    #: per-tenant, so the pointer needs somewhere to be resolved; this is
    #: the only reason the column is here, and it goes when the vault
    #: follows (PABLO-g7oe).
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Free text: the abbreviations differ by state and discipline (LMFT,
    # LCSW, LPCC, PMHNP-BC), and a new one shouldn't need a migration. Same
    # posture as ``compliance_items.item_type``.
    license_type: Mapped[str] = mapped_column(String(50), nullable=False)
    license_number: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_source: Mapped[str] = mapped_column(String(8), nullable=False, default="self")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialLiabilityPolicyRow(PlatformBase):
    """A malpractice policy: carrier, limits, dates, and the COI behind it.

    A payer asks for the per-occurrence and aggregate limits, not just that
    coverage exists, and refuses an application below its floor — so the
    numbers are columns, in cents like every other amount here.

    Superseded policies stay rather than being replaced: an application asks
    for continuous coverage history, and a gap in it is a disclosure question.
    """

    __tablename__ = "credential_liability_policies"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_liability_policies_user_id", "user_id"),
        Index(
            "ux_credential_liability_policies_one_current",
            "user_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice schema resolves ``document_id``. The vault stayed
    #: per-tenant, so the pointer needs somewhere to be resolved; this is
    #: the only reason the column is here, and it goes when the vault
    #: follows (PABLO-g7oe).
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    carrier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    per_occurrence_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    aggregate_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    document_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialConfirmationRow(PlatformBase):
    """What the clinician was shown, where it came from, and whether it is right.

    The intake's first tier asks nothing. It fills fields from NPPES, the
    public PECOS file, the exclusion lists and what the practice already
    stores, and asks her only to confirm them. A confirm surface that records
    nothing is theatre, so each of those fields leaves a row here: a payer
    application distinguishes self-reported data from verified data, and
    "confirmed on this date, against this source" is what puts a value on the
    right side of that line.

    Not ``credential_disclosures``, which is the obvious-looking home and the
    wrong one. That table's check requires an explanation whenever the answer
    is ``true`` — correct for an attestation, backwards here, where ``true``
    means "this is right" and needs nothing further while ``false`` is the
    answer carrying a correction. The check below is that constraint's mirror
    image.

    ``presented_value`` is the value she saw, stored as text whatever its type.
    For a field with a home column — the NPI, the taxonomy code — the column
    remains the record and this is a snapshot, so a later divergence between
    what she confirmed and what the row now says is visible rather than
    inferred. For the handful of Tier-0 fields with no home column — the
    exclusion-list clearance, the "no hospital affiliations" the portal asks
    everyone — this IS the record.

    One row per ``(user_id, field_key)``: re-confirming is an update, because
    the question is always "is this right now", never a history of answers.
    ``credential_disclosures`` keeps versions for the opposite reason — the
    wording it pins can change underneath a stored ``true``.
    """

    __tablename__ = "credential_confirmations"
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"source IN ({_sql_in_list(CREDENTIAL_CONFIRMATION_SOURCES)})",
            name="ck_credential_confirmations_source",
        ),
        CheckConstraint(
            "confirmed OR correction IS NOT NULL",
            name="ck_credential_confirmations_corrected",
        ),
        UniqueConstraint(
            "user_id",
            "field_key",
            name="ux_credential_confirmations_user_field",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: A ``ChecklistField.key`` from ``app.credentialing.checklist``. Free text at
    #: the schema level so adding a Tier-0 field is not a migration.
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    presented_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialServiceLocationRow(PlatformBase):
    """A place the clinician sees clients, as a payer directory would list it.

    The apparently cosmetic fields are not: ``accepts_new_patients`` is the
    most-complained-about wrong entry in every payer directory, ``languages``
    and ``ada_accessible`` are how a member filters, and ``hours`` is what a
    network-adequacy audit checks. ``telehealth_only`` marks an address that
    exists for the paperwork and not a door anyone walks through.

    ``hours`` and ``languages`` are JSONB: read and written whole, never
    queried by element, and shaped by the payer rather than by us.
    """

    __tablename__ = "credential_service_locations"
    __table_args__ = (  # type: ignore[assignment]
        Index("ix_credential_service_locations_user_id", "user_id"),
        Index(
            "ux_credential_service_locations_one_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_new_patients: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hours: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ada_accessible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    languages: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    telehealth_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialBankAccountRow(PlatformBase):
    """Where EFT lands, and the voided cheque that proves it.

    Encrypted like the government ids and read through the same audited path.
    ``*_last4`` is in the clear so a form can show which account is on file —
    a routing number is public information about a bank; what is worth
    protecting is its pairing with an account number.
    """

    __tablename__ = "credential_bank_accounts"
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"account_type IN ({_sql_in_list(CREDENTIAL_BANK_ACCOUNT_TYPES)})",
            name="ck_credential_bank_accounts_account_type",
        ),
        Index("ix_credential_bank_accounts_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice schema resolves ``document_id``. The vault stayed
    #: per-tenant, so the pointer needs somewhere to be resolved; this is
    #: the only reason the column is here, and it goes when the vault
    #: follows (PABLO-g7oe).
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    account_holder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    routing_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    routing_number_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    account_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    account_number_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    account_type: Mapped[str] = mapped_column(String(8), nullable=False)
    document_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: Where this clinician stands with one payer's panel. A state machine with an
#: effective date, never a boolean, because credentialing and contracting are
#: two processes: ``credentialed`` means the payer verified her, ``contracted``
#: means a participation agreement carrying a fee schedule exists, and
#: ``in_network`` means both as of ``effective_date``. A practice can sit in
#: ``credentialed`` for years believing it is paneled — separating the two is
#: what makes that gap visible.
#:
#: ``single_case_agreement`` is the side door: a one-off in-network rate for
#: one client, agreed without paneling, so it implies none of the others.
PARTICIPATION_STATUSES: tuple[str, ...] = (
    "out_of_network",
    "application_submitted",
    "credentialed",
    "contracted",
    "in_network",
    "single_case_agreement",
    "denied",
    "terminated",
)

#: How a contracted rate is expressed. A fee schedule arrives with the contract
#: and is one of these two shapes: a table of amounts per code, or a percentage
#: of the Medicare physician fee schedule for the practice's locality.
CONTRACTED_RATE_BASES: tuple[str, ...] = ("fixed", "percent_of_mpfs")


class PayerAuthorizationRow(PlatformBase):
    """Her signature authorising Pablo to speak to payers on her behalf.

    Shaped after the BAA (``routes/users.py::accept_baa``) because it is the
    same kind of object: a versioned agreement, accepted in the product, with
    the text she saw kept beside the acceptance. It differs from the BAA in
    three ways that each matter.

    **Per-clinician, not per-practice.** The BAA is between Pablo and the
    covered entity, so it snapshots onto the practice row. This authorises us
    to act for HER, under HER NPI, on HER applications — the same reasoning
    that made ``panel_applications`` row-scoped rather than practice-wide.

    **One row per signature, never edited.** A signature is an event. Signing a
    new version adds a row; it does not overwrite the old one. That is what
    lets us answer "what authority did you hold when you rang Aetna in March"
    with the version in force in March rather than the one in force today.

    **``full_text`` is the point, not an audit nicety.** A row saying she
    accepted version ``2026-09-13`` is worth nothing once that file is edited.
    A payer or a licensing board asking what authority we claimed needs the
    words, and the words have to be the ones she was shown.

    ``revoked_at`` exists because an authorisation to act for someone with
    third parties that she cannot withdraw is not an authorisation, it is a
    trap. Revoking sets the timestamp and leaves everything else alone: what
    she signed, and that she signed it, both remain true.

    PHI-free — this is about a clinician and an insurer, and no patient
    appears in it.

    Platform-scoped, and the docstring above already argued for it before the
    move: this authorises us to act for HER, under HER NPI, on HER
    applications. An authority that stopped at a practice boundary would mean
    re-signing on joining a second practice to grant permission she has
    already granted — and would leave the operator unable to answer "what
    authority did we hold" without knowing which practice to ask.
    """

    __tablename__ = "payer_authorizations"
    # PlatformBase annotates __table_args__ as the dict-only shape; the
    # tuple form trips mypy here, same as PracticeRow above.
    __table_args__ = (  # type: ignore[assignment]
        # No unique constraint on (user_id, kind, version). Re-signing the same
        # version after a revocation is a real sequence, and the second
        # signature is a different event from the first.
        CheckConstraint(
            "kind IN ('credentialing_authorization', 'services_agreement')",
            name="ck_payer_authorizations_kind",
        ),
        Index("ix_payer_authorizations_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which document this signature is of. Two of them authorise different
    #: things — the services agreement is the commercial relationship, the
    #: credentialing authorisation is the narrow permission to sign her name to
    #: a payer's form — so they are counted separately and never stand in for
    #: one another. Schema-enforced, because a typo here would read as a
    #: missing signature and silently shut a gate rather than open one.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The dated version she signed, e.g. ``"2026-09-13"`` — the filename stem
    #: of the document, the same scheme the BAA uses. Each kind carries its own
    #: series, so the pair ``(kind, version)`` is what identifies a document.
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The document as she was shown it. See the class docstring.
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: The name she signed under, captured at signing rather than read back
    #: from the user row later — a clinician who marries and changes her legal
    #: name did not retroactively sign under the new one.
    signed_name: Mapped[str] = mapped_column(String(200), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When she withdrew it. NULL while it stands.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PayerParticipationRow(PlatformBase):
    """Whether THIS CLINICIAN is on THIS PAYER's panel, and since when.

    Not ``payers.enrollment_status`` or ``payer_enrollments``, which are the
    practice's ELECTRONIC connection to a payer (837/835/270). Different fact,
    different party, and the two move independently in every combination: this
    row is about a person and a panel, those are about a practice and a pipe.

    Unique on ``(user_id, practice_id, payer_id)``, which is why panel status
    cannot be a column on ``payers``: in a group practice each clinician holds
    her own status against the same payer.

    ``payer_id`` refers to a row in the PRACTICE schema's ``payers`` table and
    therefore carries no foreign key, exactly as ``panel_applications`` does —
    a platform table cannot reference a per-tenant one, and ``payers`` belongs
    per-tenant because it holds the practice's own electronic enrollment state
    with that insurer, which two practices hold differently for the same
    company. ``practice_id`` is what makes ``payer_id`` resolvable.

    That is also why the practice is in the unique key and did not used to be.
    The same insurer is a different uuid in every practice's ``payers`` table,
    so ``(user_id, payer_id)`` stopped identifying what it identified when both
    sides lived in one schema. This is not a relaxation: panel participation is
    contracted per billing entity, so a clinician working in two practices
    genuinely holds two statuses against the same insurer, and the old key
    could not have expressed that.

    The behavioural carve-out needs nothing here — a carve-out is already its
    own ``payers`` row with ``is_carveout`` and ``carveout_of``, so being
    in-network with a health plan and out-of-network with the entity
    administering its behavioural benefits is two rows against two payers.
    Likewise state: a payer row already knows it is BCBS of Michigan.

    ``status`` never moves without a ``payer_participation_events`` row
    recording the move. ``app.credentialing.participation`` is the only thing
    that should write this column.
    """

    __tablename__ = "payer_participations"
    # PlatformBase annotates __table_args__ as the dict-only shape; the
    # tuple form trips mypy here, same as PracticeRow above.
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"status IN ({_sql_in_list(PARTICIPATION_STATUSES)})",
            name="ck_payer_participations_status",
        ),
        UniqueConstraint(
            "user_id",
            "practice_id",
            "payer_id",
            name="ux_payer_participations_user_practice_payer",
        ),
        Index("ix_payer_participations_payer_id", "payer_id"),
        Index("ix_payer_participations_user_id", "user_id"),
        Index("ix_payer_participations_practice_id", "practice_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice, so ``payer_id`` can be resolved in the right schema.
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: A ``payers.id`` in that practice's schema. No FK — see the class
    #: docstring. The ``ondelete="CASCADE"`` this column used to carry is gone
    #: with it: deleting a payer row no longer erases the participation
    #: history, which is the better outcome anyway. A panel she was on and a
    #: payer the practice stopped filing with are different facts, and losing
    #: the first because of the second silently rewrites when she was
    #: in-network.
    payer_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="out_of_network")
    # ``credentialed_at`` set with ``contracted_at`` NULL is the
    # credentialed-but-not-contracted trap the tracker exists to surface.
    credentialed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    contracted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The payer's date, not ours — routinely weeks after the contract signs.
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Usually three years out. Missing it terminates the panel silently.
    recredentialing_due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The id the PAYER knows her by. Not her NPI; what a status call is keyed on.
    provider_id_with_payer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PayerParticipationEventRow(PlatformBase):
    """One transition of one participation, with the moment it happened.

    The current status answers "where does this panel sit"; only the history
    answers "when did it go quiet" — and an application that has not moved in
    ninety days is the ordinary failure mode of paneling, invisible to a table
    that stores only the latest value.

    Carries ``user_id`` beside ``participation_id`` for the same reason
    ``claim_events`` carries ``patient_id`` beside ``claim_id``: the row is
    isolated by its parent's predicate without the policy engine learning a
    join.

    ``detail`` holds identifiers about the PROCESS — a reviewer's reference
    number, which queue a form went into. Never anything about a client.
    """

    __tablename__ = "payer_participation_events"
    # PlatformBase annotates __table_args__ as the dict-only shape; the
    # tuple form trips mypy here, same as PracticeRow above.
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"to_status IN ({_sql_in_list(PARTICIPATION_STATUSES)})",
            name="ck_payer_participation_events_to_status",
        ),
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_sql_in_list(PARTICIPATION_STATUSES)})",
            name="ck_payer_participation_events_from_status",
        ),
        Index("ix_payer_participation_events_participation_id", "participation_id"),
        Index("ix_payer_participation_events_user_id", "user_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    participation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("platform.payer_participations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    # NULL on the row that records a participation coming into existence.
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContractedRateRow(PlatformBase):
    """What a payer agreed to pay this clinician for one code.

    The fee schedule arrives WITH the contract, after credentialing approval —
    so a rate hangs off a ``payer_participations`` row rather than off the payer,
    and only exists once that participation reached ``contracted``.

    Rates are versioned by ``effective_date`` and never edited in place: a
    schedule that changes is a new row, and the old one stays so a claim from
    last year still reads against the rate that was in force when it was filed.
    ``end_date`` NULL means "still current".

    ``basis`` is what makes this two columns rather than one.
    ``fixed`` reads ``amount_cents``; ``percent_of_mpfs`` reads ``percent``
    against ``mpfs_amount_cents``. The Medicare amount is a column and not a
    lookup because this codebase ships no fee schedule and fetching one is its
    own project — the practice enters the locality amount from the schedule the
    payer supplied. A percentage-basis row with no ``mpfs_amount_cents`` is
    therefore a real and expected state, and the variance reports it as not
    computable rather than guessing at a number somebody could bill on.

    ``modifier`` is NOT NULL and defaults to the empty string, which is the
    unmodified code. A nullable column would read better and break the unique
    constraint: NULLs are distinct in Postgres, so two "no modifier" rates for
    the same code and date would both be accepted, and the report would then
    have to choose between them.

    Carries ``user_id`` beside ``participation_id`` for the same reason
    ``payer_participation_events`` does — the row takes its parent's
    row-ownership policy without the policy engine learning a join.

    ``practice_id`` is here for one job and one only: ``source_document_id``
    names a row in a practice schema's vault, and without the practice there is
    no way to know which vault. The events table alongside this one carries no
    such column, because it points at no document — a ``practice_id`` with
    nothing to resolve invites a query that scopes by it and a reader who
    believes that scoping means something.
    """

    __tablename__ = "contracted_rates"
    # PlatformBase annotates __table_args__ as the dict-only shape; the
    # tuple form trips mypy here, same as PracticeRow above.
    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint(
            f"basis IN ({_sql_in_list(CONTRACTED_RATE_BASES)})",
            name="ck_contracted_rates_basis",
        ),
        # Each basis needs its own number and must not carry the other's, so a
        # row cannot be ambiguous about which arm computed it.
        CheckConstraint(
            "(basis = 'fixed' AND amount_cents IS NOT NULL AND percent IS NULL) OR "
            "(basis = 'percent_of_mpfs' AND percent IS NOT NULL AND amount_cents IS NULL)",
            name="ck_contracted_rates_basis_fields",
        ),
        CheckConstraint(
            "amount_cents IS NULL OR amount_cents >= 0", name="ck_contracted_rates_amount"
        ),
        CheckConstraint("percent IS NULL OR percent > 0", name="ck_contracted_rates_percent"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= effective_date",
            name="ck_contracted_rates_date_order",
        ),
        UniqueConstraint(
            "participation_id",
            "cpt",
            "modifier",
            "effective_date",
            name="ux_contracted_rates_participation_code_date",
        ),
        Index("ix_contracted_rates_participation_id", "participation_id"),
        Index("ix_contracted_rates_user_id", "user_id"),
        Index("ix_contracted_rates_practice_id", "practice_id"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    participation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("platform.payer_participations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    #: Which practice, so ``source_document_id`` can be resolved in the right
    #: schema. See the class docstring.
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cpt: Mapped[str] = mapped_column(String(10), nullable=False)
    modifier: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Percent of the Medicare fee schedule, e.g. 85.000. Over 100 is ordinary
    # for a well-negotiated behavioural contract.
    percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 3), nullable=True)
    mpfs_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The fee schedule the payer supplied, in the practice's vault. No
    #: foreign key: ``compliance_documents`` stayed per-tenant and a platform
    #: table cannot reference one. ``practice_id`` is what resolves it, and is
    #: the only reason that column is on this table and not on the events.
    source_document_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
