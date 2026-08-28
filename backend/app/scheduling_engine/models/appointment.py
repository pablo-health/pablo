# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AppointmentStatus(StrEnum):
    # Requested, not yet agreed to by the practice. A booking surface that
    # cannot commit the diary on its own — a booking form, an assistant taking
    # a call — creates one of these and someone confirms it.
    #
    # A PENDING appointment OCCUPIES ITS SLOT. Availability treats everything
    # that is not cancelled as busy, so a requested time is not offered to
    # somebody else while it is being decided. That is the behaviour you want
    # and it is also why ``pending_expires_at`` exists: without an expiry, a
    # request nobody gets round to answering holds a slot for ever, and a queue
    # left unread quietly eats the calendar.
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


class RecurrenceFrequency(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


@dataclass
class Appointment:
    """A scheduled appointment between a therapist and patient.

    Represents a single time slot. For recurring series, each occurrence
    is a separate Appointment sharing the same recurring_appointment_id.
    """

    id: str
    user_id: str
    patient_id: str
    title: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    status: str  # AppointmentStatus value
    session_type: str  # individual | couples | group
    video_link: str | None = None
    video_platform: str | None = None
    notes: str | None = None
    # Registry key for the note generated when a session is started from this
    # appointment. Defaults to SOAP, mirroring notes.note_type.
    note_type: str = "soap"

    # Recurrence
    recurrence_rule: str | None = None
    recurring_appointment_id: str | None = None
    recurrence_index: int | None = None
    is_exception: bool = False

    # External sync — Google Calendar
    google_event_id: str | None = None
    google_calendar_id: str | None = None
    google_sync_status: str | None = None

    # External sync — EHR iCal feed
    ical_uid: str | None = None
    ical_source: str | None = None  # "simplepractice" | "sessions_health"
    ical_sync_status: str | None = None  # "synced" | "deleted"
    ehr_appointment_url: str | None = None

    # Clinical link
    session_id: str | None = None

    # Billing codes for the visit — CPT service code, up to four modifiers,
    # unit count, place of service, and an ordered ICD-10 diagnosis list
    # (first code is primary). Every field is clinician-entered; nothing
    # here is inferred or defaulted from duration, note content, or
    # anything else.
    service_code: str | None = None
    modifiers: list[str] | None = None
    unit_count: int | None = None
    place_of_service: str | None = None
    diagnosis_codes: list[str] | None = None

    # Reminders
    reminder_24h_sent: bool = False
    reminder_1h_sent: bool = False

    # When a PENDING request stops holding its slot. None for every other
    # status. Whoever creates the request decides the instant — the rules that
    # determine it (how much notice a practice wants, how long it is willing to
    # sit on a request) belong to the surface that took the booking, not here.
    pending_expires_at: datetime | None = None

    # SHA-256 of the confirmation token mailed to the booker on a hold
    # created through a booking link that requires email confirmation.
    # Set only while status is 'pending'; the raw token itself is never
    # stored. None for every appointment that never went through that path.
    confirmation_token_hash: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Appointment:
        """Create Appointment from dictionary."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            patient_id=data["patient_id"],
            title=data["title"],
            start_at=data["start_at"],
            end_at=data["end_at"],
            duration_minutes=data["duration_minutes"],
            status=data["status"],
            session_type=data["session_type"],
            video_link=data.get("video_link"),
            video_platform=data.get("video_platform"),
            notes=data.get("notes"),
            note_type=data.get("note_type") or "soap",
            recurrence_rule=data.get("recurrence_rule"),
            recurring_appointment_id=data.get("recurring_appointment_id"),
            recurrence_index=data.get("recurrence_index"),
            is_exception=data.get("is_exception", False),
            google_event_id=data.get("google_event_id"),
            google_calendar_id=data.get("google_calendar_id"),
            google_sync_status=data.get("google_sync_status"),
            ical_uid=data.get("ical_uid"),
            ical_source=data.get("ical_source"),
            ical_sync_status=data.get("ical_sync_status"),
            ehr_appointment_url=data.get("ehr_appointment_url"),
            session_id=data.get("session_id"),
            service_code=data.get("service_code"),
            modifiers=data.get("modifiers"),
            unit_count=data.get("unit_count"),
            place_of_service=data.get("place_of_service"),
            diagnosis_codes=data.get("diagnosis_codes"),
            reminder_24h_sent=data.get("reminder_24h_sent", False),
            reminder_1h_sent=data.get("reminder_1h_sent", False),
            pending_expires_at=data.get("pending_expires_at"),
            confirmation_token_hash=data.get("confirmation_token_hash"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "patient_id": self.patient_id,
            "title": self.title,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "duration_minutes": self.duration_minutes,
            "status": self.status,
            "session_type": self.session_type,
            "video_link": self.video_link,
            "video_platform": self.video_platform,
            "notes": self.notes,
            "note_type": self.note_type,
            "recurrence_rule": self.recurrence_rule,
            "recurring_appointment_id": self.recurring_appointment_id,
            "recurrence_index": self.recurrence_index,
            "is_exception": self.is_exception,
            "google_event_id": self.google_event_id,
            "google_calendar_id": self.google_calendar_id,
            "google_sync_status": self.google_sync_status,
            "ical_uid": self.ical_uid,
            "ical_source": self.ical_source,
            "ical_sync_status": self.ical_sync_status,
            "ehr_appointment_url": self.ehr_appointment_url,
            "session_id": self.session_id,
            "service_code": self.service_code,
            "modifiers": self.modifiers,
            "unit_count": self.unit_count,
            "place_of_service": self.place_of_service,
            "diagnosis_codes": self.diagnosis_codes,
            "pending_expires_at": self.pending_expires_at,
            "confirmation_token_hash": self.confirmation_token_hash,
            "reminder_24h_sent": self.reminder_24h_sent,
            "reminder_1h_sent": self.reminder_1h_sent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
