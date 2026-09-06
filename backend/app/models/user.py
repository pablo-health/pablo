# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .validators import validate_phone

ProviderType = Literal["therapist", "prescriber", "both"]
OnboardingState = Literal["in_progress", "later", "completed"]
ThemeName = Literal["warm-paper", "dark", "high-contrast", "boring-ehr"]
CalendarDensity = Literal["gentle", "balanced", "compact"]

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
