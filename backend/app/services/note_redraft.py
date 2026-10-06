# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Redraft a session's note after it was first drafted.

One path for every reason a drafted note is drafted again: the clinician
supplied or changed the note type's inputs, or added to what the session
said. Starting a redraft (:meth:`NoteRedraftService.start`) is cheap and runs
on the request; the model call runs off it (:meth:`NoteRedraftService.run`),
on the same queue and in the same way as the first draft.

A redraft never touches a signed note: a signed note changes only by an
addendum. Clinician edits survive by default — :func:`merge_kept_edits` keeps
every field the clinician changed and fills the rest from the new draft —
and are discarded only when the clinician asks for that.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..api_errors import BadRequestError, ConflictError
from ..db import release_db_connection
from ..models import SessionStatus, SOAPNote, Transcript
from ..models.enums import SessionSource
from ..models.notes import RedraftEdits
from ..notes import NoteTypeDefinition, get_default_registry
from ..notes.practice_types import validate_note_inputs
from .note_generation_service import SOAP_KEY, TransientNoteGenerationError
from .note_service import NoteNotFoundError
from .note_signing import NoteLockedError
from .session_service import (
    PatientNotFoundError,
    SessionNotFoundError,
    SOAPGenerationFailedError,
    TransientSOAPGenerationError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..models import Patient, TherapySession
    from ..models.note import Note
    from ..repositories import PatientRepository, TherapySessionRepository
    from ..repositories.session_dictation import SessionDictationRepository
    from .note_generation_service import NoteGenerationService
    from .note_service import NoteService

logger = logging.getLogger(__name__)

#: Heads what the clinician dictated after the session, below its transcript,
#: so the draft treats it as the clinician's own addendum rather than as
#: something said with the client in the room.
DICTATED_HEADING = "Dictated by the clinician after the session (the client was not present):"

# A session whose note was drafted from what it recorded: under review, or
# finalized with its note unlocked again to correct an error.
_REDRAFTABLE_STATUSES = frozenset({SessionStatus.PENDING_REVIEW, SessionStatus.FINALIZED})


class NoteHasEditsError(ConflictError):
    """The note carries clinician edits and the caller didn't say what to do with them."""

    code = "NOTE_HAS_EDITS"


class NoteRedraftInProgressError(ConflictError):
    """A redraft of this note is already running."""

    code = "NOTE_REDRAFT_IN_PROGRESS"


class NoteNotRedraftableError(BadRequestError):
    """The session has nothing a note could be drafted again from."""

    code = "NOTE_NOT_REDRAFTABLE"


class RedraftNotPendingError(Exception):
    """A redraft job found no redraft waiting for it (a duplicate delivery)."""


def has_edits(note: Note) -> bool:
    return bool(note.content_edited)


def merge_kept_edits(
    note_type: str,
    previous: dict[str, Any] | None,
    edited: dict[str, Any] | None,
    redrafted: dict[str, Any],
) -> dict[str, Any] | None:
    """The edits that survive a redraft, or ``None`` when none do.

    A field keeps the clinician's text when they changed it from the draft
    they were shown and left something there; every other field takes the
    new draft. ``None`` means the new draft is the note as it stands, so the
    draft's own structure (and a SOAP draft's source links) is what shows.

    Edits are stored in the shape the clinician edits in, which for SOAP is
    one narrative block per section, so SOAP drafts are compared in that
    shape too.
    """
    if not edited:
        return None
    before = _editable_view(note_type, previous)
    after = _editable_view(note_type, redrafted)
    merged = _merge(before, edited, after)
    return None if merged == after else merged


def _editable_view(note_type: str, content: dict[str, Any] | None) -> dict[str, Any]:
    if not content:
        return {}
    if note_type == SOAP_KEY:
        return SOAPNote.from_dict(content).to_narrative()
    return content


def _merge(before: Any, edited: Any, after: Any) -> Any:
    if isinstance(edited, dict):
        before_map = before if isinstance(before, dict) else {}
        after_map = after if isinstance(after, dict) else {}
        keys = [*after_map, *(k for k in edited if k not in after_map)]
        return {k: _merge(before_map.get(k), edited.get(k), after_map.get(k)) for k in keys}
    if _filled(edited) and edited != before:
        return edited
    return after


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


class NoteRedraftService:
    """Start a session note's redraft, and run it off the request."""

    def __init__(
        self,
        session_repo: TherapySessionRepository,
        patient_repo: PatientRepository,
        note_service: NoteService,
        note_generation_service: NoteGenerationService,
        dictation_repo: SessionDictationRepository | None = None,
    ) -> None:
        self.session_repo = session_repo
        self.patient_repo = patient_repo
        self.note_service = note_service
        self.note_generation_service = note_generation_service
        self.dictation_repo = dictation_repo

    def _redraftable(self, session_id: str, user_id: str) -> tuple[TherapySession, Note]:
        session = self.session_repo.get(session_id, user_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None or note.content is None:
            raise NoteNotFoundError(f"Session {session_id} has no drafted note")
        if note.finalized_at is not None:
            raise NoteLockedError(f"Note {note.id} is signed and locked", {"note_id": note.id})
        if (
            session.status not in _REDRAFTABLE_STATUSES
            or session.source == SessionSource.IMPORTED
            or not session.transcript.content.strip()
        ):
            raise NoteNotRedraftableError(
                "This session's note can't be drafted again", {"session_id": session_id}
            )
        if note.status == "processing":
            raise NoteRedraftInProgressError(
                f"Note {note.id} is already being redrafted", {"note_id": note.id}
            )
        return session, note

    def check_redraftable(self, session_id: str, user_id: str) -> Note:
        """The session's note, if it can be drafted again now; raises otherwise."""
        return self._redraftable(session_id, user_id)[1]

    def start(
        self,
        session_id: str,
        user_id: str,
        *,
        note_inputs: Mapping[str, str] | None = None,
        edits: RedraftEdits | None = None,
    ) -> tuple[Note, bool]:
        """Mark the session's note as being redrafted; the caller enqueues :meth:`run`.

        ``note_inputs``, when given, replace the note's inputs after the same
        validation the appointment applies. Returns the note and whether the
        redraft keeps the clinician's edits.

        Raises:
            NoteHasEditsError: the note has edits and ``edits`` is ``None``.
        """
        _session, note = self._redraftable(session_id, user_id)
        if has_edits(note) and edits is None:
            raise NoteHasEditsError(
                "The note has edits; say whether to keep them", {"note_id": note.id}
            )
        if note_inputs is not None:
            definition = get_default_registry().get(note.note_type)
            try:
                note.note_inputs = validate_note_inputs(definition, note_inputs) or None
            except ValueError as exc:
                raise BadRequestError(
                    str(exc).strip("'\""),
                    {"note_type": note.note_type},
                    code="INVALID_NOTE_INPUTS",
                ) from exc
        note = self.note_service.begin_redraft(note, user_id)
        # Committed before the job is enqueued, so the job always finds it.
        release_db_connection()
        return note, edits == RedraftEdits.KEEP

    def _source_transcript(self, session: TherapySession) -> Transcript:
        """The session's transcript, then everything dictated for its note.

        Only dictations that went into a redraft count; one that became an
        addendum to a signed note is already in the record as that addendum.
        """
        dictations = self.dictation_repo.list_for_session(session.id) if self.dictation_repo else []
        dictated = [d.transcript for d in dictations if d.used_as == "redraft" and d.transcript]
        if not dictated:
            return session.transcript
        content = "\n\n".join([session.transcript.content, DICTATED_HEADING, *dictated])
        return Transcript(format=session.transcript.format, content=content)

    def run(
        self,
        session_id: str,
        user_id: str,
        *,
        keep_edits: bool,
        transient_is_terminal: bool = False,
    ) -> tuple[TherapySession, Patient, Note]:
        """Draft the note again and write it, keeping edits when asked.

        On a deterministic failure the note keeps what it had and is marked
        ``failed`` so the page can say the redraft didn't finish. A note
        signed while the redraft ran keeps its signed body; the redraft is
        dropped.

        Raises:
            RedraftNotPendingError: nothing is waiting to be redrafted.
            TransientSOAPGenerationError: retry the job.
            SOAPGenerationFailedError: the redraft failed for good.
        """
        session = self.session_repo.get(session_id, user_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        patient = self.patient_repo.get(session.patient_id, user_id)
        if patient is None:
            raise PatientNotFoundError(f"Patient {session.patient_id} not found")
        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None or note.status != "processing":
            raise RedraftNotPendingError(session_id)
        try:
            definition: NoteTypeDefinition | None = get_default_registry().get(note.note_type)
        except KeyError:
            definition = None
        transcript = self._source_transcript(session)
        previous = note.content
        # Nothing is held open across the model call (see generate_session_note).
        release_db_connection()

        try:
            generated = self.note_generation_service.generate_note(
                note.note_type,
                transcript,
                patient,
                session.session_date,
                inputs=note.note_inputs,
                definition=definition,
            )
        except TransientNoteGenerationError as exc:
            if not transient_is_terminal:
                logger.warning("Redraft transiently failed for session %s; retrying", session_id)
                raise TransientSOAPGenerationError from exc
            self.note_service.fail_generation(note.id, user_id)
            release_db_connection()
            raise SOAPGenerationFailedError from exc
        except Exception as exc:
            logger.exception("Redraft failed for session %s", session_id)
            self.note_service.fail_generation(note.id, user_id)
            release_db_connection()
            raise SOAPGenerationFailedError from exc

        # Read again: the clinician may have saved edits, or signed, meanwhile.
        current = self.note_service.get_note(note.id, user_id)
        if current.finalized_at is not None:
            logger.info("Note for session %s was signed during its redraft; dropped", session_id)
            return session, patient, self.note_service.end_redraft(current, user_id)
        kept = (
            merge_kept_edits(
                generated.note_type, previous, current.content_edited, generated.content
            )
            if keep_edits
            else None
        )
        note = self.note_service.complete_redraft(
            current,
            content=generated.content,
            content_edited=kept,
            note_type_version=generated.note_type_version,
            user_id=user_id,
        )
        return session, patient, note
