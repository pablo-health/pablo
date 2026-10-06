# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note business logic service.

Owns the lifecycle of clinical notes (create-from-generation, edit,
finalize). Notes are first-class and patient-owned;
:class:`SessionService` delegates note-flavored operations here so the
session row stays focused on recording metadata. See pa-0nx.

Every public method takes ``user_id`` (the clinician making the
request) and forwards it to :class:`NotesRepository`, which gates the
access via ``patient_clinicians``. Notes the requester has no grant
for surface as :class:`NoteNotFoundError` — matches the repo's
"indistinguishable from absent" contract so this layer doesn't leak
an existence oracle either.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..api_errors import APIError, BadRequestError, ConflictError, NotFoundError
from ..models import Note
from ..models.note_signing import NoteAddendum, NoteSignature
from ..notes import get_default_registry, is_practice_key
from ..notes.visit_times import apply_confirmed_window, clear_drafted_time, drafted_time
from ..repositories import NotesRepository  # noqa: TC001 — runtime DI type
from ..repositories.note import PatientAccessDeniedError
from ..utcnow import utc_now
from .note_signing import (
    AddendumTextRequiredError,
    NoteAlreadySignedError,
    NoteLockedError,
    NoteNotLockedError,
    NoteNotSignedError,
    UnlockReasonRequiredError,
    addendum_chain_link,
    clean_signer,
    current_signature,
    signature_digest,
)

if TYPE_CHECKING:
    from datetime import datetime


class NoteServiceError(APIError):
    """Base exception for note service errors."""


class NoteNotFoundError(NotFoundError):
    """Raised when a note is not found or the user has no access grant."""


class NoteAlreadyFinalizedError(ConflictError):
    """Raised when finalizing a note that is already finalized."""

    code = "NOTE_ALREADY_FINALIZED"


class NoteNotFinalizedError(BadRequestError):
    """Raised when an operation requires the note to be finalized first."""

    code = "NOTE_NOT_FINALIZED"


class RestrictedNoteTypeError(BadRequestError):
    """Raised when a restricted note type is asked to do what it never does.

    A restricted note (a psychotherapy note) is written by hand and stands
    on its own: it is never bound to a session and never generated from a
    transcript.
    """

    code = "NOTE_TYPE_RESTRICTED"


def is_restricted_note_type(note_type: str) -> bool:
    """Whether ``note_type`` is one of the built-in author-only types.

    Only built-in definitions can be restricted, so the default registry is
    the whole answer: a practice-defined type is never restricted (and is
    answered without touching the practice's own store), and an unknown key
    is left for the caller's own validation to reject.
    """
    if is_practice_key(note_type):
        return False
    registry = get_default_registry()
    return registry.has(note_type) and registry.get(note_type).restricted


class NoteService:
    """Lifecycle operations for clinical notes."""

    def __init__(self, notes_repo: NotesRepository) -> None:
        self._notes = notes_repo

    # --- Read ---

    def get_note(self, note_id: str, user_id: str) -> Note:
        note = self._notes.get(note_id, user_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found")
        return note

    def get_note_by_session_id(self, session_id: str, user_id: str) -> Note | None:
        return self._notes.get_by_session_id(session_id, user_id)

    def list_notes_for_patient(self, patient_id: str, user_id: str) -> list[Note]:
        return self._notes.list_by_patient(patient_id, user_id)

    # --- Generation pipeline ---

    def create_or_update_for_session(
        self,
        *,
        session_id: str,
        patient_id: str,
        note_type: str,
        content: dict[str, Any] | None,
        user_id: str,
        note_type_version: int | None = None,
        note_inputs: dict[str, str] | None = None,
        psychotherapy_start: dict[str, Any] | None = None,
    ) -> Note:
        """Persist a note tied to a session.

        Insert a new row if none exists for this session; otherwise update
        the existing row's ``content`` and ``note_type`` (and clear
        ``content_edited`` since regenerated content supersedes prior
        in-progress edits). ``content`` may be ``None`` to pre-allocate
        a row for a scheduled session whose note has not been generated
        yet — a placeholder so the requested ``note_type`` survives until
        generation.

        ``note_type_version`` is always written, since it describes the
        content being stored. ``note_inputs`` is written only when given, so
        generating the note keeps the inputs chosen when it was scheduled.

        A restricted type is refused outright: the session's one note is
        the progress note, and a psychotherapy note is never bound to one.

        New content replaces the proposed psychotherapy start with
        ``psychotherapy_start`` and keeps any window the clinician confirmed,
        writing it into the new content (see ``app.notes.visit_times``).
        """
        if is_restricted_note_type(note_type):
            raise RestrictedNoteTypeError(
                f"Note type {note_type!r} cannot be attached to a session",
                {"note_type": note_type, "session_id": session_id},
            )
        existing = self._notes.get_by_session_id(session_id, user_id)
        now = utc_now()
        if existing is not None:
            existing.note_type = note_type
            existing.note_type_version = note_type_version
            if note_inputs is not None:
                existing.note_inputs = note_inputs
            if content is not None:
                window = {**(existing.psychotherapy_window or {}), "proposal": psychotherapy_start}
                existing.psychotherapy_window = window
                existing.content = apply_confirmed_window(content, window)
                existing.content_edited = None
            existing.updated_at = now
            return self._notes.update(existing, user_id)

        note = Note(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            session_id=session_id,
            note_type=note_type,
            note_type_version=note_type_version,
            note_inputs=note_inputs,
            psychotherapy_window={"proposal": psychotherapy_start} if psychotherapy_start else None,
            content=content,
            author_user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        return self._notes.add(note, user_id)

    def create_standalone_note(
        self,
        *,
        patient_id: str,
        note_type: str,
        content: dict[str, Any] | None = None,
        content_edited: dict[str, Any] | None = None,
        status: str = "complete",
        user_id: str,
        note_type_version: int | None = None,
        note_inputs: dict[str, str] | None = None,
    ) -> Note:
        """Persist a patient-owned note that is not bound to a session.

        Used by the standalone-note path (pa-0nx.3): no ``session_id``,
        and the row may be empty until the clinician fills it via PATCH.
        ``status`` is ``'complete'`` unless this is the skeleton for a
        dictation that generates off-request, in which case the caller
        passes ``'processing'`` and a Cloud Tasks worker completes it
        via :meth:`complete_generation` / :meth:`fail_generation`.

        The author is stamped on the row, and ``restricted`` is taken from
        the note type's definition: a restricted note may only ever start
        empty and ``complete`` — a ``processing`` skeleton means a
        dictation is about to be generated into it, which a restricted
        type never allows.
        """
        restricted = is_restricted_note_type(note_type)
        if restricted and (status != "complete" or content is not None):
            raise RestrictedNoteTypeError(
                f"Note type {note_type!r} is written by hand, not generated",
                {"note_type": note_type},
            )
        now = utc_now()
        note = Note(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            session_id=None,
            note_type=note_type,
            note_type_version=note_type_version,
            note_inputs=note_inputs,
            content=content,
            content_edited=content_edited,
            status=status,
            author_user_id=user_id,
            restricted=restricted,
            created_at=now,
            updated_at=now,
        )
        try:
            return self._notes.add(note, user_id)
        except PatientAccessDeniedError as exc:
            raise NoteNotFoundError(
                f"Patient {patient_id} not found",
                {"patient_id": patient_id},
            ) from exc

    def complete_generation(
        self,
        note_id: str,
        content: dict[str, Any],
        user_id: str,
        note_type_version: int | None = None,
    ) -> Note:
        """Write generated content onto a ``processing`` note and mark it complete.

        Called by the standalone-note dictation worker once generation
        succeeds.
        """
        note = self.get_note(note_id, user_id)
        note.content = content
        note.note_type_version = note_type_version
        note.status = "complete"
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    def fail_generation(self, note_id: str, user_id: str) -> Note:
        """Mark a ``processing`` note ``failed`` with no content.

        Called by the standalone-note dictation worker once generation has
        durably failed (deterministic error, or a transient error on the
        queue's final delivery attempt).
        """
        note = self.get_note(note_id, user_id)
        note.status = "failed"
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    # --- Redrafting a session's note (see app.services.note_redraft) ---

    def begin_redraft(self, note: Note, user_id: str) -> Note:
        """Mark ``note`` as being drafted again, with any changes made to it."""
        note.status = "processing"
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    def complete_redraft(
        self,
        note: Note,
        *,
        content: dict[str, Any],
        content_edited: dict[str, Any] | None,
        note_type_version: int | None,
        user_id: str,
        psychotherapy_start: dict[str, Any] | None = None,
    ) -> Note:
        """Write the new draft, with whatever edits the redraft kept.

        As with any new draft, the proposed psychotherapy start is replaced
        and a confirmed window is kept and written back into it.
        """
        window = {**(note.psychotherapy_window or {}), "proposal": psychotherapy_start}
        note.psychotherapy_window = window
        note.content = apply_confirmed_window(content, window)
        note.content_edited = apply_confirmed_window(content_edited, window)
        note.note_type_version = note_type_version
        note.status = "complete"
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    def end_redraft(self, note: Note, user_id: str) -> Note:
        """Close a redraft that writes nothing, leaving the note as it is."""
        note.status = "complete"
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    # --- Edits ---

    def update_note_edits(self, note_id: str, content_edited: dict[str, Any], user_id: str) -> Note:
        """Persist clinician edits to a note's content.

        Refused on a locked (finalized) note: a signed note changes only by
        an addendum, or by unlocking it with a reason.
        """
        note = self.get_note(note_id, user_id)
        if note.finalized_at is not None:
            raise NoteLockedError(
                f"Note {note_id} is signed and locked",
                {"note_id": note_id},
            )
        note.content_edited = content_edited
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    def confirm_psychotherapy_window(
        self,
        note_id: str,
        confirmed: dict[str, Any],
        user_id: str,
        *,
        replace_dictated: bool = False,
    ) -> Note:
        """Record the psychotherapy window the clinician confirmed.

        The window is written into the note's psychotherapy time field unless
        that field holds a time the clinician dictated; ``replace_dictated``
        is the clinician choosing the confirmed window over it. Refused on a
        locked note.
        """
        note = self.get_note(note_id, user_id)
        if note.finalized_at is not None:
            raise NoteLockedError(f"Note {note_id} is signed and locked", {"note_id": note_id})
        window = {**(note.psychotherapy_window or {}), "confirmed": confirmed}
        note.psychotherapy_window = window
        current = note.content_edited or note.content
        if current is not None and replace_dictated and drafted_time(current) is not None:
            current = clear_drafted_time(current)
        filled = apply_confirmed_window(current, window)
        if filled is not current:
            note.content_edited = filled
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    # --- Finalization ---

    def finalize_note(
        self,
        note_id: str,
        *,
        quality_rating: int | None = None,
        quality_rating_reason: str | None = None,
        quality_rating_sections: list[str] | None = None,
        finalized_at: datetime | None = None,
        user_id: str,
    ) -> Note:
        """Finalize a note (record quality rating + finalized_at).

        ``quality_rating`` is optional — manually-authored notes (no AI
        draft) carry no rating; session-derived notes do.
        """
        note = self.get_note(note_id, user_id)
        if note.finalized_at is not None:
            raise NoteAlreadyFinalizedError(
                f"Note {note_id} is already finalized",
                {"note_id": note_id},
            )
        note.quality_rating = quality_rating
        note.quality_rating_reason = quality_rating_reason
        note.quality_rating_sections = quality_rating_sections
        note.finalized_at = finalized_at or utc_now()
        note.updated_at = utc_now()
        return self._notes.update(note, user_id)

    def update_quality_rating(
        self,
        note_id: str,
        *,
        quality_rating: int,
        quality_rating_reason: str | None = None,
        quality_rating_sections: list[str] | None = None,
        user_id: str,
    ) -> tuple[Note, int | None]:
        """Update an already-finalized note's quality rating.

        Returns ``(note, old_rating)``. Old rating is whatever was on the
        note before this call — useful for audit logging.
        """
        note = self.get_note(note_id, user_id)
        if note.finalized_at is None:
            raise NoteNotFinalizedError(
                f"Note {note_id} is not finalized",
                {"note_id": note_id},
            )
        old_rating = note.quality_rating
        note.quality_rating = quality_rating
        note.quality_rating_reason = quality_rating_reason
        note.quality_rating_sections = quality_rating_sections
        note.updated_at = utc_now()
        return self._notes.update(note, user_id), old_rating

    # --- Signing, addenda, unlocking (see app.services.note_signing) ---

    def get_signing_record(
        self, note_id: str, user_id: str
    ) -> tuple[Note, list[NoteSignature], list[NoteAddendum]]:
        """The note with every signed version (oldest first) and its addenda."""
        note = self.get_note(note_id, user_id)
        return (
            note,
            self._notes.list_signatures(note, user_id),
            self._notes.list_addenda(note, user_id),
        )

    def sign_note(
        self,
        note_id: str,
        *,
        signer_name: str,
        signer_credentials: str | None,
        user_id: str,
        quality_rating: int | None = None,
        quality_rating_reason: str | None = None,
        quality_rating_sections: list[str] | None = None,
        now: datetime | None = None,
    ) -> tuple[Note, NoteSignature]:
        """Sign and lock a note, keeping the body as signed as a new version.

        The name and credentials are stored exactly as entered (after
        trimming) — never re-derived from the profile. An unlocked note is
        stamped ``finalized_at`` in the same transaction; a finalized note
        from before signatures existed keeps its ``finalized_at`` and gains
        its first signature. A rating, when given, is recorded as
        :meth:`finalize_note` records one.
        """
        name, credentials = clean_signer(signer_name, signer_credentials)
        note, signatures, _ = self.get_signing_record(note_id, user_id)
        if current_signature(note, signatures) is not None:
            raise NoteAlreadySignedError(
                f"Note {note_id} is already signed",
                {"note_id": note_id},
            )
        signed_at = now or utc_now()
        signature = NoteSignature(
            id=str(uuid.uuid4()),
            note_id=note.id,
            patient_id=note.patient_id,
            version=len(signatures) + 1,
            note_type=note.note_type,
            note_type_version=note.note_type_version,
            content=note.content,
            content_edited=note.content_edited,
            digest="",
            signed_by=user_id,
            signer_name=name,
            signer_credentials=credentials,
            signed_at=signed_at,
        )
        signature.digest = signature_digest(signature)
        if note.finalized_at is None:
            note.finalized_at = signed_at
        if quality_rating is not None:
            note.quality_rating = quality_rating
            note.quality_rating_reason = quality_rating_reason
            note.quality_rating_sections = quality_rating_sections
        note.updated_at = signed_at
        note = self._notes.update(note, user_id)
        return note, self._notes.add_signature(signature, user_id)

    def unlock_note(self, note_id: str, *, reason: str, user_id: str) -> tuple[Note, NoteSignature]:
        """Unlock a signed note to correct an error.

        The reason is required. The signed version is kept as it was, marked
        superseded with when, by whom and why; the note becomes editable and
        has to be signed again.
        """
        cleaned_reason = (reason or "").strip()
        if not cleaned_reason:
            raise UnlockReasonRequiredError(
                "A reason is required to unlock a note", {"note_id": note_id}
            )
        note, signatures, _ = self.get_signing_record(note_id, user_id)
        if note.finalized_at is None:
            raise NoteNotLockedError(f"Note {note_id} is not locked", {"note_id": note_id})
        signature = current_signature(note, signatures)
        if signature is None:
            raise NoteNotSignedError(
                f"Note {note_id} has no signature to supersede", {"note_id": note_id}
            )
        now = utc_now()
        signature.unlocked_at = now
        signature.unlocked_by = user_id
        signature.unlock_reason = cleaned_reason
        signature = self._notes.record_unlock(signature, user_id)
        note.finalized_at = None
        note.updated_at = now
        return self._notes.update(note, user_id), signature

    def add_addendum(
        self,
        note_id: str,
        *,
        text: str,
        signer_name: str,
        signer_credentials: str | None,
        user_id: str,
    ) -> tuple[Note, NoteAddendum]:
        """Append a signed addendum to a locked note's chain; the body is untouched."""
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            raise AddendumTextRequiredError("An addendum needs text", {"note_id": note_id})
        name, credentials = clean_signer(signer_name, signer_credentials)
        note, _, addenda = self.get_signing_record(note_id, user_id)
        if note.finalized_at is None:
            raise NoteNotLockedError(
                f"Note {note_id} is not locked; edit it instead", {"note_id": note_id}
            )
        previous = addenda[-1].digest if addenda else None
        now = utc_now()
        addendum = NoteAddendum(
            id=str(uuid.uuid4()),
            note_id=note.id,
            patient_id=note.patient_id,
            text=cleaned_text,
            signer_name=name,
            signer_credentials=credentials,
            digest=addendum_chain_link(
                note.id,
                previous,
                text=cleaned_text,
                signer_name=name,
                signer_credentials=credentials,
                author=user_id,
                created_at=now,
            ),
            prev_digest=previous,
            created_by=user_id,
            created_at=now,
        )
        return note, self._notes.add_addendum(addendum, user_id)
