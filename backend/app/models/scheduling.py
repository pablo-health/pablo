# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling API request/response models (Pydantic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateAppointmentRequest(BaseModel):
    """Request to create a single appointment."""

    patient_id: str
    title: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int = Field(ge=1, le=480)
    session_type: str = "individual"
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None


class CreateRecurringAppointmentRequest(BaseModel):
    """Request to create a recurring appointment series."""

    patient_id: str
    title: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int = Field(ge=1, le=480)
    session_type: str = "individual"
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None
    frequency: str  # weekly | biweekly | monthly
    timezone: str  # IANA timezone e.g. "America/New_York"
    end_date: str | None = None  # YYYY-MM-DD
    count: int | None = Field(default=None, ge=1, le=104)


class EditSeriesRequest(BaseModel):
    """Request to edit all future occurrences in a series."""

    title: str | None = None
    session_type: str | None = None
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None


class UpdateAppointmentRequest(BaseModel):
    """Request to update an appointment."""

    title: str | None = None
    patient_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    session_type: str | None = None
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None


class AppointmentResponse(BaseModel):
    """API response for an appointment."""

    id: str
    user_id: str
    patient_id: str
    title: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    status: str
    session_type: str
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None
    recurrence_rule: str | None = None
    recurring_appointment_id: str | None = None
    recurrence_index: int | None = None
    is_exception: bool = False
    google_event_id: str | None = None
    google_sync_status: str | None = None
    ical_uid: str | None = None
    ical_source: str | None = None
    ical_sync_status: str | None = None
    ehr_appointment_url: str | None = None
    session_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AppointmentListResponse(BaseModel):
    """Response for a list of appointments."""

    data: list[AppointmentResponse]
    total: int


# --- Availability rule models ---


class CreateAvailabilityRuleRequest(BaseModel):
    """Request to create an availability rule."""

    rule_type: str
    enforcement: str = "hard"
    params: dict[str, Any]


class UpdateAvailabilityRuleRequest(BaseModel):
    """Request to update an availability rule."""

    rule_type: str | None = None
    enforcement: str | None = None
    params: dict[str, Any] | None = None


class AvailabilityRuleResponse(BaseModel):
    """API response for an availability rule."""

    id: str
    user_id: str
    rule_type: str
    enforcement: str
    params: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AvailabilityRuleListResponse(BaseModel):
    """Response for a list of availability rules."""

    data: list[AvailabilityRuleResponse]
    total: int


class CheckConflictsRequest(BaseModel):
    """Request to check scheduling conflicts."""

    start_at: datetime
    end_at: datetime


class ConflictResponse(BaseModel):
    """A single conflict in the response."""

    rule_type: str
    enforcement: str
    message: str


class CheckConflictsResponse(BaseModel):
    """Response for conflict checking."""

    conflicts: list[ConflictResponse]
    has_hard_conflicts: bool


class TimeSlotResponse(BaseModel):
    """A single available time slot."""

    start: str
    end: str


class FreeSlotsResponse(BaseModel):
    """Response for free slot computation."""

    date: str
    duration_minutes: int
    slots: list[TimeSlotResponse]
    total: int


# --- Google Calendar models ---


class GoogleCalendarAuthResponse(BaseModel):
    """Response containing the Google OAuth authorization URL."""

    auth_url: str


class GoogleCalendarStatusResponse(BaseModel):
    """Response for Google Calendar connection status."""

    connected: bool
    calendar_id: str | None = None
    last_synced_at: datetime | None = None


# --- iCal sync models ---


class ConfigureICalRequest(BaseModel):
    """Request to configure an iCal feed URL."""

    ehr_system: str
    feed_url: str = Field(min_length=1, max_length=500)


class UnmatchedEvent(BaseModel):
    """An iCal event that couldn't be matched to a patient."""

    ical_uid: str
    client_identifier: str
    start_at: datetime
    ehr_appointment_url: str = ""


class ICalSyncResponse(BaseModel):
    """Response from an iCal sync operation."""

    created: int
    updated: int
    deleted: int
    unchanged: int
    unmatched_events: list[UnmatchedEvent]
    errors: list[str] = Field(default_factory=list)


class ICalConnectionStatus(BaseModel):
    """Status of a single iCal feed connection."""

    ehr_system: str
    connected: bool
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None


class ICalStatusResponse(BaseModel):
    """Response for all iCal connections."""

    connections: list[ICalConnectionStatus]


class ResolveClientRequest(BaseModel):
    """Request to manually map a client identifier to a patient."""

    ehr_system: str
    client_identifier: str
    patient_id: str


class ICalConfigureResponse(BaseModel):
    """Response from configuring an iCal feed."""

    message: str
    event_count: int
    ehr_system: str


class ImportClientsResponse(BaseModel):
    """Response from importing clients via CSV/zip."""

    imported: int
    updated: int
    skipped: int
    mappings_created: int
    errors: list[str] = Field(default_factory=list)
