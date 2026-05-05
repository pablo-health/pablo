# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Soft-delete cascade + atomicity (THERAPY-nyb).

End-to-end tests against a real PostgreSQL backend (provided by the
testcontainers harness in ``tests_integration/conftest.py``):

  * patient delete soft-deletes the parent and cascades to therapy
    sessions and notes (transcripts ride along on therapy_sessions.JSONB)
  * session-level and note-level ``delete()`` set ``deleted_at`` and
    leave the row on disk for the day-30 purge cron (THERAPY-cgy)
  * read paths (``get`` / ``list_by_*``) hide soft-deleted rows
  * the audit row and the soft-delete share one transaction — if audit
    raises, the soft-delete rolls back and ``deleted_at`` stays NULL

The fixture materializes tables via ORM ``create_all``, mirroring the
pattern in ``test_audit_writes.py``. Migration correctness lives in
``test_alembic_idempotency.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.models import Base, NoteRow, PatientRow, TherapySessionRow
from app.db.platform_models import PlatformBase
from app.models import Patient, User
from app.models.audit import AuditAction
from app.models.note import Note
from app.models.session import TherapySession, Transcript
from app.repositories.postgres.audit import PostgresAuditRepository
from app.repositories.postgres.note import PostgresNotesRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.session import PostgresTherapySessionRepository
from app.services.audit_service import AuditService
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        conn.execute(
            text(f"SET search_path = {DEFAULT_PRACTICE_SCHEMA}, {PLATFORM_SCHEMA}, public")
        )
        PlatformBase.metadata.create_all(conn)
        Base.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("SET search_path = practice, platform, public"))
    # Wipe everything we touch so row counts are deterministic.
    for table in (
        "practice.audit_logs",
        "practice.notes",
        "practice.therapy_sessions",
        "practice.patients",
    ):
        session.execute(text(f"TRUNCATE TABLE {table}"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ─── Builders ────────────────────────────────────────────────────────────


_USER_ID = "test-user-1"
_NOW = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)


def _build_user() -> User:
    return User(
        id=_USER_ID,
        email="soft-delete@example.com",
        name="Soft Delete Test",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2026-05-05",
    )


def _build_request() -> MagicMock:
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "198.51.100.7"
    request.headers = {"User-Agent": "pytest-soft-delete/1.0"}
    return request


def _seed_patient(pg: Session, patient_id: str = "patient-1") -> Patient:
    patient = Patient(
        id=patient_id,
        user_id=_USER_ID,
        first_name="Jane",
        last_name="Doe",
        created_at=_NOW,
        updated_at=_NOW,
    )
    PostgresPatientRepository(pg).create(patient)
    return patient


def _seed_session(
    pg: Session,
    session_id: str = "session-1",
    patient_id: str = "patient-1",
    *,
    transcript_text: str = "session transcript content",
) -> TherapySession:
    session_obj = TherapySession(
        id=session_id,
        user_id=_USER_ID,
        patient_id=patient_id,
        session_date=_NOW,
        session_number=1,
        status="pending_review",
        transcript=Transcript(format="text", content=transcript_text),
        created_at=_NOW,
    )
    PostgresTherapySessionRepository(pg).create(session_obj)
    return session_obj


def _seed_note(
    pg: Session,
    note_id: str = "note-1",
    patient_id: str = "patient-1",
    session_id: str | None = "session-1",
) -> Note:
    note = Note(
        id=note_id,
        patient_id=patient_id,
        session_id=session_id,
        note_type="soap",
        content={"subjective": "stub"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    PostgresNotesRepository(pg).add(note)
    return note


# ─── 1. Patient delete soft-deletes parent + cascades ─────────────────────


class TestPatientSoftDeleteCascade:
    def test_soft_delete_sets_deleted_at_on_patient_session_and_note(
        self, pg_session: Session
    ) -> None:
        patient = _seed_patient(pg_session)
        _seed_session(pg_session)
        _seed_note(pg_session)
        pg_session.commit()

        repo = PostgresPatientRepository(pg_session)
        deleted = repo.delete(patient.id, _USER_ID)
        assert deleted is True
        pg_session.commit()

        # All three rows still on disk (not physically deleted).
        assert pg_session.get(PatientRow, "patient-1") is not None
        assert pg_session.get(TherapySessionRow, "session-1") is not None
        assert pg_session.get(NoteRow, "note-1") is not None

        # And all three carry ``deleted_at``.
        for row in (
            pg_session.get(PatientRow, "patient-1"),
            pg_session.get(TherapySessionRow, "session-1"),
            pg_session.get(NoteRow, "note-1"),
        ):
            assert row is not None
            assert row.deleted_at is not None

    def test_repeat_delete_is_a_noop(self, pg_session: Session) -> None:
        """Calling delete twice doesn't update the existing ``deleted_at`` stamp."""
        patient = _seed_patient(pg_session)
        pg_session.commit()
        repo = PostgresPatientRepository(pg_session)

        delete_result = repo.delete(patient.id, _USER_ID)
        assert delete_result is True
        pg_session.commit()
        first_stamp = pg_session.get(PatientRow, "patient-1").deleted_at

        # Second call returns False and does not bump the stamp.
        deleted_again = repo.delete(patient.id, _USER_ID)
        assert deleted_again is False
        pg_session.commit()
        second_stamp = pg_session.get(PatientRow, "patient-1").deleted_at
        assert first_stamp == second_stamp


# ─── 2. Per-resource soft-delete (session, note) ─────────────────────────


class TestTherapySessionSoftDelete:
    def test_session_delete_sets_deleted_at(self, pg_session: Session) -> None:
        _seed_patient(pg_session)
        session_obj = _seed_session(pg_session)
        pg_session.commit()

        repo = PostgresTherapySessionRepository(pg_session)
        deleted = repo.delete(session_obj.id, _USER_ID)
        assert deleted is True
        pg_session.commit()

        row = pg_session.get(TherapySessionRow, "session-1")
        assert row is not None
        assert row.deleted_at is not None


class TestNoteSoftDelete:
    def test_note_delete_sets_deleted_at(self, pg_session: Session) -> None:
        _seed_patient(pg_session)
        _seed_session(pg_session)
        _seed_note(pg_session)
        pg_session.commit()

        repo = PostgresNotesRepository(pg_session)
        repo.delete("note-1")
        pg_session.commit()

        row = pg_session.get(NoteRow, "note-1")
        assert row is not None
        assert row.deleted_at is not None


# ─── 3. Read paths hide soft-deleted rows (covers transcript JSONB) ───────


class TestReadPathsFilterSoftDeleted:
    def test_patient_get_and_list_hide_soft_deleted(self, pg_session: Session) -> None:
        patient = _seed_patient(pg_session)
        pg_session.commit()
        repo = PostgresPatientRepository(pg_session)
        assert repo.get(patient.id, _USER_ID) is not None

        repo.delete(patient.id, _USER_ID)
        pg_session.commit()

        assert repo.get(patient.id, _USER_ID) is None
        listed, total = repo.list_by_user(_USER_ID)
        assert listed == []
        assert total == 0
        assert repo.get_multiple([patient.id], _USER_ID) == {}

    def test_session_get_and_lists_hide_soft_deleted(self, pg_session: Session) -> None:
        """list_by_patient covers transcript visibility — transcripts live
        on therapy_sessions.transcript JSONB, so a session being hidden
        hides its transcript by construction."""
        _seed_patient(pg_session)
        session_obj = _seed_session(pg_session, transcript_text="visible-only-while-live")
        pg_session.commit()

        s_repo = PostgresTherapySessionRepository(pg_session)
        before = s_repo.list_by_patient("patient-1", _USER_ID)
        assert len(before) == 1
        assert before[0].transcript.content == "visible-only-while-live"

        s_repo.delete(session_obj.id, _USER_ID)
        pg_session.commit()

        assert s_repo.get(session_obj.id, _USER_ID) is None
        assert s_repo.list_by_patient("patient-1", _USER_ID) == []
        listed_user, total_user = s_repo.list_by_user(_USER_ID)
        assert listed_user == []
        assert total_user == 0

    def test_note_get_and_list_hide_soft_deleted(self, pg_session: Session) -> None:
        _seed_patient(pg_session)
        _seed_session(pg_session)
        _seed_note(pg_session)
        pg_session.commit()

        n_repo = PostgresNotesRepository(pg_session)
        assert n_repo.get("note-1") is not None
        assert n_repo.get_by_session_id("session-1") is not None
        assert len(n_repo.list_by_patient("patient-1")) == 1

        n_repo.delete("note-1")
        pg_session.commit()

        assert n_repo.get("note-1") is None
        assert n_repo.get_by_session_id("session-1") is None
        assert n_repo.list_by_patient("patient-1") == []


# ─── 4. Atomicity: audit + soft-delete share the same transaction ────────


class TestAtomicity:
    def test_audit_and_soft_delete_commit_together(self, pg_session: Session) -> None:
        """Happy path: both the soft-delete and the audit row land in one txn."""
        patient = _seed_patient(pg_session)
        pg_session.commit()

        p_repo = PostgresPatientRepository(pg_session)
        audit = AuditService(PostgresAuditRepository(pg_session))

        # Mirror the route's order: soft-delete first, then audit.
        delete_ok = p_repo.delete(patient.id, _USER_ID)
        assert delete_ok is True
        audit.log_patient_action(
            AuditAction.PATIENT_DELETED, _build_user(), _build_request(), patient
        )
        pg_session.commit()

        # Patient row is soft-deleted, audit row is present, both visible after commit.
        row = pg_session.get(PatientRow, patient.id)
        assert row is not None
        assert row.deleted_at is not None
        audit_count = pg_session.execute(
            text("SELECT COUNT(*) FROM practice.audit_logs WHERE action='patient_deleted'")
        ).scalar()
        assert audit_count == 1

    def test_audit_failure_rolls_back_soft_delete(self, pg_session: Session) -> None:
        """If the audit insert raises mid-flow, the soft-delete UPDATE
        must roll back and ``deleted_at`` must remain NULL — the split
        state we explicitly reject."""
        patient = _seed_patient(pg_session)
        pg_session.commit()

        p_repo = PostgresPatientRepository(pg_session)

        # Build an audit service whose append() blows up — same shape as
        # an unexpected DB error from inside the audit path.
        broken_repo = MagicMock()
        broken_repo.append.side_effect = RuntimeError("audit DB is down")
        audit = AuditService(broken_repo)

        # Match the route's order: soft-delete first, then audit. The
        # audit raise propagates to the middleware, which rolls back.
        delete_ok = p_repo.delete(patient.id, _USER_ID)
        assert delete_ok is True
        with pytest.raises(RuntimeError, match="audit DB is down"):
            audit.log_patient_action(
                AuditAction.PATIENT_DELETED, _build_user(), _build_request(), patient
            )
        pg_session.rollback()  # what the middleware does on exception

        # Re-query after rollback. deleted_at must be NULL.
        row = pg_session.get(PatientRow, patient.id)
        assert row is not None
        assert row.deleted_at is None
        # And the audit row never landed.
        audit_count = pg_session.execute(
            text("SELECT COUNT(*) FROM practice.audit_logs WHERE action='patient_deleted'")
        ).scalar()
        assert audit_count == 0


# ─── 5. Recently-deleted listing + restore (THERAPY-yg2) ─────────────────


class TestRecentlyDeletedListing:
    def test_lists_only_in_window_soft_deleted(self, pg_session: Session) -> None:
        """``list_recently_deleted`` returns soft-deletes inside the 30-day
        window and excludes both live patients and past-window tombstones."""
        live = _seed_patient(pg_session, "patient-live")
        recent = _seed_patient(pg_session, "patient-recent")
        old = _seed_patient(pg_session, "patient-old")
        pg_session.commit()

        repo = PostgresPatientRepository(pg_session)
        # Live: untouched. Recent: soft-deleted now. Old: stamp backdated.
        repo.delete(recent.id, _USER_ID)
        repo.delete(old.id, _USER_ID)
        pg_session.commit()

        old_row = pg_session.get(PatientRow, old.id)
        assert old_row is not None
        old_row.deleted_at = datetime.now(UTC) - timedelta(days=31)
        pg_session.commit()

        results = repo.list_recently_deleted(_USER_ID, window_days=30)
        ids = [p.id for p, _stamp in results]
        assert ids == [recent.id]
        # And the stamp is propagated for the UI countdown.
        assert results[0][1] is not None
        # Sanity: live patients are not affected by the recently-deleted slice.
        live_listed, _ = repo.list_by_user(_USER_ID)
        assert [p.id for p in live_listed] == [live.id]


class TestPatientRestore:
    def test_restore_clears_deleted_at_and_cascades(self, pg_session: Session) -> None:
        """Restore reverses ``delete``: patient + cascaded sessions/notes
        all return to ``deleted_at IS NULL``."""
        patient = _seed_patient(pg_session)
        _seed_session(pg_session)
        _seed_note(pg_session)
        pg_session.commit()

        repo = PostgresPatientRepository(pg_session)
        assert repo.delete(patient.id, _USER_ID) is True
        pg_session.commit()

        restored = repo.restore(patient.id, _USER_ID, window_days=30)
        assert restored is not None
        assert restored.id == patient.id
        pg_session.commit()

        for row in (
            pg_session.get(PatientRow, "patient-1"),
            pg_session.get(TherapySessionRow, "session-1"),
            pg_session.get(NoteRow, "note-1"),
        ):
            assert row is not None
            assert row.deleted_at is None

        # Live read paths surface the patient again.
        assert repo.get(patient.id, _USER_ID) is not None
        listed, total = repo.list_by_user(_USER_ID)
        assert total == 1
        assert [p.id for p in listed] == [patient.id]

    def test_restore_preserves_session_number(self, pg_session: Session) -> None:
        """``session_number`` is monotonic across the soft-delete cycle —
        ``get_session_number_for_patient`` ignores ``deleted_at`` so a
        restored session keeps the number it had before deletion
        (THERAPY-nyb invariant guarded for THERAPY-yg2)."""
        _seed_patient(pg_session)
        session_obj = _seed_session(pg_session)
        pg_session.commit()
        original_number = session_obj.session_number

        repo = PostgresPatientRepository(pg_session)
        repo.delete("patient-1", _USER_ID)
        pg_session.commit()
        repo.restore("patient-1", _USER_ID, window_days=30)
        pg_session.commit()

        row = pg_session.get(TherapySessionRow, "session-1")
        assert row is not None
        assert row.session_number == original_number

    def test_restore_past_window_returns_none(self, pg_session: Session) -> None:
        """Past-window soft-deletes cannot be restored (awaiting hard-purge)."""
        patient = _seed_patient(pg_session)
        pg_session.commit()
        repo = PostgresPatientRepository(pg_session)
        repo.delete(patient.id, _USER_ID)
        pg_session.commit()

        # Backdate the stamp past the window.
        row = pg_session.get(PatientRow, patient.id)
        assert row is not None
        row.deleted_at = datetime.now(UTC) - timedelta(days=31)
        pg_session.commit()

        assert repo.restore(patient.id, _USER_ID, window_days=30) is None
        # Stamp is unchanged — restore did not touch the row.
        row = pg_session.get(PatientRow, patient.id)
        assert row is not None
        assert row.deleted_at is not None

    def test_restore_not_soft_deleted_returns_none(self, pg_session: Session) -> None:
        """Restoring a live (never-deleted) patient is a no-op None."""
        patient = _seed_patient(pg_session)
        pg_session.commit()
        repo = PostgresPatientRepository(pg_session)
        assert repo.restore(patient.id, _USER_ID, window_days=30) is None

    def test_restore_only_undoes_matching_cascade_stamp(self, pg_session: Session) -> None:
        """Independently soft-deleted sessions stay tombstoned after restore.

        Models the case where a single session was deleted earlier (per-row
        soft-delete), then later the whole patient was deleted. The patient
        delete cascades to the still-live sessions/notes, stamping them with
        the patient's own ``deleted_at``. ``restore`` must clear only the
        rows whose stamp matches that cascade — the older standalone
        soft-deletes keep their original stamps and stay hidden.
        """
        _seed_patient(pg_session)
        # Two sessions: one we'll delete on its own first.
        old = _seed_session(pg_session, session_id="session-1")
        _seed_session(pg_session, session_id="session-2")
        pg_session.commit()

        s_repo = PostgresTherapySessionRepository(pg_session)
        s_repo.delete(old.id, _USER_ID)
        pg_session.commit()
        old_stamp = pg_session.get(TherapySessionRow, "session-1").deleted_at
        assert old_stamp is not None

        p_repo = PostgresPatientRepository(pg_session)
        p_repo.delete("patient-1", _USER_ID)
        pg_session.commit()

        p_repo.restore("patient-1", _USER_ID, window_days=30)
        pg_session.commit()

        # session-2 was cascaded with the patient's stamp — now restored.
        s2 = pg_session.get(TherapySessionRow, "session-2")
        assert s2 is not None
        assert s2.deleted_at is None
        # session-1 had its own earlier stamp — stays soft-deleted.
        s1 = pg_session.get(TherapySessionRow, "session-1")
        assert s1 is not None
        assert s1.deleted_at == old_stamp
