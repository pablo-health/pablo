# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling service — orchestrates appointment lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, tzinfo
from datetime import date as date_type
from typing import TYPE_CHECKING

from ...utcnow import utc_now
from ..exceptions import (
    AppointmentConflictError,
    AppointmentNotFoundError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
    RuleViolationError,
)
from ..models.appointment import Appointment, AppointmentStatus, RecurrenceFrequency
from ..models.availability import EnforcementLevel
from .recurrence import RecurrenceGenerator

if TYPE_CHECKING:
    from ..repositories.appointment import AppointmentRepository
    from .availability import AvailabilityEngine


def _now() -> datetime:
    return utc_now()


def _localize(dt: datetime, tz: tzinfo) -> datetime:
    """Attach ``tz`` to a naive datetime, reading it as local wall-clock.

    A caller that sends ``2026-08-27T15:00:00`` with no offset means three
    o'clock on the practice's own clock — the same thing the clinician means
    when they type "15:00" into an availability rule. Note this is
    deliberately ``replace``, not ``astimezone``: ``astimezone`` on a naive
    datetime assumes the *host's* timezone, which would make the same request
    resolve differently on a UTC container than on a developer's laptop.
    Already-aware input is an explicit instant and passes through untouched.
    """
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _to_utc(iso_str: str, tz: tzinfo = UTC) -> str:
    """Normalize an ISO 8601 datetime string to UTC Z-suffix format.

    An offset-less string is read as wall-clock in ``tz`` (see ``_localize``).
    """
    dt = _localize(datetime.fromisoformat(iso_str), tz)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _as_datetime(value: datetime | str, tz: tzinfo = UTC) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return _localize(parsed, tz)


class SchedulingService:
    """Orchestrates appointment CRUD with validation.

    Database-independent: operates through the AppointmentRepository ABC.
    """

    def __init__(
        self,
        appointment_repo: AppointmentRepository,
        availability_engine: AvailabilityEngine | None = None,
    ) -> None:
        self._repo = appointment_repo
        self._availability_engine = availability_engine
        # Populated by the most recent create/update call — the caller reads
        # this right after, before making any further calls on this service,
        # to surface soft-rule warnings alongside the written appointment.
        self.rule_warnings: list[str] = []

    def _check_availability_rules(
        self,
        user_id: str,
        start_dt: datetime,
        end_dt: datetime,
        tz: tzinfo = UTC,
    ) -> list[str]:
        """Evaluate availability rules against a proposed booking window.

        ``tz`` is the zone rules are evaluated in — see
        ``AvailabilityEngine.check_conflicts``. Defaults to UTC so existing
        callers are unaffected.

        Skipped entirely when no engine was wired in (the ~35 tests that
        construct ``SchedulingService(repo)`` directly) or when the user has
        configured no rules. A hard-enforcement violation refuses the
        booking; soft violations are returned as warning messages for the
        caller to surface rather than silently vanishing.
        """
        if self._availability_engine is None:
            return []
        result = self._availability_engine.check_conflicts(user_id, start_dt, end_dt, tz=tz)
        hard = [c.message for c in result.conflicts if c.enforcement == EnforcementLevel.HARD]
        if hard:
            raise RuleViolationError(hard)
        return [c.message for c in result.conflicts if c.enforcement == EnforcementLevel.SOFT]

    def _reject_if_overlapping(
        self,
        user_id: str,
        start_dt: datetime,
        end_dt: datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> None:
        """Raise if the proposed slot collides with an existing booking.

        Pure collision check against the calendar as it stands — no rules,
        no buffers. ``list_overlapping`` already excludes cancelled
        appointments and treats back-to-back as non-overlapping.
        """
        conflicts = self._repo.list_overlapping(
            user_id, start_dt, end_dt, exclude_appointment_id=exclude_appointment_id
        )
        if conflicts:
            conflicting = conflicts[0]
            raise AppointmentConflictError(
                f"Conflicts with an existing appointment at {conflicting.start_at.isoformat()}"
            )

    def create_appointment(
        self,
        user_id: str,
        *,
        data: dict[str, str | int | datetime | None],
        tz: tzinfo = UTC,
    ) -> Appointment:
        """Create a single appointment.

        Required keys in data: patient_id, title, start_at, end_at, duration_minutes.
        Optional: session_type, video_link, video_platform, notes.

        ``tz`` is the zone availability rules are evaluated in — see
        ``AvailabilityEngine.check_conflicts``. Defaults to UTC.
        """
        patient_id = data.get("patient_id", "")
        if not patient_id:
            raise InvalidAppointmentError("patient_id is required")
        start_at = data.get("start_at")
        end_at = data.get("end_at")
        if not start_at or not end_at:
            raise InvalidAppointmentError("start_at and end_at are required")
        if not isinstance(start_at, datetime | str) or not isinstance(end_at, datetime | str):
            raise InvalidAppointmentError("start_at and end_at must be datetimes")
        duration_minutes = data.get("duration_minutes", 0)
        if not isinstance(duration_minutes, int) or duration_minutes <= 0:
            raise InvalidAppointmentError("duration_minutes must be positive")

        start_dt = _as_datetime(start_at, tz)
        end_dt = _as_datetime(end_at, tz)

        # Request mode. A surface that cannot commit the diary on its own asks
        # for PENDING and supplies the instant the request stops holding its
        # slot; everything else gets the confirmed booking it always got, so no
        # existing caller changes behaviour by upgrading.
        status = self._requested_status(data.get("status"))
        pending_expires_at = self._pending_expiry(status, data.get("pending_expires_at"))

        self._reject_if_overlapping(user_id, start_dt, end_dt)
        self.rule_warnings = self._check_availability_rules(user_id, start_dt, end_dt, tz)

        now = _now()
        appointment = Appointment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=str(patient_id),
            title=str(data.get("title", "")),
            start_at=start_dt,
            end_at=end_dt,
            duration_minutes=duration_minutes,
            status=status,
            pending_expires_at=pending_expires_at,
            confirmation_token_hash=data.get("confirmation_token_hash"),  # type: ignore[arg-type]
            session_type=str(data.get("session_type", "individual")),
            video_link=data.get("video_link"),  # type: ignore[arg-type]
            video_platform=data.get("video_platform"),  # type: ignore[arg-type]
            notes=data.get("notes"),  # type: ignore[arg-type]
            note_type=str(data.get("note_type") or "soap"),
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

    def list_appointments(
        self, user_id: str, start: str, end: str, *, tz: tzinfo = UTC
    ) -> list[Appointment]:
        """List appointments in a date range.

        ``start``/``end`` without an offset are read as wall-clock in ``tz``,
        so "today" for a New York practice is that practice's midnight-to-
        midnight rather than UTC's. Defaults to UTC.
        """
        return self._repo.list_by_range(user_id, _to_utc(start, tz), _to_utc(end, tz))

    def update_appointment(
        self,
        appointment_id: str,
        user_id: str,
        *,
        tz: tzinfo = UTC,
        **updates: str | int | bool | None,
    ) -> Appointment:
        """Update fields on an existing appointment.

        ``tz`` is the zone availability rules are evaluated in — see
        ``AvailabilityEngine.check_conflicts``. Defaults to UTC.
        """
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
            "note_type",
            "status",
            "session_id",
            "google_event_id",
            "google_sync_status",
            "service_code",
            "modifiers",
            "unit_count",
            "place_of_service",
            "diagnosis_codes",
        }
        for field, value in updates.items():
            if field not in allowed_fields:
                raise InvalidAppointmentError(f"Cannot update field: {field}")
            setattr(appointment, field, value)

        # Only re-check collision and availability rules when a time field
        # actually moved — the internal start-session call attaches
        # session_id with no time fields at all and must never see a 409 or
        # 422 here.
        self.rule_warnings = []
        if {"start_at", "end_at", "duration_minutes"} & updates.keys():
            appointment.start_at = _as_datetime(appointment.start_at, tz)
            appointment.end_at = _as_datetime(appointment.end_at, tz)
            self._reject_if_overlapping(
                user_id,
                appointment.start_at,
                appointment.end_at,
                exclude_appointment_id=appointment_id,
            )
            self.rule_warnings = self._check_availability_rules(
                user_id, appointment.start_at, appointment.end_at, tz
            )

        appointment.updated_at = _now()

        # If this appointment is part of a recurring series
        # and was individually edited, mark as exception
        if appointment.recurring_appointment_id:
            appointment.is_exception = True

        return self._repo.update(appointment)

    @staticmethod
    def _requested_status(raw: object) -> AppointmentStatus:
        """The status a create may ask for — confirmed (the default) or pending.

        Nothing else: an appointment cannot be born cancelled, completed or a
        no-show, and letting a caller say so would put the calendar into states
        the rest of the engine has no path to.
        """
        if raw is None:
            return AppointmentStatus.CONFIRMED
        creatable = {AppointmentStatus.CONFIRMED, AppointmentStatus.PENDING}
        try:
            status = AppointmentStatus(str(raw))
        except ValueError:
            raise InvalidAppointmentError(f"Unknown appointment status: {raw}") from None
        if status not in creatable:
            raise InvalidAppointmentError(f"Cannot create an appointment as {status}")
        return status

    @staticmethod
    def _pending_expiry(status: AppointmentStatus, raw: object) -> datetime | None:
        """When a pending request stops holding its slot.

        REQUIRED for a pending appointment, and refused for any other. A
        pending request occupies its slot exactly like a confirmed one, so one
        created without an expiry holds a therapist's hour until somebody
        notices — which, on a queue nobody is watching, is never. Making it
        mandatory here means the footgun cannot be loaded rather than being
        documented and then forgotten.
        """
        if status is not AppointmentStatus.PENDING:
            if raw is not None:
                raise InvalidAppointmentError(
                    "pending_expires_at is only meaningful for a pending appointment"
                )
            return None
        if raw is None:
            raise InvalidAppointmentError("A pending appointment must carry pending_expires_at")
        if isinstance(raw, datetime):
            return raw
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

    def confirm_appointment(self, appointment_id: str, user_id: str) -> Appointment:
        """Accept a pending request: it becomes an ordinary booking.

        The expiry goes with it — once confirmed there is nothing left to
        expire, and leaving the timestamp behind would arm the sweep against a
        real appointment.
        """
        appointment = self.get_appointment(appointment_id, user_id)
        if appointment.status != AppointmentStatus.PENDING:
            raise InvalidAppointmentError(
                f"Only a pending appointment can be confirmed (this one is {appointment.status})"
            )
        appointment.status = AppointmentStatus.CONFIRMED
        appointment.pending_expires_at = None
        appointment.updated_at = _now()
        return self._repo.update(appointment)

    def expire_pending_appointments(self, user_id: str) -> list[Appointment]:
        """Release the slots held by requests nobody answered in time.

        Cancels rather than deletes, so the request is still visible as
        something that was asked for and lapsed — which is what a patient who
        rings back to ask "did you get my request?" needs somebody to be able
        to see. Returns what it expired so the caller can tell whoever asked.
        """
        expired: list[Appointment] = []
        for appointment in self._repo.list_expired_pending(user_id, _now()):
            appointment.status = AppointmentStatus.CANCELLED
            appointment.pending_expires_at = None
            appointment.updated_at = _now()
            expired.append(self._repo.update(appointment))
        return expired

    def cancel_appointment(self, appointment_id: str, user_id: str) -> Appointment:
        """Cancel a single appointment."""
        appointment = self.get_appointment(appointment_id, user_id)
        appointment.status = AppointmentStatus.CANCELLED
        appointment.pending_expires_at = None
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
        data: dict[str, str | int | datetime | None],
        recurrence: dict[str, str | int | None],
        tz: tzinfo = UTC,
    ) -> list[Appointment]:
        """Create a recurring appointment series using fan-out pattern.

        data: appointment fields (patient_id, title, start_at, end_at, etc.)
        recurrence: keys frequency, timezone, end_date (optional), count (optional)

        ``tz`` is the zone availability rules are evaluated in for each
        occurrence — see ``AvailabilityEngine.check_conflicts``. Independent
        of ``recurrence["timezone"]``, which drives occurrence expansion.
        Defaults to UTC.
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

        # One colliding occurrence fails the whole series rather than
        # silently dropping it: nothing is written via create_batch until
        # every occurrence has cleared the collision check, so a rejection
        # here leaves the calendar untouched instead of half-booked.
        appointments: list[Appointment] = []
        warnings: list[str] = []
        for idx, occ_start in enumerate(occurrences):
            occ_end = occ_start + appt_duration
            # RecurrenceGenerator returns naive-UTC datetimes; the repo's
            # overlap query and the rule check both compare them against
            # aware timestamps from the non-recurring create path, so they
            # need a UTC tzinfo to compare / localize.
            occ_start_aware = (
                occ_start.replace(tzinfo=UTC) if occ_start.tzinfo is None else occ_start
            )
            occ_end_aware = occ_end.replace(tzinfo=UTC) if occ_end.tzinfo is None else occ_end
            self._reject_if_overlapping(user_id, occ_start_aware, occ_end_aware)
            warnings.extend(
                self._check_availability_rules(user_id, occ_start_aware, occ_end_aware, tz)
            )
            appt = Appointment(
                id=master_id if idx == 0 else str(uuid.uuid4()),
                user_id=user_id,
                patient_id=str(patient_id),
                title=str(data.get("title", "")),
                start_at=occ_start,
                end_at=occ_end,
                duration_minutes=duration_minutes,
                status=AppointmentStatus.CONFIRMED,
                session_type=str(data.get("session_type", "individual")),
                video_link=data.get("video_link"),  # type: ignore[arg-type]
                video_platform=data.get("video_platform"),  # type: ignore[arg-type]
                notes=data.get("notes"),  # type: ignore[arg-type]
                note_type=str(data.get("note_type") or "soap"),
                recurrence_rule=rrule_str,
                recurring_appointment_id=master_id,
                recurrence_index=idx,
                created_at=now,
                updated_at=now,
            )
            appointments.append(appt)

        self.rule_warnings = warnings
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
        allowed_fields = {
            "title",
            "session_type",
            "video_link",
            "video_platform",
            "notes",
            "note_type",
        }
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
