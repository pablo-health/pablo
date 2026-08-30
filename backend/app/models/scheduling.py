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
    note_type: str | None = None


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
    note_type: str | None = None
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
    note_type: str | None = None


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
    note_type: str | None = None
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
    note_type: str = "soap"
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
    # Messages from soft-enforcement availability rules the booking violated.
    # Empty when nothing was violated, no rules are configured, or availability
    # rules didn't run at all (e.g. a series occurrence).
    warnings: list[str] = Field(default_factory=list)


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
    refusal_reason: str | None = Field(
        default=None,
        description=(
            "Why a sentence was refused, when it was: 'ambiguous' (no "
            "boundary to write down), 'out_of_scope' (about who may book "
            "rather than when slots exist), or 'multi_intent' (an "
            "availability rule bundled with an unrelated request). Lets a "
            "caller say something specific instead of a generic failure."
        ),
    )
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
    write_target: str | None = None
    event_titling: str | None = Field(
        default=None,
        description=(
            "How sessions will actually read — the stored choice, unless it "
            "is being held back for want of a fresh confirmation"
        ),
    )
    titling_needs_attestation: bool = Field(
        default=False,
        description=(
            "The connection holds a full-name preference attested for a "
            "different Google account, so names are not being written until "
            "it is confirmed again for this one"
        ),
    )


class SetEventTitlingRequest(BaseModel):
    """How the therapist wants their sessions to read on the calendar."""

    style: str = Field(description="generic, initials, or full")
    attested: bool = Field(
        default=False,
        description=(
            "Set when choosing 'full': the therapist confirming the connected "
            "account is covered by an agreement their own practice holds. "
            "Required for that choice and recorded as evidence."
        ),
    )


class SetEventTitlingResponse(BaseModel):
    """The stored choice, and what changing it did to events already pushed."""

    style: str
    events_retitled: int = Field(
        default=0,
        description="Future events rewritten because the choice narrowed what they say",
    )
    events_not_retitled: int = Field(
        default=0,
        description="Future events the calendar would not update — retrying is safe",
    )


class GoogleCalendarConsentOption(BaseModel):
    """One choice a therapist can make about what Pablo may do.

    ``promise`` is generated from the provider's own declaration of how the
    underlying grant is limited, so a choice whose grant reaches further
    than the feature cannot be described as though it doesn't.
    """

    id: str
    promise: str


class GoogleCalendarConsentOptionsResponse(BaseModel):
    """The choices offered on connect, and the promise each one carries."""

    write_targets: list[GoogleCalendarConsentOption]
    busy: GoogleCalendarConsentOption
    default_write_target: str
    busy_default: bool


# --- Calendar practice-import models ---


class ProposedSeriesResponse(BaseModel):
    """One candidate client series a scan found.

    ``summary`` is the calendar's own wording, carried here so a person can
    read it and decide. It is never stored, logged, or sent anywhere else.
    """

    candidate_key: str
    summary: str
    weekday: int = Field(description="Monday is 0, matching Python's weekday()")
    local_start_time: str = Field(description="HH:MM in the calendar's timezone")
    duration_minutes: int
    cadence: str = Field(description="weekly or biweekly")
    occurrences_in_window: int
    occurrences_ahead: int = Field(description="Occurrences still to come — the importable ones")
    first_future_start: datetime | None
    last_seen: datetime
    recurrence_rule: str
    status: str = Field(description="active, or looks_finished for a series that appears over")
    confidence: float = Field(description="Structural score. Orders the list; hides nothing")
    preselected: bool


class ImportProposalResponse(BaseModel):
    """What a scan of the calendar suggests the practice looks like.

    Returned and then forgotten. Nothing here is written down until a
    person confirms a subset of it.
    """

    series: list[ProposedSeriesResponse]
    left_alone: int = Field(description="Events that matched nothing — a count, never their titles")
    events_read: int
    partial: bool = Field(
        description="True when a cap was hit and this describes part of the calendar"
    )
    lookback_days: int
    horizon_days: int
    timezone: str


class ImportConsentRequiredResponse(BaseModel):
    """Reading event content was never granted, so nothing was read.

    Carries the URL that asks for it — an incremental grant on top of what
    the connection already holds.
    """

    needs_consent: bool = True
    capability: str = "import"
    auth_url: str


class ConfirmImportSeries(BaseModel):
    """One series a therapist chose to import.

    The proposal is not stored, so a confirmation carries what it needs.
    Everything here is checked before anything is written.
    """

    candidate_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    start_at: datetime = Field(description="First occurrence to create — must be in the future")
    duration_minutes: int = Field(ge=5, le=480)
    cadence: str = Field(description="weekly, biweekly, or monthly")
    occurrences: int = Field(ge=1, le=104)
    timezone: str = Field(default="UTC", max_length=64)


class ConfirmImportRequest(BaseModel):
    """The subset of a proposal to turn into patients and appointments."""

    series: list[ConfirmImportSeries] = Field(min_length=1, max_length=200)


class ConfirmedSeriesResponse(BaseModel):
    """What one confirmed series became."""

    candidate_key: str
    patient_id: str
    appointments_created: int


class ConfirmImportResponse(BaseModel):
    """What the confirmation created, and what it could not."""

    confirmed: list[ConfirmedSeriesResponse]
    patients_created: int
    appointments_created: int
    skipped: list[str] = Field(
        default_factory=list,
        description=(
            "Candidate keys whose chart was created but whose recurring series "
            "was not — a collision with something already booked. Keys only, "
            "never titles."
        ),
    )


class BusyWindowResponse(BaseModel):
    """One stretch of time the calendar shows as busy.

    Times only — the BUSY grant is structurally incapable of carrying a
    title, attendee, or any other content.
    """

    start: datetime
    end: datetime


class BusyWindowsResponse(BaseModel):
    """Busy windows over a requested range."""

    windows: list[BusyWindowResponse]


class BusyWindowsNotGrantedResponse(BaseModel):
    """BUSY was never granted for this connection — declined at connect, or
    the connection predates the choice. Unlike IMPORT, BUSY is never asked
    for incrementally, so there is no consent URL to hand back; the caller
    falls back to whatever it can build without this endpoint.
    """

    granted: bool = False


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
