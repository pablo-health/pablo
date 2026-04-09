# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling service — orchestrates appointment lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from typing import TYPE_CHECKING

from ..exceptions import AppointmentNotFoundError, InvalidAppointmentError, InvalidRecurrenceError
from ..models.appointment import Appointment, AppointmentStatus, RecurrenceFrequency
from .recurrence import RecurrenceGenerator

if TYPE_CHECKING:
    from ..repositories.appointment import AppointmentRepository


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _to_utc(iso_str: str) -> str:
    """Normalize an ISO 8601 datetime string to UTC Z-suffix format."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


class SchedulingService:
    """Orchestrates appointment CRUD with validation.

    Database-independent: operates through the AppointmentRepository ABC.
    """

    def __init__(self, appointment_repo: AppointmentRepository) -> None:
        self._repo = appointment_repo

    def create_appointment(
        self,
        user_id: str,
        *,
        data: dict[str, str | int | None],
    ) -> Appointment:
        """Create a single appointment.

        Required keys in data: patient_id, title, start_at, end_at, duration_minutes.
        Optional: session_type, video_link, video_platform, notes.
        """
        patient_id = data.get("patient_id", "")
        if not patient_id:
            raise InvalidAppointmentError("patient_id is required")
        start_at = data.get("start_at", "")
        end_at = data.get("end_at", "")
        if not start_at or not end_at:
            raise InvalidAppointmentError("start_at and end_at are required")
        duration_minutes = data.get("duration_minutes", 0)
        if not isinstance(duration_minutes, int) or duration_minutes <= 0:
            raise InvalidAppointmentError("duration_minutes must be positive")

        now = _now()
        appointment = Appointment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=str(patient_id),
            title=str(data.get("title", "")),
            start_at=str(start_at),
            end_at=str(end_at),
            duration_minutes=duration_minutes,
            status=AppointmentStatus.CONFIRMED,
            session_type=str(data.get("session_type", "individual")),
            video_link=data.get("video_link"),  # type: ignore[arg-type]
            video_platform=data.get("video_platform"),  # type: ignore[arg-type]
            notes=data.get("notes"),  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )
        return self._repo.create(appointment)

    def get_appointment(self, appointment_id: str, user_id: str) -> Appointment:
        """Get a single appointment, raising if not found."""
        appointment = self._repo.get(appointment_id, user_id)
        if not appointment:
            raise AppointmentNotFoundError(appointment_id)
        return appointment

    def list_appointments(self, user_id: str, start: str, end: str) -> list[Appointment]:
        """List appointments in a date range."""
        return self._repo.list_by_range(user_id, _to_utc(start), _to_utc(end))

    def update_appointment(
        self,
        appointment_id: str,
        user_id: str,
        **updates: str | int | bool | None,
    ) -> Appointment:
        """Update fields on an existing appointment."""
        appointment = self.get_appointment(appointment_id, user_id)

        allowed_fields = {
            "title",
            "start_at",
            "end_at",
            "duration_minutes",
            "patient_id",
            "session_type",
            "video_link",
            "video_platform",
            "notes",
            "status",
        }
        for field, value in updates.items():
            if field not in allowed_fields:
                raise InvalidAppointmentError(f"Cannot update field: {field}")
            setattr(appointment, field, value)

        appointment.updated_at = _now()

        # If this appointment is part of a recurring series
        # and was individually edited, mark as exception
        if appointment.recurring_appointment_id:
            appointment.is_exception = True

        return self._repo.update(appointment)

    def cancel_appointment(self, appointment_id: str, user_id: str) -> Appointment:
        """Cancel a single appointment."""
        appointment = self.get_appointment(appointment_id, user_id)
        appointment.status = AppointmentStatus.CANCELLED
        appointment.updated_at = _now()
        return self._repo.update(appointment)

    def list_patient_appointments(self, user_id: str, patient_id: str) -> list[Appointment]:
        """List all appointments for a specific patient."""
        return self._repo.list_by_patient(user_id, patient_id)

    # --- Recurring appointment operations ---

    def create_recurring(
        self,
        user_id: str,
        *,
        data: dict[str, str | int | None],
        recurrence: dict[str, str | int | None],
    ) -> list[Appointment]:
        """Create a recurring appointment series using fan-out pattern.

        data: appointment fields (patient_id, title, start_at, end_at, etc.)
        recurrence: keys frequency, timezone, end_date (optional), count (optional)
        """
        frequency = str(recurrence.get("frequency", ""))
        timezone = str(recurrence.get("timezone", "UTC"))
        end_date = recurrence.get("end_date")
        count = recurrence.get("count")

        try:
            freq = RecurrenceFrequency(frequency)
        except ValueError as e:
            raise InvalidRecurrenceError(f"Invalid frequency: {frequency}") from e

        patient_id = data.get("patient_id", "")
        if not patient_id:
            raise InvalidAppointmentError("patient_id is required")
        start_at_str = data.get("start_at", "")
        end_at_str = data.get("end_at", "")
        if not start_at_str or not end_at_str:
            raise InvalidAppointmentError("start_at and end_at are required")
        duration_minutes = data.get("duration_minutes", 0)
        if not isinstance(duration_minutes, int) or duration_minutes <= 0:
            raise InvalidAppointmentError("duration_minutes must be positive")

        start_dt = datetime.fromisoformat(str(start_at_str).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end_at_str).replace("Z", "+00:00"))
        appt_duration = end_dt - start_dt

        parsed_end_date: date_type | None = None
        if end_date:
            parsed_end_date = date_type.fromisoformat(str(end_date))

        parsed_count: int | None = int(count) if count is not None else None

        occurrences = RecurrenceGenerator.generate(
            start_at=start_dt,
            frequency=freq,
            timezone=timezone,
            end_date=parsed_end_date,
            count=parsed_count,
        )

        master_id = str(uuid.uuid4())
        now = _now()
        rrule_str = f"FREQ={freq.value.upper()}"
        if freq == RecurrenceFrequency.BIWEEKLY:
            rrule_str = "FREQ=WEEKLY;INTERVAL=2"

        appointments: list[Appointment] = []
        for idx, occ_start in enumerate(occurrences):
            occ_end = occ_start + appt_duration
            appt = Appointment(
                id=master_id if idx == 0 else str(uuid.uuid4()),
                user_id=user_id,
                patient_id=str(patient_id),
                title=str(data.get("title", "")),
                start_at=occ_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_at=occ_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                duration_minutes=duration_minutes,
                status=AppointmentStatus.CONFIRMED,
                session_type=str(data.get("session_type", "individual")),
                video_link=data.get("video_link"),  # type: ignore[arg-type]
                video_platform=data.get("video_platform"),  # type: ignore[arg-type]
                notes=data.get("notes"),  # type: ignore[arg-type]
                recurrence_rule=rrule_str,
                recurring_appointment_id=master_id,
                recurrence_index=idx,
                created_at=now,
                updated_at=now,
            )
            appointments.append(appt)

        return self._repo.create_batch(appointments)

    def edit_future_occurrences(
        self,
        appointment_id: str,
        user_id: str,
        **updates: str | int | bool | None,
    ) -> list[Appointment]:
        """Update all future occurrences in a recurring series."""
        appointment = self.get_appointment(appointment_id, user_id)
        if not appointment.recurring_appointment_id:
            raise InvalidAppointmentError("Appointment is not part of a recurring series")

        future = self._repo.list_by_recurring_id(
            user_id, appointment.recurring_appointment_id, after=appointment.start_at
        )
        now = _now()
        allowed_fields = {"title", "session_type", "video_link", "video_platform", "notes"}
        for appt in future:
            for field, value in updates.items():
                if field in allowed_fields:
                    setattr(appt, field, value)
            appt.updated_at = now
        for appt in future:
            self._repo.update(appt)
        return future

    def cancel_future_occurrences(
        self,
        appointment_id: str,
        user_id: str,
    ) -> list[Appointment]:
        """Cancel all future occurrences in a recurring series."""
        appointment = self.get_appointment(appointment_id, user_id)
        if not appointment.recurring_appointment_id:
            raise InvalidAppointmentError("Appointment is not part of a recurring series")

        future = self._repo.list_by_recurring_id(
            user_id, appointment.recurring_appointment_id, after=appointment.start_at
        )
        now = _now()
        for appt in future:
            appt.status = AppointmentStatus.CANCELLED
            appt.updated_at = now
            self._repo.update(appt)
        return future
