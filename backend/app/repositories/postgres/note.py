# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL NotesRepository implementation.

Every method calls the schema-local ``has_patient_access(patient_id,
user_id)`` SQL function to decide whether the requesting clinician has
a grant for the note's patient. The function reads the
``patient_clinicians`` table (see migration ``777b846ab944``).

The check happens in the same SQL statement as the read where possible
(via a join through ``patient_clinicians``), so the database can use
``EXISTS`` short-circuiting and avoid serializing across two
round-trips. Writes do a guard query first because the row may not
exist yet (insert path) or may need to be loaded for access check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, text

from ...db.models import NoteRow, PatientClinicianRow
from ...models.note import Note
from ...utcnow import utc_now
from ..note import NotesRepository, PatientAccessDeniedError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresNotesRepository(NotesRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- internal access predicate ---

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        """Application-layer mirror of the RLS policy.

        We call the SQL function rather than re-implementing the
        ``patient_clinicians`` lookup in Python so the predicate has a
        single definition. Mismatches between app and DB authorization
        is exactly the failure mode we're trying to prevent.
        """
        result = self._session.execute(
            text("SELECT has_patient_access(:pid::uuid, :uid)"),
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    # --- reads ---

    def get(self, note_id: str, user_id: str) -> Note | None:
        """Fetch the note if it exists, is live, and the user has a grant.

        Single-query join through ``patient_clinicians`` so a denied
        request is indistinguishable from a missing row at the SQL
        layer — no existence oracle.
        """
        row = (
            self._session.query(NoteRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == NoteRow.patient_id,
            )
            .filter(
                NoteRow.id == note_id,
                NoteRow.deleted_at.is_(None),
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
            .one_or_none()
        )
        return _row_to_note(row) if row else None

    def get_by_session_id(self, session_id: str, user_id: str) -> Note | None:
        row = (
            self._session.query(NoteRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == NoteRow.patient_id,
            )
            .filter(
                NoteRow.session_id == session_id,
                NoteRow.deleted_at.is_(None),
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
            .one_or_none()
        )
        return _row_to_note(row) if row else None

    def list_by_patient(self, patient_id: str, user_id: str) -> list[Note]:
        # Top-level access gate: if the user can't read this patient,
        # return [] without enumerating any rows. Avoids leaking a
        # has-notes-vs-no-notes signal via timing.
        if not self._has_access(patient_id, user_id):
            return []
        rows = (
            self._session.query(NoteRow)
            .filter(
                NoteRow.patient_id == patient_id,
                NoteRow.deleted_at.is_(None),
            )
            .order_by(
                NoteRow.finalized_at.desc().nullslast(),
                NoteRow.created_at.desc(),
            )
            .all()
        )
        return [_row_to_note(r) for r in rows]

    # --- writes ---

    def add(self, note: Note, user_id: str) -> Note:
        if not self._has_access(note.patient_id, user_id):
            raise PatientAccessDeniedError(note.patient_id, user_id)
        row = NoteRow()
        _note_to_row(note, row)
        self._session.add(row)
        self._session.flush()
        return note

    def update(self, note: Note, user_id: str) -> Note:
        if not self._has_access(note.patient_id, user_id):
            raise PatientAccessDeniedError(note.patient_id, user_id)
        row = self._session.get(NoteRow, note.id)
        if row is None:
            row = NoteRow()
            self._session.add(row)
        elif not self._has_access(row.patient_id, user_id):
            # Defense-in-depth: if the on-disk patient_id differs from
            # the in-memory note (e.g., caller forged note.patient_id),
            # check the DB-side value too. Either grant denies the write.
            raise PatientAccessDeniedError(row.patient_id, user_id)
        _note_to_row(note, row)
        self._session.flush()
        return note

    def delete(self, note_id: str, user_id: str) -> None:
        """Soft-delete the note (THERAPY-nyb).

        No-op if the row is missing, already soft-deleted, or the user
        has no grant — matches the read-side "indistinguishable from
        absent" contract.
        """
        row = self._session.get(NoteRow, note_id)
        if row is None or row.deleted_at is not None:
            return
        if not self._has_access(row.patient_id, user_id):
            return
        row.deleted_at = utc_now()
        self._session.flush()

    def _physical_delete(self, note_id: str) -> None:
        """Internal — purge cron only (THERAPY-cgy). Not HTTP-exposed.

        Bypasses the access check by design: the cron runs as a system
        identity that legitimately has no per-patient grant. Callers
        outside ``backend/app/jobs/`` must not use this.
        """
        row = self._session.get(NoteRow, note_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()


def _row_to_note(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        patient_id=row.patient_id,
        session_id=row.session_id,
        note_type=row.note_type,
        content=row.content,
        content_edited=row.content_edited,
        finalized_at=row.finalized_at,
        quality_rating=row.quality_rating,
        quality_rating_reason=row.quality_rating_reason,
        quality_rating_sections=row.quality_rating_sections,
        export_status=row.export_status,
        export_queued_at=row.export_queued_at,
        export_reviewed_at=row.export_reviewed_at,
        export_reviewed_by=row.export_reviewed_by,
        exported_at=row.exported_at,
        redacted_content=row.redacted_content,
        naturalized_content=row.naturalized_content,
        redacted_export_payload=row.redacted_export_payload,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _note_to_row(note: Note, row: NoteRow) -> None:
    row.id = note.id
    row.patient_id = note.patient_id
    row.session_id = note.session_id
    row.note_type = note.note_type
    row.content = note.content
    row.content_edited = note.content_edited
    row.finalized_at = note.finalized_at
    row.quality_rating = note.quality_rating
    row.quality_rating_reason = note.quality_rating_reason
    row.quality_rating_sections = note.quality_rating_sections
    row.export_status = note.export_status
    row.export_queued_at = note.export_queued_at
    row.export_reviewed_at = note.export_reviewed_at
    row.export_reviewed_by = note.export_reviewed_by
    row.exported_at = note.exported_at
    row.redacted_content = note.redacted_content
    row.naturalized_content = note.naturalized_content
    row.redacted_export_payload = note.redacted_export_payload
    row.created_at = note.created_at
    row.updated_at = note.updated_at
