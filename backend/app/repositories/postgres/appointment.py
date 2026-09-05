# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL appointment repository implementation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import String, Uuid, bindparam, func, or_, select, text, update

from ...db.models import AppointmentRow, PatientClinicianRow
from ...scheduling_engine.models.appointment import Appointment, AppointmentStatus
from ...scheduling_engine.repositories.appointment import AppointmentRepository
from ...utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _grant_filters(user_id: str) -> tuple:
    return (
        PatientClinicianRow.user_id == user_id,
        or_(
            PatientClinicianRow.expires_at.is_(None),
            PatientClinicianRow.expires_at > utc_now(),
        ),
    )


class PostgresAppointmentRepository(AppointmentRepository):
    """Appointment access model — hybrid of calendar-ownership and patient-access.

    * ``user_id`` on ``appointments`` stays as "the clinician on whose
      calendar this slot lives." Used by ``list_by_range`` (the "my
      calendar" view), ``list_by_recurring_id``, and
      ``list_by_ical_source`` — these are intrinsically clinician-
      personal slices.

    * Patient-scoped reads (``get``, ``list_by_patient``) authorize
      via ``has_patient_access`` so a covering / co-treating /
      supervisor clinician can see appointments for a patient they're
      authorized to treat, regardless of which clinician owns the
      calendar slot. Closes the matching gap to therapy_sessions.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        row = self._session.execute(
            select(AppointmentRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == AppointmentRow.patient_id,
            )
            .where(
                AppointmentRow.id == appointment_id,
                *_grant_filters(user_id),
            )
        ).scalar_one_or_none()
        return _row_to_appointment(row) if row else None

    def list_by_range(
        self, user_id: str, start: str | datetime, end: str | datetime
    ) -> list[Appointment]:
        """List appointments on ``user_id``'s calendar in the date range.

        This is the "my calendar" slice — filters on the appointment's
        own ``user_id`` (the calendar owner), not via ``patient_clinicians``.
        Multi-clinician practices don't merge calendars by default; if a
        coverage clinician needs to see the primary's calendar, that's a
        future per-call elevation, not the default view.
        """
        rows = (
            self._session.execute(
                select(AppointmentRow)
                .where(
                    AppointmentRow.user_id == user_id,
                    AppointmentRow.start_at >= start,
                    AppointmentRow.start_at < end,
                )
                .order_by(AppointmentRow.start_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_appointment(r) for r in rows]

    def list_overlapping(
        self,
        user_id: str,
        start: str | datetime,
        end: str | datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> list[Appointment]:
        """List non-cancelled appointments on ``user_id``'s calendar that
        overlap ``[start, end)`` — the "my calendar" slice, matching
        :meth:`list_by_range`'s ownership filter rather than
        ``patient_clinicians``.
        """
        query = select(AppointmentRow).where(
            AppointmentRow.user_id == user_id,
            AppointmentRow.status != "cancelled",
            AppointmentRow.start_at < end,
            AppointmentRow.end_at > start,
        )
        if exclude_appointment_id is not None:
            query = query.where(AppointmentRow.id != exclude_appointment_id)
        rows = self._session.execute(query.order_by(AppointmentRow.start_at)).scalars().all()
        return [_row_to_appointment(r) for r in rows]

    def list_by_patient(self, user_id: str, patient_id: str) -> list[Appointment]:
        """List all appointments for a patient that ``user_id`` can see.

        Patient-scoped — returns all appointments for the patient
        regardless of which clinician owns the calendar slot, gated by
        ``has_patient_access``.
        """
        if not self._has_patient_access(patient_id, user_id):
            return []
        rows = (
            self._session.execute(
                select(AppointmentRow)
                .where(
                    AppointmentRow.patient_id == patient_id,
                )
                .order_by(AppointmentRow.start_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_appointment(r) for r in rows]

    def get_by_session_ids(self, session_ids: list[str], user_id: str) -> dict[str, Appointment]:
        if not session_ids:
            return {}
        rows = (
            self._session.execute(
                select(AppointmentRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == AppointmentRow.patient_id,
                )
                .where(
                    AppointmentRow.session_id.in_(session_ids),
                    *_grant_filters(user_id),
                )
            )
            .scalars()
            .all()
        )
        return {row.session_id: _row_to_appointment(row) for row in rows if row.session_id}

    def count_by_range(self, user_id: str, start: str | datetime, end: str | datetime) -> int:
        """Aggregate variant of :meth:`list_by_range` — one COUNT, no rows
        materialised, valid under column-scoped grants."""
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AppointmentRow)
                .where(
                    AppointmentRow.user_id == user_id,
                    AppointmentRow.start_at >= start,
                    AppointmentRow.start_at < end,
                )
            )
            or 0
        )

    def start_times_by_patient(self, user_id: str, patient_id: str) -> list[datetime]:
        """Timestamp-only variant of :meth:`list_by_patient`."""
        if not self._has_patient_access(patient_id, user_id):
            return []
        rows = self._session.execute(
            select(AppointmentRow.start_at)
            .where(AppointmentRow.patient_id == patient_id)
            .order_by(AppointmentRow.start_at)
        ).all()
        return [r[0] for r in rows]

    def _has_patient_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    def list_by_recurring_id(
        self, user_id: str, recurring_appointment_id: str, after: str | datetime | None = None
    ) -> list[Appointment]:
        query = select(AppointmentRow).where(
            AppointmentRow.user_id == user_id,
            AppointmentRow.recurring_appointment_id == recurring_appointment_id,
        )
        if after:
            query = query.where(AppointmentRow.start_at >= after)
        rows = self._session.execute(query.order_by(AppointmentRow.start_at)).scalars().all()
        return [_row_to_appointment(r) for r in rows]

    def list_by_ical_source(self, user_id: str, ehr_system: str) -> list[Appointment]:
        rows = (
            self._session.execute(
                select(AppointmentRow)
                .where(
                    AppointmentRow.user_id == user_id,
                    AppointmentRow.ical_source == ehr_system,
                )
                .order_by(AppointmentRow.start_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_appointment(r) for r in rows]

    def get_by_google_event_id(
        self,
        user_id: str,
        google_event_id: str,
    ) -> Appointment | None:
        row = self._session.execute(
            select(AppointmentRow).where(
                AppointmentRow.user_id == user_id,
                AppointmentRow.google_event_id == google_event_id,
            )
        ).scalar_one_or_none()
        return _row_to_appointment(row) if row else None

    def list_expired_pending(self, user_id: str, now: datetime) -> list[Appointment]:
        """Pending requests on ``user_id``'s calendar whose expiry has passed.

        The calendar-owner slice, like ``list_by_range`` — expiring a stale
        request is housekeeping on your own diary, not a patient query.
        """
        rows = (
            self._session.execute(
                select(AppointmentRow)
                .where(
                    AppointmentRow.user_id == user_id,
                    AppointmentRow.status == AppointmentStatus.PENDING,
                    AppointmentRow.pending_expires_at.is_not(None),
                    AppointmentRow.pending_expires_at <= now,
                )
                .order_by(AppointmentRow.pending_expires_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_appointment(r) for r in rows]

    def get_by_confirmation_token_hash(self, user_id: str, token_hash: str) -> Appointment | None:
        row = self._session.execute(
            select(AppointmentRow).where(
                AppointmentRow.user_id == user_id,
                AppointmentRow.confirmation_token_hash == token_hash,
            )
        ).scalar_one_or_none()
        return _row_to_appointment(row) if row else None

    def create(self, appointment: Appointment) -> Appointment:
        row = AppointmentRow()
        _appointment_to_row(appointment, row)
        # SAVEPOINT, not a bare flush: a slot collision (unique active-slot
        # index) must undo this INSERT and nothing else, leaving the caller's
        # session usable to translate the error rather than stuck aborted.
        with self._session.begin_nested():
            self._session.add(row)
            self._session.flush()
        return appointment

    def create_batch(self, appointments: list[Appointment]) -> list[Appointment]:
        for appt in appointments:
            row = AppointmentRow()
            _appointment_to_row(appt, row)
            self._session.add(row)
        self._session.flush()
        return appointments

    def update(self, appointment: Appointment) -> Appointment:
        row = self._session.get(AppointmentRow, appointment.id)
        if row is None:
            row = AppointmentRow()
            self._session.add(row)
        _appointment_to_row(appointment, row)
        self._session.flush()
        return appointment

    def bulk_set_patient(self, appointment_ids: list[str], patient_id: str) -> int:
        if not appointment_ids:
            return 0
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(AppointmentRow)
                .where(AppointmentRow.id.in_(appointment_ids))
                .values(patient_id=patient_id, updated_at=utc_now())
            ),
        )
        self._session.flush()
        return result.rowcount or 0

    def delete(self, appointment_id: str, user_id: str) -> bool:
        """Delete an appointment.

        Gated by ``has_patient_access`` (not appointment ownership) so a
        covering clinician can cancel an appointment for their patient
        even if the original calendar slot was owned by the primary.
        """
        row = self._session.get(AppointmentRow, appointment_id)
        if row is None:
            return False
        if not self._has_patient_access(row.patient_id, user_id):
            return False
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_appointment(row: AppointmentRow) -> Appointment:
    return Appointment(
        id=row.id,
        user_id=row.user_id,
        patient_id=row.patient_id,
        title=row.title,
        start_at=row.start_at,
        end_at=row.end_at,
        duration_minutes=row.duration_minutes,
        status=row.status,
        session_type=row.session_type,
        appointment_type_id=row.appointment_type_id,
        video_link=row.video_link,
        video_platform=row.video_platform,
        notes=row.notes,
        note_type=row.note_type,
        recurrence_rule=row.recurrence_rule,
        recurring_appointment_id=row.recurring_appointment_id,
        recurrence_index=row.recurrence_index,
        is_exception=row.is_exception,
        google_event_id=row.google_event_id,
        google_calendar_id=row.google_calendar_id,
        google_sync_status=row.google_sync_status,
        ical_uid=row.ical_uid,
        ical_source=row.ical_source,
        ical_sync_status=row.ical_sync_status,
        ehr_appointment_url=row.ehr_appointment_url,
        session_id=row.session_id,
        service_code=row.service_code,
        modifiers=row.modifiers,
        unit_count=row.unit_count,
        place_of_service=row.place_of_service,
        diagnosis_codes=row.diagnosis_codes,
        reminder_24h_sent=row.reminder_24h_sent,
        reminder_1h_sent=row.reminder_1h_sent,
        pending_expires_at=row.pending_expires_at,
        confirmation_token_hash=row.confirmation_token_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _appointment_to_row(appt: Appointment, row: AppointmentRow) -> None:
    row.id = appt.id
    row.user_id = appt.user_id
    row.patient_id = appt.patient_id
    row.title = appt.title
    row.start_at = appt.start_at
    row.end_at = appt.end_at
    row.duration_minutes = appt.duration_minutes
    row.status = appt.status
    row.session_type = appt.session_type
    row.appointment_type_id = appt.appointment_type_id
    row.video_link = appt.video_link
    row.video_platform = appt.video_platform
    row.notes = appt.notes
    row.note_type = appt.note_type
    row.recurrence_rule = appt.recurrence_rule
    row.recurring_appointment_id = appt.recurring_appointment_id
    row.recurrence_index = appt.recurrence_index
    row.is_exception = appt.is_exception
    row.google_event_id = appt.google_event_id
    row.google_calendar_id = appt.google_calendar_id
    row.google_sync_status = appt.google_sync_status
    row.ical_uid = appt.ical_uid
    row.ical_source = appt.ical_source
    row.ical_sync_status = appt.ical_sync_status
    row.ehr_appointment_url = appt.ehr_appointment_url
    row.session_id = appt.session_id
    row.service_code = appt.service_code
    row.modifiers = appt.modifiers
    row.unit_count = appt.unit_count
    row.place_of_service = appt.place_of_service
    row.diagnosis_codes = appt.diagnosis_codes
    row.reminder_24h_sent = appt.reminder_24h_sent
    row.reminder_1h_sent = appt.reminder_1h_sent
    row.pending_expires_at = appt.pending_expires_at
    row.confirmation_token_hash = appt.confirmation_token_hash
    row.created_at = appt.created_at
    row.updated_at = appt.updated_at
