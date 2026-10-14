# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A note imported from a document is a reference copy, never a note to sign.

The visit it records was documented, and signed, in the system where it
happened. Signing it here would stamp today's date and this clinician's name
on someone else's record of an earlier visit, so every path that signs, locks,
unlocks or adds to a note refuses one whose session is ``imported``. Its chart
proposals are still decided the ordinary way: accepting one never needed a
signature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api_errors import ConflictError
from ..models.enums import SessionStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..models import Note, TherapySession
    from ..repositories import TherapySessionRepository

IMPORTED_NOTE_REFUSAL = "An imported note can't be signed or changed here."


class ImportedNoteError(ConflictError):
    """The note was imported; it is kept as it was documented."""

    code = "NOTE_IMPORTED"


def refuse_imported(session: TherapySession | None) -> None:
    """Raise :class:`ImportedNoteError` when ``session`` holds an imported note."""
    if session is not None and session.status == SessionStatus.IMPORTED:
        raise ImportedNoteError(IMPORTED_NOTE_REFUSAL, {"session_id": session.id})


def refuse_imported_note(note: Note, sessions: TherapySessionRepository, user_id: str) -> None:
    """Raise :class:`ImportedNoteError` when ``note`` is an imported session's note."""
    if note.session_id is not None:
        refuse_imported(sessions.get(note.session_id, user_id))


def imported_note_ids(
    notes: Iterable[Note], sessions: TherapySessionRepository, user_id: str
) -> set[str]:
    """The ids of the notes among ``notes`` that are imported sessions' notes."""
    by_session = {n.session_id: n.id for n in notes if n.session_id is not None}
    found = sessions.get_multiple(list(by_session), user_id) if by_session else {}
    return {by_session[sid] for sid, s in found.items() if s.status == SessionStatus.IMPORTED}
