# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note repository implementations."""

from abc import ABC, abstractmethod

from ..models import Note


class NotesRepository(ABC):
    """Abstract base class for note data access."""

    @abstractmethod
    def get(self, note_id: str) -> Note | None:
        """Get a note by ID."""

    @abstractmethod
    def get_by_session_id(self, session_id: str) -> Note | None:
        """Get the note for a recording session, if one exists.

        At most one note exists per session (enforced by the partial unique
        index on ``notes.session_id``).
        """

    @abstractmethod
    def list_by_patient(self, patient_id: str) -> list[Note]:
        """List all notes for a patient, newest first."""

    @abstractmethod
    def add(self, note: Note) -> Note:
        """Insert a new note row."""

    @abstractmethod
    def update(self, note: Note) -> Note:
        """Update an existing note row (upsert if missing)."""

    @abstractmethod
    def delete(self, note_id: str) -> None:
        """Delete a note by ID. No-op if it doesn't exist."""


class InMemoryNotesRepository(NotesRepository):
    """In-memory NotesRepository for unit tests."""

    def __init__(self) -> None:
        self._notes: dict[str, Note] = {}

    def get(self, note_id: str) -> Note | None:
        return self._notes.get(note_id)

    def get_by_session_id(self, session_id: str) -> Note | None:
        for note in self._notes.values():
            if note.session_id == session_id:
                return note
        return None

    def list_by_patient(self, patient_id: str) -> list[Note]:
        notes = [n for n in self._notes.values() if n.patient_id == patient_id]
        notes.sort(
            key=lambda n: (n.finalized_at or n.created_at),
            reverse=True,
        )
        return notes

    def add(self, note: Note) -> Note:
        self._notes[note.id] = note
        return note

    def update(self, note: Note) -> Note:
        self._notes[note.id] = note
        return note

    def delete(self, note_id: str) -> None:
        self._notes.pop(note_id, None)
