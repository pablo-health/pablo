# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How long a practice keeps session audio, and deleting it once a note is signed.

``platform.practices.audio_retention_days`` is 0..2555. One or more is a
number of days after the session. 0 means the audio is kept only until the
session's note is signed: redrafting a note and dictating more both need the
recording, so it stays while the note is open and goes once the clinician signs.

Deleting on signing happens in the request that signs, through
:class:`AudioOnSigning`. The signature is committed first, so a failure here
can never cost the clinician the signature, and the audio is never deleted for
a note that did not end up signed. What goes: the session's recording (both
channels), any staged speech-only copies beside it, and every clip dictated
about the session that has finished transcribing. A clip still transcribing is
left for the next signing, the addendum it becomes.

No PHI is logged: a session id and counts only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db import get_db_session
from ..models import AuditAction

if TYPE_CHECKING:
    from fastapi import Request

    from ..models import Note, User
    from ..repositories import TherapySessionRepository
    from ..repositories.session_dictation import SessionDictationRepository
    from .audit_service import AuditService
    from .file_storage import FileStorageProvider

logger = logging.getLogger(__name__)

#: ``audio_retention_days`` value meaning "delete when the note is signed".
DELETE_ON_SIGNING = 0


def deletes_on_signing(retention_days: int) -> bool:
    """Whether a practice keeps session audio only until the note is signed."""
    return retention_days == DELETE_ON_SIGNING


def retention_phrase(retention_days: int) -> str:
    """When session audio is deleted, as a consent document finishes the sentence.

    "Session audio is deleted {phrase}." reads true for every setting.
    """
    if deletes_on_signing(retention_days):
        return "once your note is signed"
    unit = "day" if retention_days == 1 else "days"
    return f"{retention_days} {unit} after your session"


def _audio_objects(audio_gcs_path: str | None) -> list[str]:
    """The object names a session's ``audio_gcs_path`` holds, one per channel."""
    if not audio_gcs_path:
        return []
    return [part.strip() for part in audio_gcs_path.split(",") if part.strip()]


@dataclass
class AudioOnSigning:
    """Deletes a signed note's session audio for a practice set to delete on signing.

    ``retention_days`` is the practice's setting, ``None`` when the caller has
    no practice (nothing is deleted then).
    """

    retention_days: int | None
    session_repo: TherapySessionRepository
    dictation_repo: SessionDictationRepository
    storage: FileStorageProvider
    bucket: str

    def after_signing(self, note: Note, user: User, request: Request, audit: AuditService) -> int:
        """Delete the audio behind *note* if it is signed and the practice says so.

        Returns how many stored objects were deleted. Commits the request's
        transaction before deleting anything, so the signature stands first.
        """
        if (
            self.retention_days is None
            or not deletes_on_signing(self.retention_days)
            or note.session_id is None
            or note.finalized_at is None
        ):
            return 0
        session = self.session_repo.get(note.session_id, user.id)
        if session is None:
            return 0
        get_db_session().commit()

        recording = _audio_objects(session.audio_gcs_path)
        clips = [
            d.audio_path
            for d in self.dictation_repo.list_for_session(session.id)
            if d.status != "transcribing"
        ]
        try:
            deleted = self._delete(recording + clips)
        except Exception:
            # The note is signed and stays signed. The recording keeps its
            # path, so the next signing of this note tries again.
            logger.exception("audio_on_signing_failed session=%s", session.id)
            return 0

        if session.audio_gcs_path:
            session.audio_gcs_path = None
            self.session_repo.update(session)
        if deleted:
            audit.log_session_action(
                AuditAction.AUDIO_PURGED,
                user,
                request,
                session,
                changes={"source": "deleted_on_signing", "objects": deleted},
            )
        return deleted

    def _delete(self, object_names: list[str]) -> int:
        """Delete each stored object that is still there, and its speech-only copies."""
        deleted = 0
        for name in object_names:
            present = self.storage.list_names(bucket=self.bucket, prefix=name)
            for sibling in present:
                if sibling.startswith(f"{name}.speech."):
                    self.storage.delete(bucket=self.bucket, object_name=sibling)
            if name in present:
                self.storage.delete(bucket=self.bucket, object_name=name)
                deleted += 1
        return deleted


__all__ = [
    "DELETE_ON_SIGNING",
    "AudioOnSigning",
    "deletes_on_signing",
    "retention_phrase",
]
