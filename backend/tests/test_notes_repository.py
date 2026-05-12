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
from app.repositories.note import PatientAccessDeniedError
from app.repositories.postgres.note import PostgresNotesRepository

# Hard-coded throughout — every test in this module passes user_id explicitly,
# which is the contract we want to enforce post-patient_clinicians.
_USER = "test-clinician"


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
        repo = InMemoryNotesRepository()
        # Tests in this class don't exercise access control — the
        # patient_clinicians-aware tests live in TestInMemoryAccessControl
        # below. Grant universal access so the legacy behavior tests
        # exercise the read/write/sort/delete logic in isolation.
        repo.grant_all_access()
        return repo

    def test_add_and_get(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note, _USER)
        fetched = repo.get(note.id, _USER)
        assert fetched is not None
        assert fetched.id == note.id
        assert fetched.patient_id == "patient-1"

    def test_get_missing_returns_none(self, repo: InMemoryNotesRepository) -> None:
        assert repo.get("does-not-exist", _USER) is None

    def test_get_by_session_id(self, repo: InMemoryNotesRepository) -> None:
        repo.add(_make_note(session_id="session-A"), _USER)
        repo.add(_make_note(session_id="session-B"), _USER)
        repo.add(_make_note(session_id=None), _USER)

        found = repo.get_by_session_id("session-B", _USER)
        assert found is not None
        assert found.session_id == "session-B"
        assert repo.get_by_session_id("missing", _USER) is None

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
        repo.add(older, _USER)
        repo.add(newer, _USER)
        repo.add(other_patient, _USER)

        results = repo.list_by_patient("patient-1", _USER)
        assert [n.id for n in results] == [newer.id, older.id]

    def test_update_replaces_row(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note, _USER)
        note.quality_rating = 5
        repo.update(note, _USER)
        fetched = repo.get(note.id, _USER)
        assert fetched is not None
        assert fetched.quality_rating == 5

    def test_delete(self, repo: InMemoryNotesRepository) -> None:
        note = _make_note()
        repo.add(note, _USER)
        repo.delete(note.id, _USER)
        assert repo.get(note.id, _USER) is None
        # Idempotent — deleting again is a no-op.
        repo.delete(note.id, _USER)


class TestInMemoryAccessControl:
    """The in-memory repo must enforce the same access contract as
    PostgresNotesRepository — otherwise IDOR regressions could pass
    every test and only fail in production.
    """

    def test_get_denied_returns_none(self) -> None:
        repo = InMemoryNotesRepository()
        note = _make_note(patient_id="pt-A")
        repo.grant_access("pt-A", "alice")
        repo.add(note, "alice")

        # Bob has no grant for patient A.
        assert repo.get(note.id, "bob") is None

    def test_update_denied_raises(self) -> None:
        repo = InMemoryNotesRepository()
        note = _make_note(patient_id="pt-A")
        repo.grant_access("pt-A", "alice")
        repo.add(note, "alice")

        note.quality_rating = 5
        with pytest.raises(PatientAccessDeniedError):
            repo.update(note, "bob")

    def test_list_by_patient_denied_returns_empty(self) -> None:
        repo = InMemoryNotesRepository()
        repo.grant_access("pt-A", "alice")
        repo.add(_make_note(patient_id="pt-A"), "alice")
        repo.add(_make_note(patient_id="pt-A"), "alice")

        assert repo.list_by_patient("pt-A", "bob") == []
        assert len(repo.list_by_patient("pt-A", "alice")) == 2

    def test_delete_denied_is_silent_noop(self) -> None:
        repo = InMemoryNotesRepository()
        note = _make_note(patient_id="pt-A")
        repo.grant_access("pt-A", "alice")
        repo.add(note, "alice")

        # Bob's delete is a no-op — matches read-side "indistinguishable
        # from absent" so existence isn't leaked.
        repo.delete(note.id, "bob")
        assert repo.get(note.id, "alice") is not None


class TestPostgresNotesRepositoryMapping:
    """Verify the Postgres repo correctly maps Note <-> NoteRow.

    Uses a MagicMock SQLAlchemy session so we don't require a live DB —
    these tests cover the row/dataclass conversion, which is where
    backfill-data-shape bugs typically hide. The access-function check
    (``has_patient_access``) is stubbed to return True so writes proceed
    into the mapping path that's actually under test; access-denial
    behavior is covered by integration tests against a live DB.
    """

    @staticmethod
    def _session_with_access_granted() -> MagicMock:
        session = MagicMock()
        # Mock the has_patient_access call: session.execute(...).scalar() -> True
        session.execute.return_value.scalar.return_value = True
        return session

    def test_add_assigns_all_fields_to_row(self) -> None:
        session = self._session_with_access_granted()
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
        repo.add(note, _USER)

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
        session = self._session_with_access_granted()
        # The query path returns None when no row matches the join.
        q = session.query.return_value.join.return_value.filter.return_value
        q.one_or_none.return_value = None
        repo = PostgresNotesRepository(session)
        assert repo.get("missing", _USER) is None

    def test_update_upserts_when_row_missing(self) -> None:
        session = self._session_with_access_granted()
        session.get.return_value = None
        repo = PostgresNotesRepository(session)
        note = _make_note(note_id="note-2")
        repo.update(note, _USER)

        # When the row doesn't exist yet, update() creates a new one and adds it.
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert isinstance(added, NoteRow)
        assert added.id == "note-2"

    def test_delete_noop_when_missing(self) -> None:
        session = self._session_with_access_granted()
        session.get.return_value = None
        repo = PostgresNotesRepository(session)
        repo.delete("missing", _USER)
        session.delete.assert_not_called()
