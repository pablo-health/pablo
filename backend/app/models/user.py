# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .validators import validate_iso_date


class UpdateUserRequest(BaseModel):
    """Request to update user profile."""

    name: str | None = Field(None, min_length=1, max_length=255)
    title: str | None = Field(None, max_length=50)
    credentials: str | None = Field(None, max_length=100)
    baa_accepted_at: datetime | None = None

    @classmethod
    def validate_baa_date(cls, v: str | None) -> str | None:
        """Validate baa_accepted_at is ISO format."""
        return validate_iso_date(v, "baa_accepted_at")


class UserPreferences(BaseModel):
    """User preferences for the companion app."""

    default_video_platform: str = "zoom"
    default_session_type: str = "individual"
    default_duration_minutes: int = Field(default=50, ge=1, le=480)
    auto_transcribe: bool = True
    quality_preset: str = "balanced"
    therapist_display_name: str | None = None
    working_hours_start: int = Field(default=8, ge=0, le=23)
    working_hours_end: int = Field(default=18, ge=1, le=24)
    calendar_default_view: str = "timeGridWeek"
    timezone: str = Field(
        default="America/New_York",
        description="IANA timezone. Auto-detected from browser on first save.",
    )


class AcceptBAARequest(BaseModel):
    """Request to accept Business Associate Agreement."""

    legal_name: str = Field(min_length=1, max_length=255)
    license_number: str = Field(min_length=1, max_length=100)
    license_state: str = Field(min_length=2, max_length=2)
    practice_name: str | None = Field(None, max_length=255)
    business_address: str = Field(min_length=1, max_length=500)
    version: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    accepted: bool = True


class BAAStatusResponse(BaseModel):
    """Response containing BAA acceptance status."""

    accepted: bool
    accepted_at: datetime | None = None
    version: str | None = None
    current_version: str


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
    picture: str | None = None
    baa_accepted_at: datetime | None = None
    baa_version: str | None = None
    baa_legal_name: str | None = None
    baa_license_number: str | None = None
    baa_license_state: str | None = None
    baa_practice_name: str | None = None
    baa_business_address: str | None = None
    baa_full_text: str | None = None
    is_platform_admin: bool = False
    status: str = "approved"
    mfa_enrolled_at: datetime | None = None
    role: str = "clinician"

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
            baa_accepted_at=data.get("baa_accepted_at"),
            baa_version=data.get("baa_version"),
            baa_legal_name=data.get("baa_legal_name"),
            baa_license_number=data.get("baa_license_number"),
            baa_license_state=data.get("baa_license_state"),
            baa_practice_name=data.get("baa_practice_name"),
            baa_business_address=data.get("baa_business_address"),
            baa_full_text=data.get("baa_full_text"),
            is_platform_admin=data.get("is_platform_admin", data.get("is_admin", False)),
            status=data.get("status", "approved"),
            mfa_enrolled_at=data.get("mfa_enrolled_at"),
            role=data.get("role", "clinician"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert User to dictionary."""
        return asdict(self)
