# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from .validators import validate_phone

ProviderType = Literal["therapist", "prescriber", "both"]
OnboardingState = Literal["in_progress", "later", "completed"]
ThemeName = Literal["warm-paper", "dark", "high-contrast", "boring-ehr"]
CalendarDensity = Literal["gentle", "balanced", "compact"]

#: SUPERSEDED by :data:`BillingSetupState`. Retained so preferences saved
#: before the change still load, and so a returning clinician resumes where she
#: left off rather than at an empty checklist.
#:
#: It asked one question and got two answers back: "platform_to_own" and
#: "wants_panels" describe where she is AND where she wants to go, which is why
#: a therapist on a platform who also takes cash clients could not describe
#: herself at all.
BillingSetupRoute = Literal[
    "private_pay",
    "already_paneled",
    "wants_panels",
    "platform_to_own",
]

#: What is true of how she is paid today. Several hold at once — a therapist on
#: a platform who also sees a few clients privately is the ordinary case, not an
#: edge one.
#:
#: Purely descriptive. Nothing here says what she WANTS, which is asked once,
#: separately, and answered by ``billing_setup_wants_credentialing``. Each entry
#: only ever ADDS to what setup covers; none of them removes a step or sends her
#: down a different path, which is what makes an under-answered checklist safe.
BillingSetupState = Literal[
    "self_pay",
    "platform",
    "own_insurance",
]

#: How a superseded single answer reads as a description of today. Only the
#: present-tense half survives: "wants_panels" said she is paid privately and
#: hopes to panel, and the hope is now a separate question rather than an
#: inference we make on her behalf.
_ROUTE_AS_STATE: dict[str, list[str]] = {
    "private_pay": ["self_pay"],
    "already_paneled": ["own_insurance"],
    "wants_panels": ["self_pay"],
    "platform_to_own": ["platform"],
}

#: Which superseded answers carried an explicit wish to be credentialed. Read
#: once, to seed the new question for a clinician who already answered the old
#: one — never to re-derive it afterwards.
_ROUTE_WANTED_CREDENTIALING = frozenset({"wants_panels", "platform_to_own"})

# Max length of a single credential title (matches clinician_profiles.title).
MAX_CREDENTIAL_TITLE_LEN = 50


class UpdateUserRequest(BaseModel):
    """Request to update user profile."""

    name: str | None = Field(None, min_length=1, max_length=255)
    legal_name: str | None = Field(None, min_length=1, max_length=255)
    title: str | None = Field(None, max_length=50)
    credentials: str | None = Field(None, max_length=100)
    # Structured credential titles (multi-select picker + free-text). When
    # present, the server derives the ``credentials`` display string from it.
    credential_titles: list[str] | None = Field(None, max_length=20)
    provider_type: ProviderType | None = None
    onboarding_state: OnboardingState | None = None
    phone: str | None = Field(None, max_length=50)
    profile_basics_completed: bool | None = None

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        """Normalize/validate an optional phone number (None when blank)."""
        return validate_phone(v)

    @field_validator("credential_titles")
    @classmethod
    def _clean_credential_titles(cls, v: list[str] | None) -> list[str] | None:
        """Strip each title, drop blanks, and bound length. Order and any
        board-certification suffix are preserved verbatim (``PMHNP`` and
        ``PMHNP-BC`` are distinct values)."""
        if v is None:
            return None
        cleaned: list[str] = []
        for raw in v:
            title = raw.strip()
            if not title:
                continue
            if len(title) > MAX_CREDENTIAL_TITLE_LEN:
                raise ValueError(
                    f"each credential title must be {MAX_CREDENTIAL_TITLE_LEN} characters or fewer"
                )
            cleaned.append(title)
        return cleaned


class UserPreferences(BaseModel):
    """User preferences for the companion app."""

    default_video_platform: str = "zoom"
    default_session_type: str = "individual"
    default_duration_minutes: int = Field(default=50, ge=1, le=480)
    auto_transcribe: bool = True
    quality_preset: str = "balanced"
    therapist_display_name: str | None = None
    calendar_default_view: str = "timeGridWeek"
    timezone: str = Field(
        default="America/New_York",
        description="IANA timezone. Auto-detected from browser on first save.",
    )
    theme: ThemeName = "warm-paper"
    calendar_density: CalendarDensity = "balanced"
    # Set once the therapist has walked (or waved away) the first-visit
    # calendar setup wizard, so the Calendar page stops opening on it.
    # Settings keeps its own way back into the wizard regardless.
    calendar_setup_complete: bool = False
    # The same, for billing setup. Set when she finishes the wizard AND when
    # she chooses to finish later, because a first-visit surface she cannot
    # leave is a trap rather than a wizard — she gets a card on the billing
    # page instead, and can return whenever.
    billing_setup_complete: bool = False
    # SUPERSEDED by ``billing_setup_state``. Still written by nothing and read
    # only to seed the checklist for a clinician who answered the old question;
    # kept on the model so her saved blob still validates.
    billing_setup_route: BillingSetupRoute | None = None
    # Everything true of how she is paid today, not one of them. ``None`` means
    # she has not answered; an empty list means she answered "not seeing clients
    # yet", which is a real answer and must not read as unanswered.
    billing_setup_state: list[BillingSetupState] | None = None
    # Whether she asked for help getting in-network under her own contracts.
    # Deliberately its own field rather than a value inside the list above: it
    # is about what she WANTS and changes on its own schedule, and folding a
    # wish into a description of today is exactly what made the old single
    # answer unable to describe a platform clinician.
    billing_setup_wants_credentialing: bool = False
    # Whether she wants clients to be able to pay by card. A want, like the
    # field above, and stored the same way for the same reason.
    #
    # NULLABLE, unlike its neighbour, and the difference carries meaning: None
    # is "has not said", and the wizard defaults it ON for a practice whose
    # clients already pay it directly. A plain ``False`` default would be
    # indistinguishable from her having declined, so unticking the box would
    # not survive — the default would switch it back on the next time she
    # opened the wizard, and the step she just declined would reappear.
    billing_setup_wants_card_payments: bool | None = None
    # Where she had got to, as the step's ID rather than its position. An index
    # would quietly point at the wrong screen the first time a step is inserted
    # ahead of it; an id either resolves or falls back to the start of her
    # branch. Free text on purpose, so adding a step is not a schema change.
    billing_setup_step: str | None = None

    @model_validator(mode="after")
    def _seed_state_from_superseded_route(self) -> UserPreferences:
        """Read a pre-checklist answer forward, once.

        A clinician who answered the old single question should resume where
        she left off rather than meet an empty checklist, so her saved route is
        read as the description of today it half was. Only ever seeds: once
        ``billing_setup_state`` is set — including to the empty list, which is
        her real answer of "not seeing clients yet" — this does nothing, so
        unticking a box can never be undone by the old value underneath it.
        """
        if self.billing_setup_state is None and self.billing_setup_route is not None:
            self.billing_setup_state = cast(
                "list[BillingSetupState]",
                list(_ROUTE_AS_STATE.get(self.billing_setup_route, [])),
            )
            if self.billing_setup_route in _ROUTE_WANTED_CREDENTIALING:
                self.billing_setup_wants_credentialing = True
        return self


class UpdateThemeRequest(BaseModel):
    """Targeted update of just the UI theme preference."""

    theme: ThemeName


class AcceptBAARequest(BaseModel):
    """Request to accept Business Associate Agreement.

    Credential fields are no longer submitted here — they are read from
    the already-stored professional-info (legal_name on the user row,
    license_* on the clinician profile, address on the practice row).
    The snapshot is built server-side at acceptance time.
    """

    version: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    accepted: bool = True


class UpdateProfessionalInfoRequest(BaseModel):
    """Request to save professional credentials at the onboarding step."""

    legal_name: str | None = Field(None, min_length=1, max_length=255)
    license_number: str | None = Field(None, min_length=1, max_length=100)
    license_state: str | None = Field(None, min_length=2, max_length=2)
    business_address: str | None = Field(None, min_length=1, max_length=500)
    practice_name: str | None = Field(None, min_length=1, max_length=255)
    practice_phone: str | None = Field(None, min_length=1, max_length=50)
    # Prescriber credential identifiers — optional, only relevant to
    # provider types that prescribe. Stored once so downstream surfaces
    # can reuse them instead of re-typing per encounter.
    dea_number: str | None = Field(None, min_length=1, max_length=50)
    npi_number: str | None = Field(None, pattern=r"^\d{10}$")
    # NUCC health care provider taxonomy code — the specialty classification
    # a payer expects on a claim's rendering-provider loop. A short picker
    # of common behavioral-health codes plus free text, so no fixed pattern.
    taxonomy_code: str | None = Field(None, min_length=1, max_length=10)


# Keep in sync with the DB CHECK constraint added by
# d7a3f1c8e2b4_practices_retention_offboard_columns.py.
AUDIO_RETENTION_MIN_DAYS = 30
AUDIO_RETENTION_MAX_DAYS = 2555


class AudioRetentionResponse(BaseModel):
    """Response for the caller's own practice audio retention window."""

    practice_id: str
    audio_retention_days: int


class UpdateAudioRetentionRequest(BaseModel):
    """Request to set the caller's own practice audio retention window."""

    audio_retention_days: int = Field(..., ge=AUDIO_RETENTION_MIN_DAYS, le=AUDIO_RETENTION_MAX_DAYS)


class BAAStatusResponse(BaseModel):
    """Response containing BAA acceptance status."""

    accepted: bool
    accepted_at: datetime | None = None
    version: str | None = None
    current_version: str


class AcknowledgeSecurityGuideRequest(BaseModel):
    """Request to record acknowledgment of the security & privacy guide.

    The version string is the YYYY-MM-DD effective date of the guide
    the user is acknowledging. The frontend declares the current
    version; this endpoint records whatever is sent.
    """

    version: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class SecurityGuideStatusResponse(BaseModel):
    """Response containing security-guide acknowledgment status."""

    acknowledged: bool
    acknowledged_at: datetime | None = None
    version: str | None = None


@dataclass
class User:
    """
    User data model.

    Represents a therapist/clinician using the platform.
    """

    id: str
    email: str
    name: str
    created_at: datetime
    title: str | None = None
    credentials: str | None = None
    credential_titles: list[str] | None = None
    picture: str | None = None
    phone: str | None = None
    baa_accepted_at: datetime | None = None
    baa_version: str | None = None
    legal_name: str | None = None
    is_platform_admin: bool = False
    status: str = "approved"
    mfa_enrolled_at: datetime | None = None
    role: str = "clinician"
    provider_type: str | None = None
    security_guide_acknowledged_at: datetime | None = None
    security_guide_version: str | None = None
    onboarding_state: str | None = None
    profile_basics_completed_at: datetime | None = None
    chat_quality_review_opt_in: bool = False
    chat_quality_review_opt_in_at: datetime | None = None
    chat_quality_review_opt_out_at: datetime | None = None
    session_notes_quality_review_opt_in: bool = False
    session_notes_quality_review_opt_in_at: datetime | None = None
    session_notes_quality_review_opt_out_at: datetime | None = None
    quality_review_consent_prompted_at: datetime | None = None
    inbox_quality_review_opt_in: bool = False
    inbox_quality_review_opt_in_at: datetime | None = None
    inbox_quality_review_opt_out_at: datetime | None = None

    @property
    def is_admin(self) -> bool:
        """Backward-compat alias for is_platform_admin."""
        return self.is_platform_admin

    @property
    def formal_name(self) -> str:
        """Return name with title if available."""
        if self.title:
            return f"{self.title} {self.name}"
        return self.name

    @property
    def professional_name(self) -> str:
        """Return name with credentials if available."""
        if self.credentials:
            return f"{self.name}, {self.credentials}"
        return self.name

    @property
    def full_name(self) -> str:
        """Return name with title and credentials if available."""
        parts = []
        if self.title:
            parts.append(self.title)
        parts.append(self.name)
        name = " ".join(parts)
        if self.credentials:
            name = f"{name}, {self.credentials}"
        return name

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        """Create User from dictionary."""
        return cls(
            id=data["id"],
            email=data["email"],
            name=data["name"],
            created_at=data["created_at"],
            title=data.get("title"),
            credentials=data.get("credentials"),
            picture=data.get("picture"),
            phone=data.get("phone"),
            baa_accepted_at=data.get("baa_accepted_at"),
            baa_version=data.get("baa_version"),
            legal_name=data.get("legal_name"),
            is_platform_admin=data.get("is_platform_admin", data.get("is_admin", False)),
            status=data.get("status", "approved"),
            mfa_enrolled_at=data.get("mfa_enrolled_at"),
            role=data.get("role", "clinician"),
            provider_type=data.get("provider_type"),
            security_guide_acknowledged_at=data.get("security_guide_acknowledged_at"),
            security_guide_version=data.get("security_guide_version"),
            onboarding_state=data.get("onboarding_state"),
            chat_quality_review_opt_in=data.get("chat_quality_review_opt_in", False),
            chat_quality_review_opt_in_at=data.get("chat_quality_review_opt_in_at"),
            chat_quality_review_opt_out_at=data.get("chat_quality_review_opt_out_at"),
            session_notes_quality_review_opt_in=data.get(
                "session_notes_quality_review_opt_in", False
            ),
            session_notes_quality_review_opt_in_at=data.get(
                "session_notes_quality_review_opt_in_at"
            ),
            session_notes_quality_review_opt_out_at=data.get(
                "session_notes_quality_review_opt_out_at"
            ),
            quality_review_consent_prompted_at=data.get("quality_review_consent_prompted_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert User to dictionary."""
        return asdict(self)
