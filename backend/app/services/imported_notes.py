# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A note imported from another system is a reference copy, never a note to sign.

The visit it records was documented, and signed, in the system where it
happened. Signing it here would stamp today's date and this clinician's name
on someone else's record of an earlier visit, so every path that signs, locks,
unlocks or adds to a note refuses one that was imported. Its chart proposals
are still decided the ordinary way: accepting one never needed a signature.

Two kinds of note are imported. A note read from a single document hangs off
a session in the ``imported`` status. A note an archive import landed without
a visit (a chart note, an administrative note, a treatment plan, a
psychotherapy note) has no session; it carries where it came from in its
content (:mod:`app.models.note_source`), and is imported on that alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api_errors import ConflictError
from ..models.enums import SessionStatus
from ..models.note_source import note_source

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
    """Raise :class:`ImportedNoteError` when ``note`` was imported, with or without a session."""
    if note_source(note) is not None:
        raise ImportedNoteError(IMPORTED_NOTE_REFUSAL, {"note_id": note.id})
    if note.session_id is not None:
        refuse_imported(sessions.get(note.session_id, user_id))


def imported_note_ids(
    notes: Iterable[Note], sessions: TherapySessionRepository, user_id: str
) -> set[str]:
    """The ids of the notes among ``notes`` that were imported.

    A note carrying its provenance is imported whatever its session says; the
    rest are imported when their session is.
    """
    listed = list(notes)
    sourced = {n.id for n in listed if note_source(n) is not None}
    by_session = {n.session_id: n.id for n in listed if n.session_id is not None}
    found = sessions.get_multiple(list(by_session), user_id) if by_session else {}
    return sourced | {
        by_session[sid] for sid, s in found.items() if s.status == SessionStatus.IMPORTED
    }
