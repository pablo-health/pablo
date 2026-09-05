# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment repository interface and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from ...utcnow import utc_now
from ..models.appointment import AppointmentStatus

if TYPE_CHECKING:
    from ..models.appointment import Appointment


class AppointmentRepository(ABC):
    """Abstract base class for appointment data access."""

    @abstractmethod
    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        """Get appointment by ID, ensuring it belongs to the user."""

    @abstractmethod
    def list_by_range(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
    ) -> list[Appointment]:
        """List appointments for a user within a date range."""

    @abstractmethod
    def list_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[Appointment]:
        """List appointments for a specific patient."""

    @abstractmethod
    def list_overlapping(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> list[Appointment]:
        """List non-cancelled appointments on ``user_id``'s calendar that
        overlap the half-open interval ``[start, end)``.

        Unlike :meth:`list_by_range`, this also matches appointments that
        start before ``start`` and end inside the window — the case a
        proposed-slot collision check needs to catch. Back-to-back
        appointments (one ends exactly when the other starts) do not
        overlap. Pass ``exclude_appointment_id`` when checking a
        reschedule so the appointment being moved doesn't collide with
        itself.
        """

    def count_by_range(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
    ) -> int:
        """Count of the :meth:`list_by_range` slice.

        For callers that only need the volume (the audit reviewer's
        new-vs-seasoned user signal) — backends can answer with an
        aggregate instead of loading every row. Default delegates to
        ``list_by_range``.
        """
        return len(self.list_by_range(user_id, start, end))

    def start_times_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[datetime]:
        """Start timestamps of the :meth:`list_by_patient` slice.

        For callers that only correlate timestamps (the audit reviewer's
        appointment-proximity check). Default delegates to
        ``list_by_patient``.
        """
        return [a.start_at for a in self.list_by_patient(user_id, patient_id)]

    @abstractmethod
    def get_by_session_ids(self, session_ids: list[str], user_id: str) -> dict[str, Appointment]:
        """The appointment each session started from, keyed by ``session_id``.

        A session with no appointment (or one the user can't access) is
        simply absent from the result. Backs the unbilled queue, which needs
        each finalized session's appointment to resolve a rate and to check
        the charge ledger.
        """

    @abstractmethod
    def list_by_recurring_id(
        self,
        user_id: str,
        recurring_appointment_id: str,
        after: str | datetime | None = None,
    ) -> list[Appointment]:
        """List all occurrences of a recurring series, optionally after a date."""

    @abstractmethod
    def list_by_ical_source(
        self,
        user_id: str,
        ehr_system: str,
    ) -> list[Appointment]:
        """List all appointments synced from a specific iCal source."""

    @abstractmethod
    def get_by_google_event_id(
        self,
        user_id: str,
        google_event_id: str,
    ) -> Appointment | None:
        """Get the appointment (if any) synced to a given Google Calendar event."""

    @abstractmethod
    def create(self, appointment: Appointment) -> Appointment:
        """Create a new appointment."""

    @abstractmethod
    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        """Create multiple appointments in a batch."""

    @abstractmethod
    def update(self, appointment: Appointment) -> Appointment:
        """Update an existing appointment."""

    @abstractmethod
    def bulk_set_patient(self, appointment_ids: list[str], patient_id: str) -> int:
        """Link many appointments to a patient in one statement.

        Sets ``patient_id`` (and refreshes ``updated_at``) on every
        appointment in ``appointment_ids``; returns the number updated.
        Replaces the per-row update loop in iCal client resolution.
        """

    @abstractmethod
    def list_expired_pending(self, user_id: str, now: datetime) -> list[Appointment]:
        """Pending requests whose ``pending_expires_at`` has passed.

        The sweep behind :meth:`SchedulingService.expire_pending_appointments`.
        A pending request holds its slot, so one nobody answers has to stop
        holding it eventually or an unread queue silently eats the calendar.
        """

    @abstractmethod
    def get_by_confirmation_token_hash(self, user_id: str, token_hash: str) -> Appointment | None:
        """Find the appointment holding this confirmation token, any status.

        Unlike every other lookup here, this deliberately does not filter
        by status — a confirmed row answers an idempotent double-click, and
        a cancelled row (hash still present) is a lapsed hold that may yet
        be revived. ``user_id`` is the link owner, not a patient grant: a
        token is bound to the link it was minted on.
        """

    @abstractmethod
    def delete(self, appointment_id: str, user_id: str) -> bool:
        """Delete an appointment. Returns True if deleted."""


class InMemoryAppointmentRepository(AppointmentRepository):
    """In-memory implementation for testing.

    Maintains a per-(patient_id, user_id) access set mirroring
    ``patient_clinicians`` semantics for the patient-scoped methods
    (``get``, ``list_by_patient``, ``delete``). Calendar-personal
    methods (``list_by_range``, ``list_by_recurring_id``,
    ``list_by_ical_source``) keep the original ``appointment.user_id``
    filter — those are "my calendar" slices, not patient queries.
    """

    def __init__(self) -> None:
        self._appointments: dict[str, Appointment] = {}
        self._access: set[tuple[str, str]] = set()  # (patient_id, user_id)

    def grant_access(self, patient_id: str, user_id: str) -> None:
        """Test helper — record a patient_clinicians-equivalent grant."""
        self._access.add((patient_id, user_id))

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return (patient_id, user_id) in self._access

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        appt = self._appointments.get(appointment_id)
        if appt is None:
            return None
        if not self._can_access(appt.patient_id, user_id):
            return None
        return appt

    def list_by_range(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
    ) -> list[Appointment]:
        """My-calendar slice — filtered by appointment ownership, not patient access."""
        start_dt = (
            start
            if isinstance(start, datetime)
            else datetime.fromisoformat(start.replace("Z", "+00:00"))
        )
        end_dt = (
            end if isinstance(end, datetime) else datetime.fromisoformat(end.replace("Z", "+00:00"))
        )
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id and a.start_at >= start_dt and a.start_at < end_dt
            ],
            key=lambda a: a.start_at,
        )

    def list_by_patient(
        self,
        user_id: str,
        patient_id: str,
    ) -> list[Appointment]:
        if not self._can_access(patient_id, user_id):
            return []
        return sorted(
            [a for a in self._appointments.values() if a.patient_id == patient_id],
            key=lambda a: a.start_at,
        )

    def get_by_session_ids(self, session_ids: list[str], user_id: str) -> dict[str, Appointment]:
        wanted = set(session_ids)
        return {
            a.session_id: a
            for a in self._appointments.values()
            if a.session_id in wanted and self._can_access(a.patient_id, user_id)
        }

    def list_overlapping(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> list[Appointment]:
        """My-calendar slice — filtered by appointment ownership, not patient access."""
        start_dt = (
            start
            if isinstance(start, datetime)
            else datetime.fromisoformat(start.replace("Z", "+00:00"))
        )
        end_dt = (
            end if isinstance(end, datetime) else datetime.fromisoformat(end.replace("Z", "+00:00"))
        )
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id
                and a.status != "cancelled"
                and a.id != exclude_appointment_id
                and a.start_at < end_dt
                and a.end_at > start_dt
            ],
            key=lambda a: a.start_at,
        )

    def list_by_recurring_id(
        self,
        user_id: str,
        recurring_appointment_id: str,
        after: str | datetime | None = None,
    ) -> list[Appointment]:
        results = [
            a
            for a in self._appointments.values()
            if a.user_id == user_id and a.recurring_appointment_id == recurring_appointment_id
        ]
        if after:
            after_dt = (
                after
                if isinstance(after, datetime)
                else datetime.fromisoformat(after.replace("Z", "+00:00"))
            )
            results = [a for a in results if a.start_at >= after_dt]
        return sorted(results, key=lambda a: a.start_at)

    def list_by_ical_source(
        self,
        user_id: str,
        ehr_system: str,
    ) -> list[Appointment]:
        return sorted(
            [
                a
                for a in self._appointments.values()
                if a.user_id == user_id and a.ical_source == ehr_system
            ],
            key=lambda a: a.start_at,
        )

    def get_by_google_event_id(
        self,
        user_id: str,
        google_event_id: str,
    ) -> Appointment | None:
        for a in self._appointments.values():
            if a.user_id == user_id and a.google_event_id == google_event_id:
                return a
        return None

    def list_expired_pending(self, user_id: str, now: datetime) -> list[Appointment]:
        return [
            appt
            for appt in self._appointments.values()
            if appt.user_id == user_id
            and appt.status == AppointmentStatus.PENDING
            and appt.pending_expires_at is not None
            and appt.pending_expires_at <= now
        ]

    def get_by_confirmation_token_hash(self, user_id: str, token_hash: str) -> Appointment | None:
        for appt in self._appointments.values():
            if appt.user_id == user_id and appt.confirmation_token_hash == token_hash:
                return appt
        return None

    def create(self, appointment: Appointment) -> Appointment:
        self._appointments[appointment.id] = appointment
        # Auto-grant the creator access to the patient — mirrors the
        # Postgres guarantee that callers verified patient access
        # before reaching this point.
        self._access.add((appointment.patient_id, appointment.user_id))
        return appointment

    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        for appt in appointments:
            self._appointments[appt.id] = appt
            self._access.add((appt.patient_id, appt.user_id))
        return appointments

    def update(self, appointment: Appointment) -> Appointment:
        self._appointments[appointment.id] = appointment
        return appointment

    def bulk_set_patient(self, appointment_ids: list[str], patient_id: str) -> int:
        now = utc_now()
        count = 0
        for appt_id in appointment_ids:
            appt = self._appointments.get(appt_id)
            if appt is not None:
                appt.patient_id = patient_id
                appt.updated_at = now
                count += 1
        return count

    def delete(self, appointment_id: str, user_id: str) -> bool:
        appt = self.get(appointment_id, user_id)
        if not appt:
            return False
        del self._appointments[appointment_id]
        return True
