# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient repository implementation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ...db.models import NoteRow, PatientRow, TherapySessionRow
from ...models import Patient
from ...utcnow import utc_now
from ..patient import PatientRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresPatientRepository(PatientRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        # User-facing reads filter out soft-deleted rows (THERAPY-nyb).
        # Audit lookups bypass this repo and query AuditLogRow directly,
        # so dangling-reference resolution still works.
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id or row.deleted_at is not None:
            return None
        return _row_to_patient(row)

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        if not patient_ids:
            return {}
        rows = (
            self._session.query(PatientRow)
            .filter(
                PatientRow.id.in_(patient_ids),
                PatientRow.user_id == user_id,
                PatientRow.deleted_at.is_(None),
            )
            .all()
        )
        return {r.id: _row_to_patient(r) for r in rows}

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        search_by: str = "last_name",
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        query = self._session.query(PatientRow).filter(
            PatientRow.user_id == user_id,
            PatientRow.deleted_at.is_(None),
        )

        if search:
            search_lower = search.lower()
            if search_by == "first_name":
                query = query.filter(PatientRow.first_name_lower.startswith(search_lower))
            else:
                query = query.filter(PatientRow.last_name_lower.startswith(search_lower))

        query = query.order_by(PatientRow.last_name_lower, PatientRow.first_name_lower)

        total = query.count()
        offset = (page - 1) * page_size
        rows = query.offset(offset).limit(page_size).all()
        return [_row_to_patient(r) for r in rows], total

    def create(self, patient: Patient) -> Patient:
        row = PatientRow()
        _patient_to_row(patient, row)
        self._session.add(row)
        self._session.flush()
        return patient

    def update(self, patient: Patient) -> Patient:
        patient.updated_at = utc_now()
        patient.first_name_lower = patient.first_name.lower()
        patient.last_name_lower = patient.last_name.lower()
        row = self._session.get(PatientRow, patient.id)
        if row is None:
            row = PatientRow()
            self._session.add(row)
        _patient_to_row(patient, row)
        self._session.flush()
        return patient

    def delete(self, patient_id: str, user_id: str) -> bool:
        """Soft-delete the patient and cascade to its therapy sessions and notes.

        Sets ``deleted_at = NOW()`` on each row instead of removing it from
        disk. The day-30 purge cron (THERAPY-cgy) is the only path that
        physically removes rows; HTTP must never call ``_physical_delete``.

        Cascade order matches the prior hard-delete:
            therapy_sessions → notes → patients

        Returns False if the row is already gone or already soft-deleted —
        in both cases the caller's invariant ("nothing live with this id"
        ) is satisfied without further work.
        """
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id or row.deleted_at is not None:
            return False

        now = utc_now()
        # Cascade: soft-delete therapy sessions for this patient. Skip
        # rows already soft-deleted so deleted_at reflects the *first*
        # delete, not the latest one.
        self._session.query(TherapySessionRow).filter(
            TherapySessionRow.patient_id == patient_id,
            TherapySessionRow.deleted_at.is_(None),
        ).update({TherapySessionRow.deleted_at: now}, synchronize_session=False)
        # Cascade: notes are patient-scoped. Same idempotency guard.
        self._session.query(NoteRow).filter(
            NoteRow.patient_id == patient_id,
            NoteRow.deleted_at.is_(None),
        ).update({NoteRow.deleted_at: now}, synchronize_session=False)
        row.deleted_at = now
        self._session.flush()
        return True

    # ─── Recently-deleted listing + restore (THERAPY-yg2) ──────────────

    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """Soft-deleted patients still inside the undo window.

        Mirrors the partial index ``WHERE deleted_at IS NOT NULL`` on
        ``patients`` (THERAPY-nyb migration). Rows past the window stay
        on disk until the day-30 hard-purge cron physically removes
        them but no longer surface here, so the UI hides them as
        "permanently removed."

        Returns ``(patient, deleted_at)`` pairs — ``deleted_at`` is
        carried out-of-band because it isn't on the ``Patient``
        dataclass.
        """
        cutoff = utc_now() - timedelta(days=window_days)
        rows = (
            self._session.query(PatientRow)
            .filter(
                PatientRow.user_id == user_id,
                PatientRow.deleted_at.isnot(None),
                PatientRow.deleted_at > cutoff,
            )
            .order_by(PatientRow.last_name_lower, PatientRow.first_name_lower)
            .all()
        )
        return [(_row_to_patient(r), r.deleted_at) for r in rows if r.deleted_at is not None]

    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Reverse a soft-delete by clearing ``deleted_at``.

        Returns ``None`` if the row is not soft-deleted, not owned by
        this user, or already past the undo window. The cascade clears
        ``deleted_at`` only on therapy_sessions / notes whose stamp
        matches the patient's — i.e. rows the original delete cascade
        knocked over together. Sessions or notes that were soft-deleted
        independently before the patient delete keep their own stamps,
        which is what users expect: undoing the patient delete does not
        revive earlier per-row deletes.

        Session numbers are unaffected: ``session_number`` is a stored
        column, and the next-number generator
        (``get_session_number_for_patient``) deliberately ignores
        ``deleted_at``, so numbering is monotonic across this round
        trip (THERAPY-nyb).
        """
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id or row.deleted_at is None:
            return None
        cutoff = utc_now() - timedelta(days=window_days)
        if row.deleted_at <= cutoff:
            return None

        patient_stamp = row.deleted_at
        # Cascade: only undo rows whose deleted_at matches the patient's
        # tombstone — those are the ones the patient delete cascaded
        # onto. Earlier independent per-row soft-deletes stay tombstoned.
        self._session.query(TherapySessionRow).filter(
            TherapySessionRow.patient_id == patient_id,
            TherapySessionRow.deleted_at == patient_stamp,
        ).update({TherapySessionRow.deleted_at: None}, synchronize_session=False)
        self._session.query(NoteRow).filter(
            NoteRow.patient_id == patient_id,
            NoteRow.deleted_at == patient_stamp,
        ).update({NoteRow.deleted_at: None}, synchronize_session=False)
        row.deleted_at = None
        self._session.flush()
        return _row_to_patient(row)

    # ─── Internal — purge cron only (THERAPY-cgy) ──────────────────────
    # Not exposed via HTTP. The day-30 purge cron will call this to
    # physically remove rows whose ``deleted_at`` is past the retention
    # window. Keeping it on the repo (vs. raw SQL in the cron) preserves
    # the cascade order audit-log readers depend on.

    def _physical_delete(self, patient_id: str, user_id: str) -> bool:
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id:
            return False
        # Mirror cascade order from soft-delete.
        self._session.query(NoteRow).filter(NoteRow.patient_id == patient_id).delete(
            synchronize_session=False
        )
        self._session.query(TherapySessionRow).filter(
            TherapySessionRow.patient_id == patient_id
        ).delete(synchronize_session=False)
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_patient(row: PatientRow) -> Patient:
    return Patient(
        id=row.id,
        user_id=row.user_id,
        first_name=row.first_name,
        last_name=row.last_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        first_name_lower=row.first_name_lower,
        last_name_lower=row.last_name_lower,
        session_count=row.session_count,
        email=row.email,
        phone=row.phone,
        status=row.status,
        date_of_birth=row.date_of_birth,
        diagnosis=row.diagnosis,
        last_session_date=row.last_session_date,
        next_session_date=row.next_session_date,
    )


def _patient_to_row(patient: Patient, row: PatientRow) -> None:
    row.id = patient.id
    row.user_id = patient.user_id
    row.first_name = patient.first_name
    row.last_name = patient.last_name
    row.first_name_lower = patient.first_name_lower
    row.last_name_lower = patient.last_name_lower
    row.email = patient.email
    row.phone = patient.phone
    row.status = patient.status
    row.date_of_birth = patient.date_of_birth
    row.diagnosis = patient.diagnosis
    row.session_count = patient.session_count
    row.last_session_date = patient.last_session_date
    row.next_session_date = patient.next_session_date
    row.created_at = patient.created_at
    row.updated_at = patient.updated_at
