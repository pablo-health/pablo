# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note repository — patient-access-scoped reads & writes.

Every method takes a ``user_id`` representing the clinician making the
request. Reads return ``None`` (or an empty list) when ``user_id`` has
no grant in ``patient_clinicians`` for the relevant patient; writes
raise :class:`PatientAccessDeniedError`. The Postgres implementation
delegates the check to the ``has_patient_access`` SQL function so
application-layer and database-layer (RLS) authorization stay in
lockstep. See ``alembic/versions/777b846ab944_*.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Note


class PatientAccessDeniedError(Exception):
    """Raised when a write touches a patient the user has no grant for.

    Reads return ``None`` instead of raising, matching the prevailing
    repository convention and avoiding existence-oracle leaks; writes
    raise because a 404 on PUT/PATCH/DELETE would mask a real bug.
    """

    def __init__(self, patient_id: str, user_id: str) -> None:
        super().__init__(
            f"user {user_id!r} has no access grant for patient {patient_id!r}"
        )
        self.patient_id = patient_id
        self.user_id = user_id


class NotesRepository(ABC):
    """Abstract base class for note data access.

    All methods are scoped by ``user_id``. The contract is:

    * Reads return ``None`` / empty when access is denied — same shape
      as "row doesn't exist" so callers don't accidentally distinguish
      "wasn't there" from "you can't see it" and leak an existence
      oracle.
    * Writes raise :class:`PatientAccessDeniedError` because silently
      no-op'ing a write would mask broken code.
    """

    @abstractmethod
    def get(self, note_id: str, user_id: str) -> Note | None:
        """Get a note by ID, or ``None`` if absent or inaccessible."""

    @abstractmethod
    def get_by_session_id(self, session_id: str, user_id: str) -> Note | None:
        """Get the note for a recording session, or ``None`` if absent or inaccessible.

        At most one note exists per session (enforced by the partial
        unique index on ``notes.session_id``).
        """

    @abstractmethod
    def list_by_patient(
        self, patient_id: str, user_id: str, *, limit: int | None = None
    ) -> list[Note]:
        """List notes for a patient, newest first.

        Returns ``[]`` when the user has no access grant — matches the
        empty-result shape for "patient has no notes" so callers can't
        distinguish.

        ``limit`` caps the result to the most-recent N notes (by
        ``finalized_at`` then ``created_at``). The chat context bundler
        passes a cap so the hot path doesn't load an entire chart's note
        history on every turn; ``None`` (the default) preserves the
        unbounded behavior for callers that genuinely need all notes.
        """

    @abstractmethod
    def add(self, note: Note, user_id: str) -> Note:
        """Insert a new note row. Raises ``PatientAccessDeniedError`` if blocked."""

    @abstractmethod
    def update(self, note: Note, user_id: str) -> Note:
        """Update an existing note row (upsert if missing)."""

    @abstractmethod
    def delete(self, note_id: str, user_id: str) -> None:
        """Soft-delete a note. No-op if it doesn't exist or is inaccessible."""


_TEST_DEFAULT_USER = "__inmemory_test_default__"


class InMemoryNotesRepository(NotesRepository):
    """In-memory NotesRepository for unit tests.

    Maintains a ``(patient_id, user_id)`` access set populated via
    :meth:`grant_access`. Tests that don't care about access control
    can call :meth:`grant_all_access` (the shared ``mock_notes_repo``
    fixture in ``conftest.py`` does this so legacy tests work
    unchanged). Tests that *do* exercise access control should
    instantiate ``InMemoryNotesRepository()`` directly and call
    :meth:`grant_access` for specific ``(patient_id, user_id)`` pairs;
    see ``test_routes_notes.py::TestIDOR``.

    The ``user_id`` parameter defaults to a sentinel on every method
    purely as a test ergonomic — production code paths thread
    ``user_id`` explicitly. The :class:`PostgresNotesRepository`
    intentionally does *not* default, so prod can't accidentally drop
    the argument.
    """

    def __init__(self) -> None:
        self._notes: dict[str, Note] = {}
        self._access: set[tuple[str, str]] = set()  # (patient_id, user_id)
        self._allow_all = False

    # --- test setup helpers ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        """Record that ``user_id`` may read/write ``patient_id``'s notes."""
        self._access.add((patient_id, user_id))

    def grant_all_access(self) -> None:
        """Open the gate — use for legacy tests that pre-date the access model."""
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

    # --- read methods ---

    def get(self, note_id: str, user_id: str = _TEST_DEFAULT_USER) -> Note | None:
        note = self._notes.get(note_id)
        if note is None:
            return None
        if not self._can_access(note.patient_id, user_id):
            return None
        return note

    def get_by_session_id(
        self, session_id: str, user_id: str = _TEST_DEFAULT_USER
    ) -> Note | None:
        for note in self._notes.values():
            if note.session_id == session_id:
                if not self._can_access(note.patient_id, user_id):
                    return None
                return note
        return None

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str = _TEST_DEFAULT_USER,
        *,
        limit: int | None = None,
    ) -> list[Note]:
        if not self._can_access(patient_id, user_id):
            return []
        notes = [n for n in self._notes.values() if n.patient_id == patient_id]
        notes.sort(
            key=lambda n: (n.finalized_at or n.created_at),
            reverse=True,
        )
        if limit is not None:
            notes = notes[:limit]
        return notes

    # --- write methods ---

    def add(self, note: Note, user_id: str = _TEST_DEFAULT_USER) -> Note:
        if not self._can_access(note.patient_id, user_id):
            raise PatientAccessDeniedError(note.patient_id, user_id)
        self._notes[note.id] = note
        return note

    def update(self, note: Note, user_id: str = _TEST_DEFAULT_USER) -> Note:
        if not self._can_access(note.patient_id, user_id):
            raise PatientAccessDeniedError(note.patient_id, user_id)
        self._notes[note.id] = note
        return note

    def delete(self, note_id: str, user_id: str = _TEST_DEFAULT_USER) -> None:
        existing = self._notes.get(note_id)
        if existing is None:
            return
        if not self._can_access(existing.patient_id, user_id):
            return
        self._notes.pop(note_id, None)
