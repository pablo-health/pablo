# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .validators import validate_email, validate_iso_date, validate_phone, validate_status


class CreatePatientRequest(BaseModel):
    """Request to create a new patient."""

    first_name: str = Field(max_length=255)
    last_name: str = Field(max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    status: str = Field("active")
    date_of_birth: str | None = None
    diagnosis: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, v: str | None) -> str | None:
        """Validate email format."""
        return validate_email(v)

    @field_validator("phone")
    @classmethod
    def validate_phone_field(cls, v: str | None) -> str | None:
        """Validate phone format."""
        return validate_phone(v)

    @field_validator("status")
    @classmethod
    def validate_status_field(cls, v: str) -> str:
        """Validate status is one of allowed values."""
        return validate_status(v)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v: str | None) -> str | None:
        """Validate date_of_birth is ISO format."""
        return validate_iso_date(v, "date_of_birth")


class UpdatePatientRequest(BaseModel):
    """Request to update a patient."""

    first_name: str | None = Field(None, min_length=1, max_length=255)
    last_name: str | None = Field(None, min_length=1, max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    status: str | None = None
    date_of_birth: str | None = None
    diagnosis: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, v: str | None) -> str | None:
        """Validate email format."""
        return validate_email(v)

    @field_validator("phone")
    @classmethod
    def validate_phone_field(cls, v: str | None) -> str | None:
        """Validate phone format."""
        return validate_phone(v)

    @field_validator("status")
    @classmethod
    def validate_status_field(cls, v: str | None) -> str | None:
        """Validate status is one of allowed values."""
        if v is None:
            return None
        return validate_status(v)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v: str | None) -> str | None:
        """Validate date_of_birth is ISO format."""
        return validate_iso_date(v, "date_of_birth")


class PatientResponse(BaseModel):
    """Patient response model for API endpoints."""

    id: str
    user_id: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    status: str
    date_of_birth: str | None = None
    diagnosis: str | None = None
    session_count: int
    last_session_date: datetime | None = None
    next_session_date: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_patient(cls, patient: Patient) -> PatientResponse:
        """Create response from Patient dataclass."""
        return cls(
            id=patient.id,
            user_id=patient.user_id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            email=patient.email,
            phone=patient.phone,
            status=patient.status,
            date_of_birth=patient.date_of_birth,
            diagnosis=patient.diagnosis,
            session_count=patient.session_count,
            last_session_date=patient.last_session_date,
            next_session_date=patient.next_session_date,
            created_at=patient.created_at,
            updated_at=patient.updated_at,
        )


class PatientListResponse(BaseModel):
    """Response model for list patients endpoint."""

    data: list[PatientResponse]
    total: int
    page: int
    page_size: int


class DeletePatientResponse(BaseModel):
    """Response model for delete patient endpoint."""

    message: str


class ExportFormat(str):
    """Supported export formats for patient data."""

    JSON = "json"
    PDF = "pdf"


class PatientExportData(BaseModel):
    """Complete patient data export for HIPAA Right to Access (§ 164.524)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    patient: PatientResponse
    sessions: list[dict[str, Any]]
    exported_at: datetime
    export_format: str


@dataclass
class Patient:
    """
    Patient data model.

    Represents a therapy client/patient managed by a therapist.
    Search fields (first_name_lower, last_name_lower) are auto-generated.
    """

    id: str
    user_id: str
    first_name: str
    last_name: str
    created_at: datetime
    updated_at: datetime
    first_name_lower: str = ""
    last_name_lower: str = ""
    session_count: int = 0
    email: str | None = None
    phone: str | None = None
    status: str = "active"
    date_of_birth: str | None = None
    diagnosis: str | None = None
    last_session_date: datetime | None = None
    next_session_date: datetime | None = None

    def __post_init__(self) -> None:
        """Auto-generate search fields if not provided."""
        if not self.first_name_lower and self.first_name:
            self.first_name_lower = self.first_name.lower()
        if not self.last_name_lower and self.last_name:
            self.last_name_lower = self.last_name.lower()

    @property
    def display_name(self) -> str:
        """Return display name as 'First Last'."""
        return f"{self.first_name} {self.last_name}"

    @property
    def formal_name(self) -> str:
        """Return formal name as 'Last, First' (clinical standard)."""
        return f"{self.last_name}, {self.first_name}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Patient:
        """Create Patient from dictionary."""
        first = data["first_name"]
        last = data["last_name"]
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            first_name=first,
            last_name=last,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            first_name_lower=data.get("first_name_lower", first.lower()),
            last_name_lower=data.get("last_name_lower", last.lower()),
            session_count=data.get("session_count", 0),
            email=data.get("email"),
            phone=data.get("phone"),
            status=data.get("status", "active"),
            date_of_birth=data.get("date_of_birth"),
            diagnosis=data.get("diagnosis"),
            last_session_date=data.get("last_session_date"),
            next_session_date=data.get("next_session_date"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Patient to dictionary."""
        return asdict(self)

    def to_api_dict(self) -> dict[str, Any]:
        """Convert Patient to dictionary for API response (excludes internal fields)."""
        data = asdict(self)
        del data["first_name_lower"]
        del data["last_name_lower"]
        return data
