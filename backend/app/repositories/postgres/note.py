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

from dataclasses import asdict
from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, func, or_, select, text

from ...db.models import NoteAddendumRow, NoteRow, NoteSignatureRow, PatientClinicianRow
from ...models.note import Note
from ...models.note_signing import NoteAddendum, NoteSignature
from ...utcnow import utc_now
from ..note import NotesRepository, PatientAccessDeniedError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


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
            _HAS_PATIENT_ACCESS_SQL,
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
        row = self._session.execute(
            select(NoteRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == NoteRow.patient_id,
            )
            .where(
                NoteRow.id == note_id,
                NoteRow.deleted_at.is_(None),
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
        ).scalar_one_or_none()
        return _row_to_note(row) if row else None

    def get_by_session_id(self, session_id: str, user_id: str) -> Note | None:
        row = self._session.execute(
            select(NoteRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == NoteRow.patient_id,
            )
            .where(
                NoteRow.session_id == session_id,
                NoteRow.deleted_at.is_(None),
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
        ).scalar_one_or_none()
        return _row_to_note(row) if row else None

    def get_by_session_ids(self, session_ids: list[str], user_id: str) -> dict[str, Note]:
        if not session_ids:
            return {}
        rows = (
            self._session.execute(
                select(NoteRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == NoteRow.patient_id,
                )
                .where(
                    NoteRow.session_id.in_(session_ids),
                    NoteRow.deleted_at.is_(None),
                    PatientClinicianRow.user_id == user_id,
                    or_(
                        PatientClinicianRow.expires_at.is_(None),
                        PatientClinicianRow.expires_at > utc_now(),
                    ),
                )
            )
            .scalars()
            .all()
        )
        # session_id is a nullable column but the IN filter selects only the
        # requested (non-null) ids, so the guard is just for the type-checker.
        return {row.session_id: _row_to_note(row) for row in rows if row.session_id is not None}

    def list_by_patient(
        self, patient_id: str, user_id: str, *, limit: int | None = None
    ) -> list[Note]:
        # Top-level access gate: if the user can't read this patient,
        # return [] without enumerating any rows. Avoids leaking a
        # has-notes-vs-no-notes signal via timing.
        if not self._has_access(patient_id, user_id):
            return []
        query = (
            select(NoteRow)
            .where(
                NoteRow.patient_id == patient_id,
                NoteRow.deleted_at.is_(None),
            )
            .order_by(
                NoteRow.finalized_at.desc().nullslast(),
                NoteRow.created_at.desc(),
            )
        )
        if limit is not None:
            query = query.limit(limit)
        rows = self._session.execute(query).scalars().all()
        return [_row_to_note(r) for r in rows]

    def list_finalized(self, user_id: str, *, limit: int | None = None) -> list[Note]:
        query = (
            select(NoteRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == NoteRow.patient_id,
            )
            .where(
                NoteRow.session_id.is_not(None),
                # A restricted note never has a session, so this is
                # belt-and-braces: the billing queue documents a visit
                # from the progress note, never from a private one.
                NoteRow.restricted.is_(False),
                NoteRow.finalized_at.is_not(None),
                NoteRow.deleted_at.is_(None),
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
            .order_by(NoteRow.finalized_at.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        rows = self._session.execute(query).scalars().all()
        return [_row_to_note(r) for r in rows]

    def count_unfinalized(self, user_id: str) -> int:
        # Single-query join through patient_clinicians so the count covers
        # exactly the notes the user is granted on. session_id IS NOT NULL
        # restricts to session-attached notes ("awaiting your signature").
        return (
            self._session.scalar(
                select(func.count())
                .select_from(NoteRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == NoteRow.patient_id,
                )
                .where(
                    NoteRow.session_id.is_not(None),
                    NoteRow.finalized_at.is_(None),
                    NoteRow.deleted_at.is_(None),
                    PatientClinicianRow.user_id == user_id,
                    or_(
                        PatientClinicianRow.expires_at.is_(None),
                        PatientClinicianRow.expires_at > utc_now(),
                    ),
                )
            )
            or 0
        )

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

    # --- signed versions and addenda ---

    def list_signatures(self, note: Note, user_id: str) -> list[NoteSignature]:
        if not self._has_access(note.patient_id, user_id):
            return []
        rows = self._session.scalars(
            select(NoteSignatureRow)
            .where(NoteSignatureRow.note_id == note.id)
            .order_by(NoteSignatureRow.version)
        ).all()
        return [_row_to_signature(r) for r in rows]

    def add_signature(self, signature: NoteSignature, user_id: str) -> NoteSignature:
        if not self._has_access(signature.patient_id, user_id):
            raise PatientAccessDeniedError(signature.patient_id, user_id)
        self._session.add(NoteSignatureRow(**asdict(signature)))
        self._session.flush()
        return signature

    def record_unlock(self, signature: NoteSignature, user_id: str) -> NoteSignature:
        if not self._has_access(signature.patient_id, user_id):
            raise PatientAccessDeniedError(signature.patient_id, user_id)
        row = self._session.get(NoteSignatureRow, signature.id)
        if row is None:
            raise PatientAccessDeniedError(signature.patient_id, user_id)
        row.unlocked_at = signature.unlocked_at
        row.unlocked_by = signature.unlocked_by
        row.unlock_reason = signature.unlock_reason
        self._session.flush()
        return signature

    def list_addenda(self, note: Note, user_id: str) -> list[NoteAddendum]:
        if not self._has_access(note.patient_id, user_id):
            return []
        rows = self._session.scalars(
            select(NoteAddendumRow)
            .where(NoteAddendumRow.note_id == note.id)
            .order_by(NoteAddendumRow.created_at, NoteAddendumRow.id)
        ).all()
        return [_row_to_addendum(r) for r in rows]

    def add_addendum(self, addendum: NoteAddendum, user_id: str) -> NoteAddendum:
        if not self._has_access(addendum.patient_id, user_id):
            raise PatientAccessDeniedError(addendum.patient_id, user_id)
        self._session.add(NoteAddendumRow(**asdict(addendum)))
        self._session.flush()
        return addendum


def _row_to_signature(row: NoteSignatureRow) -> NoteSignature:
    return NoteSignature(
        id=row.id,
        note_id=row.note_id,
        patient_id=row.patient_id,
        version=row.version,
        note_type=row.note_type,
        note_type_version=row.note_type_version,
        content=row.content,
        content_edited=row.content_edited,
        digest=row.digest,
        signed_by=row.signed_by,
        signer_name=row.signer_name,
        signer_credentials=row.signer_credentials,
        signed_at=row.signed_at,
        unlocked_at=row.unlocked_at,
        unlocked_by=row.unlocked_by,
        unlock_reason=row.unlock_reason,
    )


def _row_to_addendum(row: NoteAddendumRow) -> NoteAddendum:
    return NoteAddendum(
        id=row.id,
        note_id=row.note_id,
        patient_id=row.patient_id,
        text=row.text,
        signer_name=row.signer_name,
        signer_credentials=row.signer_credentials,
        digest=row.digest,
        prev_digest=row.prev_digest,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _row_to_note(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        patient_id=row.patient_id,
        session_id=row.session_id,
        note_type=row.note_type,
        note_type_version=row.note_type_version,
        note_inputs=row.note_inputs,
        content=row.content,
        content_edited=row.content_edited,
        finalized_at=row.finalized_at,
        quality_rating=row.quality_rating,
        quality_rating_reason=row.quality_rating_reason,
        quality_rating_sections=row.quality_rating_sections,
        status=row.status,
        redacted_content=row.redacted_content,
        naturalized_content=row.naturalized_content,
        author_user_id=row.author_user_id,
        restricted=row.restricted,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _note_to_row(note: Note, row: NoteRow) -> None:
    row.id = note.id
    row.patient_id = note.patient_id
    row.session_id = note.session_id
    row.note_type = note.note_type
    row.note_type_version = note.note_type_version
    row.note_inputs = note.note_inputs
    row.content = note.content
    row.content_edited = note.content_edited
    row.finalized_at = note.finalized_at
    row.quality_rating = note.quality_rating
    row.quality_rating_reason = note.quality_rating_reason
    row.quality_rating_sections = note.quality_rating_sections
    row.status = note.status
    row.redacted_content = note.redacted_content
    row.naturalized_content = note.naturalized_content
    row.author_user_id = note.author_user_id
    row.restricted = note.restricted
    row.created_at = note.created_at
    row.updated_at = note.updated_at
