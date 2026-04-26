# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for NotesRepository (InMemory + Postgres mapping)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from app.db.models import NoteRow
from app.models import Note
from app.repositories import InMemoryNotesRepository
from app.repositories.postgres.note import PostgresNotesRepository


def _make_note(
    *,
    note_id: str | None = None,
    patient_id: str = "patient-1",
    session_id: str | None = None,
    finalized_at: datetime | None = None,
    created_at: datetime | None = None,
) -> Note:
    now = created_at or datetime.now(UTC)
    return Note(
        id=note_id or str(uuid.uuid4()),
        patient_id=patient_id,
        session_id=session_id,
        note_type="soap",
        content={"subjective": "S", "objective": "O", "assessment": "A", "plan": "P"},
        finalized_at=finalized_at,
        created_at=now,
        updated_at=now,
    )


class TestInMemoryNotesRepository:
    @pytest.fixture
    def repo(self) -> InMemoryNotesRepository:
        return InMemoryNotesRepository()

    def test_add_and_get(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note)
        fetched = repo.get(note.id)
        assert fetched is not None
        assert fetched.id == note.id
        assert fetched.patient_id == "patient-1"

    def test_get_missing_returns_none(self, repo: InMemoryNotesRepository) -> None:
        assert repo.get("does-not-exist") is None

    def test_get_by_session_id(self, repo: InMemoryNotesRepository) -> None:
        repo.add(_make_note(session_id="session-A"))
        repo.add(_make_note(session_id="session-B"))
        repo.add(_make_note(session_id=None))

        found = repo.get_by_session_id("session-B")
        assert found is not None
        assert found.session_id == "session-B"
        assert repo.get_by_session_id("missing") is None

    def test_list_by_patient_sorted_newest_first(
        self, repo: InMemoryNotesRepository
    ) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        older = _make_note(
            patient_id="patient-1", finalized_at=base, created_at=base
        )
        newer = _make_note(
            patient_id="patient-1",
            finalized_at=base + timedelta(days=2),
            created_at=base + timedelta(days=2),
        )
        other_patient = _make_note(patient_id="patient-2")
        repo.add(older)
        repo.add(newer)
        repo.add(other_patient)

        results = repo.list_by_patient("patient-1")
        assert [n.id for n in results] == [newer.id, older.id]

    def test_update_replaces_row(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note)
        note.quality_rating = 5
        repo.update(note)
        fetched = repo.get(note.id)
        assert fetched is not None
        assert fetched.quality_rating == 5

    def test_delete(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note)
        repo.delete(note.id)
        assert repo.get(note.id) is None
        # Idempotent — deleting again is a no-op.
        repo.delete(note.id)


class TestPostgresNotesRepositoryMapping:
    """Verify the Postgres repo correctly maps Note <-> NoteRow.

    Uses a MagicMock SQLAlchemy session so we don't require a live DB —
    these tests cover the row/dataclass conversion, which is where
    backfill-data-shape bugs typically hide.
    """

    def test_add_assigns_all_fields_to_row(self) -> None:
        session = MagicMock()
        repo = PostgresNotesRepository(session)
        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        note = Note(
            id="note-1",
            patient_id="pt-1",
            session_id="sess-1",
            note_type="soap",
            content={"s": "x"},
            content_edited={"s": "y"},
            finalized_at=now,
            quality_rating=4,
            quality_rating_reason="solid",
            quality_rating_sections=["plan"],
            export_status="queued",
            export_queued_at=now,
            redacted_content={"s": "<REDACTED>"},
            naturalized_content={"s": "Jane"},
            redacted_export_payload={"payload": "ok"},
            created_at=now,
            updated_at=now,
        )
        repo.add(note)

        added = session.add.call_args.args[0]
        assert isinstance(added, NoteRow)
        assert added.id == "note-1"
        assert added.patient_id == "pt-1"
        assert added.session_id == "sess-1"
        assert added.note_type == "soap"
        assert added.content == {"s": "x"}
        assert added.content_edited == {"s": "y"}
        assert added.finalized_at == now
        assert added.quality_rating == 4
        assert added.quality_rating_reason == "solid"
        assert added.quality_rating_sections == ["plan"]
        assert added.export_status == "queued"
        assert added.export_queued_at == now
        assert added.redacted_content == {"s": "<REDACTED>"}
        assert added.naturalized_content == {"s": "Jane"}
        assert added.redacted_export_payload == {"payload": "ok"}
        assert added.created_at == now
        assert added.updated_at == now
        session.flush.assert_called_once()

    def test_get_returns_none_for_missing_row(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        repo = PostgresNotesRepository(session)
        assert repo.get("missing") is None

    def test_update_upserts_when_row_missing(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        repo = PostgresNotesRepository(session)
        note = _make_note(note_id="note-2")
        repo.update(note)

        # When the row doesn't exist yet, update() creates a new one and adds it.
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, NoteRow)
        assert added.id == "note-2"

    def test_delete_noop_when_missing(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        repo = PostgresNotesRepository(session)
        repo.delete("missing")
        session.delete.assert_not_called()
