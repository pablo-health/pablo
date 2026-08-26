# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling API request/response models (Pydantic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Runtime import: Pydantic resolves this annotation at runtime for validation,
# so it cannot live in a TYPE_CHECKING block.
from ..scheduling_engine.models.appointment import AppointmentStatus  # noqa: TC001
from .validators import validate_visit_diagnosis_codes, validate_visit_modifiers

# 11 office, 02 telehealth other than home, 10 telehealth in home. Closed —
# payers deny claims on a missing or unrecognized place-of-service code, so
# an unknown value is rejected here rather than reaching storage.
PlaceOfServiceCode = Literal["11", "02", "10"]


class VisitCodingFields(BaseModel):
    """Billing codes a clinician records on a visit.

    Shared between the two surfaces that write them — a standalone edit on
    the appointment (:class:`UpdateAppointmentRequest`) and note creation
    (``CreateStandaloneNoteRequest``) — so both validate identically and
    converge on the same stored fields. Every field is optional: nothing
    infers or defaults a code, so an unset visit stays unset.
    """

    service_code: str | None = Field(default=None, max_length=10)
    modifiers: list[str] | None = None
    unit_count: int | None = Field(default=None, ge=1)
    place_of_service: PlaceOfServiceCode | None = None
    diagnosis_codes: list[str] | None = None

    @field_validator("modifiers")
    @classmethod
    def _validate_modifiers(cls, v: list[str] | None) -> list[str] | None:
        return validate_visit_modifiers(v)

    @field_validator("diagnosis_codes")
    @classmethod
    def _validate_diagnosis_codes(cls, v: list[str] | None) -> list[str] | None:
        return validate_visit_diagnosis_codes(v)


class StartSessionFromAppointmentRequest(BaseModel):
    """Optional body for starting a session from an appointment.

    The note_type picks the registry key used for note generation; if
    omitted the session falls back to the appointment default (SOAP).
    """

    note_type: str | None = None


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


class UpdateAppointmentRequest(VisitCodingFields):
    """Request to update an appointment.

    Inherits the billing-code fields from :class:`VisitCodingFields` — this
    is the "standalone edit on the visit" surface for after-the-fact
    correction and for visits that never get a note.
    """

    title: str | None = None
    patient_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    session_type: str | None = None
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None
    status: AppointmentStatus | None = None


class AppointmentResponse(BaseModel):
    """API response for an appointment."""

    id: str
    user_id: str
    patient_id: str
    title: str
    # Display name of the patient, resolved server-side so clients can label
    # events without holding the full patient roster. None when the patient
    # could not be resolved (e.g. no live grant for the caller).
    patient_name: str | None = None
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
    service_code: str | None = None
    modifiers: list[str] | None = None
    unit_count: int | None = None
    place_of_service: str | None = None
    diagnosis_codes: list[str] | None = None
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


class ParseAvailabilityRulesRequest(BaseModel):
    """Request to parse a natural-language sentence into rule proposals."""

    text: str = Field(min_length=1, max_length=1000)


class ProposedAvailabilityRule(BaseModel):
    """A single rule proposal parsed from natural language, pending confirm.

    Never persisted directly -- the caller confirms (optionally editing
    it first) through the existing create-rule endpoint.
    """

    rule_type: str
    enforcement: str
    params: dict[str, Any]
    human_summary: str


class ParseAvailabilityRulesResponse(BaseModel):
    """Response for a natural-language availability-rule parse.

    Semantic outcomes are always HTTP 200: ``proposals`` may be empty with
    a ``could_not_parse`` reason instead -- that's a product outcome, not a
    transport error. ``exclusive`` and ``existing_conflicting_rules``
    support "I ONLY meet on..." phrasing: when true, existing working-hours
    rules for days not covered by the proposals are surfaced here so the
    confirm UI can flag them, without the parser ever proposing to delete
    or modify them.
    """

    proposals: list[ProposedAvailabilityRule]
    could_not_parse: str | None = None
    exclusive: bool = False
    existing_conflicting_rules: list[AvailabilityRuleResponse] = Field(default_factory=list)


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
    # False when the practice has no availability rules at all — booking stays
    # permissive either way, but this distinguishes "nothing to check against"
    # from "checked and clear".
    configured: bool


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
    # False when the practice has no availability rules at all — an empty
    # slots list otherwise reads identically whether nothing is set up or
    # the day is simply full. False here means "point the caller at
    # availability settings", not "fully booked".
    configured: bool


# --- Appointment type models ---


class CreateAppointmentTypeRequest(BaseModel):
    """Request to create an appointment type with an optional default fee."""

    name: str = Field(min_length=1, max_length=100)
    default_fee_cents: int | None = Field(None, ge=0)


class UpdateAppointmentTypeRequest(BaseModel):
    """Request to update an appointment type."""

    name: str | None = Field(None, min_length=1, max_length=100)
    default_fee_cents: int | None = Field(None, ge=0)


class AppointmentTypeResponse(BaseModel):
    """API response for an appointment type."""

    id: str
    user_id: str
    name: str
    default_fee_cents: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AppointmentTypeListResponse(BaseModel):
    """Response for a list of appointment types."""

    data: list[AppointmentTypeResponse]
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
