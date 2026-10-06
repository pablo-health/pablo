# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Dictate more about a session after its recording has stopped.

The clip is stored where session audio is stored and transcribed off the
request. What happens next depends on the note:

* **Unsigned** — the note is redrafted from the session's transcript plus
  everything dictated for it, through the one redraft path
  (:class:`app.services.note_redraft.NoteRedraftService`). The clinician's
  edits are kept unless they asked to redraft everything.
* **Signed** — the signed body is never touched. The dictation, lightly
  cleaned (:func:`clean_dictation`), becomes a draft addendum the clinician
  reviews, edits and signs through the addendum flow.

Dictation is documentation time. It is stored on its own row with its own
duration, never added to the session's transcript or its timing.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Literal

from ..api_errors import APIError, BadRequestError, NotFoundError
from ..db import release_db_connection
from ..models.notes import RedraftEdits
from ..models.session_dictation import SessionDictation
from ..utcnow import utc_now
from .dictation_transcription import (
    DictationTranscriptionError,
    TransientDictationTranscriptionError,
)
from .note_service import NoteNotFoundError
from .session_service import (
    SessionNotFoundError,
    SOAPGenerationFailedError,
    TransientSOAPGenerationError,
)

if TYPE_CHECKING:
    from ..models import TherapySession
    from ..models.note import Note
    from ..repositories import TherapySessionRepository
    from ..repositories.session_dictation import SessionDictationRepository
    from .dictation_transcription import DictationTranscriber
    from .file_storage import FileStorageProvider
    from .note_redraft import NoteRedraftService
    from .note_service import NoteService

logger = logging.getLogger(__name__)

#: Longest clip the recorder offers; the server allows a little over for
#: container overhead and a recorder that stops a beat late.
MAX_DICTATION_SECONDS = 600
MAX_DICTATION_BYTES = 50 * 1024 * 1024


class DictationUnavailableError(APIError):
    """This deployment has nothing to transcribe a dictation with."""

    status_code = 501
    code = "DICTATION_UNAVAILABLE"
    default_message = "Dictation isn't available on this server."


class DictationNotFoundError(NotFoundError):
    """No such dictation, or the caller can't see it."""


class DictationNotPendingError(Exception):
    """A dictation job found nothing left to do (a duplicate delivery)."""


class EmptyDictationError(BadRequestError):
    code = "DICTATION_EMPTY"


_FILLER = re.compile(r"\b(?:um+|uh+|erm+|er|hmm+|mm+)\b[,.]?\s*", re.IGNORECASE)


def clean_dictation(text: str) -> str:
    """The dictation as a draft addendum: filler and stray spacing removed.

    Deliberately mechanical — it drops sounds that aren't words and tidies
    the spacing and the first and last character, and nothing else, so the
    draft can never say something the clinician didn't.
    """
    cleaned = " ".join(_FILLER.sub("", text).split())
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned[-1] in ".!?" else f"{cleaned}."


def draft_addendum(dictation: SessionDictation) -> str | None:
    """The draft addendum a dictation offers, while it waits to be signed."""
    if dictation.used_as != "addendum" or dictation.addendum_id or not dictation.transcript:
        return None
    return clean_dictation(dictation.transcript)


class SessionDictationService:
    """Take a dictated clip, then transcribe it and apply it off the request."""

    def __init__(
        self,
        *,
        session_repo: TherapySessionRepository,
        note_service: NoteService,
        dictation_repo: SessionDictationRepository,
        redraft_service: NoteRedraftService,
        storage: FileStorageProvider,
        bucket: str,
        transcriber: DictationTranscriber | None,
    ) -> None:
        self.session_repo = session_repo
        self.note_service = note_service
        self.dictation_repo = dictation_repo
        self.redraft_service = redraft_service
        self.storage = storage
        self.bucket = bucket
        self.transcriber = transcriber

    def list_for_session(
        self, session_id: str, user_id: str
    ) -> tuple[TherapySession, list[SessionDictation]]:
        session = self.session_repo.get(session_id, user_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        return session, self.dictation_repo.list_for_session(session_id)

    def start(
        self,
        session_id: str,
        user_id: str,
        *,
        audio: bytes,
        content_type: str,
        duration_seconds: int | None,
        edits: RedraftEdits | None,
    ) -> tuple[SessionDictation, Note, bool]:
        """Store the clip and record it; the caller enqueues :meth:`run`.

        An unsigned note is marked as being redrafted straight away, so the
        page shows progress and nothing edits it meanwhile. Returns the
        dictation, the note, and whether the redraft keeps the clinician's
        edits (by default it does).
        """
        if self.transcriber is None:
            raise DictationUnavailableError
        if not audio:
            raise EmptyDictationError("The dictation has no audio")
        session = self.session_repo.get(session_id, user_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        note = self.note_service.get_note_by_session_id(session_id, user_id)
        if note is None or note.content is None:
            raise NoteNotFoundError(f"Session {session_id} has no drafted note")
        signed = note.finalized_at is not None
        if not signed:
            self.redraft_service.check_redraftable(session_id, user_id)

        dictation_id = str(uuid.uuid4())
        object_name = f"dictations/{session_id}/{dictation_id}"
        self.storage.upload_bytes(
            bucket=self.bucket, object_name=object_name, data=audio, content_type=content_type
        )
        dictation = self.dictation_repo.add(
            SessionDictation(
                id=dictation_id,
                session_id=session_id,
                note_id=note.id,
                patient_id=note.patient_id,
                author_user_id=user_id,
                audio_path=object_name,
                content_type=content_type,
                status="transcribing",
                created_at=utc_now(),
                duration_seconds=duration_seconds,
            )
        )
        if not signed:
            note = self.note_service.begin_redraft(note, user_id)
        # Committed before the job is enqueued, so the job always finds it.
        release_db_connection()
        return dictation, note, edits != RedraftEdits.REPLACE

    def run(
        self,
        dictation_id: str,
        user_id: str,
        *,
        keep_edits: bool,
        transient_is_terminal: bool = False,
    ) -> tuple[SessionDictation, Note | None]:
        """Transcribe the clip, then redraft the note or offer a draft addendum.

        Safe to deliver twice: a transcribed dictation is not transcribed
        again, and a redraft that already landed is not run again. Returns
        the dictation and, when the note was redrafted, the note.

        Raises:
            DictationNotPendingError: nothing left to do.
            TransientSOAPGenerationError: retry the job.
            SOAPGenerationFailedError: transcription or the redraft failed for good.
        """
        dictation = self.dictation_repo.get(dictation_id)
        if dictation is None:
            raise DictationNotPendingError(dictation_id)
        if dictation.status == "transcribing":
            dictation = self._transcribe(dictation, user_id, transient_is_terminal)
        if dictation.status != "transcribed" or dictation.used_as != "redraft":
            raise DictationNotPendingError(dictation_id)
        note = self.note_service.get_note(dictation.note_id, user_id)
        if note.status != "processing":
            raise DictationNotPendingError(dictation_id)
        _, _, note = self.redraft_service.run(
            dictation.session_id,
            user_id,
            keep_edits=keep_edits,
            transient_is_terminal=transient_is_terminal,
        )
        return dictation, note

    def _transcribe(
        self, dictation: SessionDictation, user_id: str, transient_is_terminal: bool
    ) -> SessionDictation:
        if self.transcriber is None:
            raise DictationUnavailableError
        audio = self.storage.download_bytes(bucket=self.bucket, object_name=dictation.audio_path)
        # Nothing is held open while the provider listens.
        release_db_connection()
        try:
            text = self.transcriber.transcribe(audio, dictation.content_type)
        except TransientDictationTranscriptionError as exc:
            if not transient_is_terminal:
                logger.warning("Dictation %s: transcription will be retried", dictation.id)
                raise TransientSOAPGenerationError from exc
            self._fail(dictation, user_id)
            raise SOAPGenerationFailedError from exc
        except DictationTranscriptionError as exc:
            logger.warning("Dictation %s could not be transcribed", dictation.id)
            self._fail(dictation, user_id)
            raise SOAPGenerationFailedError from exc
        if not text:
            self._fail(dictation, user_id)
            raise SOAPGenerationFailedError

        note = self.note_service.get_note(dictation.note_id, user_id)
        used_as: Literal["redraft", "addendum"] = "addendum" if note.finalized_at else "redraft"
        dictation = self.dictation_repo.record_transcript(
            dictation.id,
            status="transcribed",
            transcript=text,
            used_as=used_as,
            transcribed_at=utc_now(),
        )
        if used_as == "addendum" and note.status == "processing":
            # Signed while the clip was transcribing: the redraft it started
            # has nothing to write.
            self.note_service.end_redraft(note, user_id)
        release_db_connection()
        return dictation

    def _fail(self, dictation: SessionDictation, user_id: str) -> None:
        self.dictation_repo.record_transcript(
            dictation.id, status="failed", transcript=None, used_as=None, transcribed_at=utc_now()
        )
        note = self.note_service.get_note(dictation.note_id, user_id)
        if note.status == "processing":
            self.note_service.end_redraft(note, user_id)
        release_db_connection()


def awaiting_addendum(
    dictation_repo: SessionDictationRepository, dictation_id: str, note_id: str
) -> SessionDictation:
    """The dictation whose draft addendum is being signed onto ``note_id``.

    Raises:
        DictationNotFoundError: no such dictation offers this note an addendum.
    """
    dictation = dictation_repo.get(dictation_id)
    if dictation is None or dictation.note_id != note_id or draft_addendum(dictation) is None:
        raise DictationNotFoundError(f"Dictation {dictation_id} not found")
    return dictation
