# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AppointmentStatus(StrEnum):
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

    # Reminders
    reminder_24h_sent: bool = False
    reminder_1h_sent: bool = False

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
            reminder_24h_sent=data.get("reminder_24h_sent", False),
            reminder_1h_sent=data.get("reminder_1h_sent", False),
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
            "reminder_24h_sent": self.reminder_24h_sent,
            "reminder_1h_sent": self.reminder_1h_sent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
