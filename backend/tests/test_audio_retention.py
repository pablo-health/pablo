# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deleting a session's audio when its note is signed.

A practice whose audio retention is 0 keeps the recording only until the note
is signed: the audio of an unsigned note is never touched, the signature is
committed before anything is deleted, and a storage failure never costs the
clinician the signature. Any other setting leaves signing alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.models.audit import AuditAction
from app.routes.notes import get_audio_on_signing
from app.services.audio_retention import (
    DELETE_ON_SIGNING,
    AudioOnSigning,
    deletes_on_signing,
    retention_phrase,
)
from app.services.file_storage import LocalFileStorage

from .test_note_redraft import USER
from .test_session_dictations import WEBM, World, registry  # noqa: F401 — autouse fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from app.models import User
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from app.repositories.session_dictation import InMemorySessionDictationRepository
    from fastapi.testclient import TestClient

THERAPIST = "audio/2026-10-06/session/therapist.webm"
CLIENT = "audio/2026-10-06/session/client.webm"
SPEECH = f"{CLIENT}.speech.flac"


class Recorded(World):
    """A session with a two-channel recording, a staged speech copy, and a note."""

    def __init__(self, tmp_path: Path, *repos: Any, retention_days: int | None) -> None:
        super().__init__(tmp_path, *repos)
        self.bucket = str(tmp_path)
        self.storage = LocalFileStorage()
        for name in (THERAPIST, CLIENT, SPEECH):
            self.storage.upload_bytes(
                bucket=self.bucket, object_name=name, data=WEBM, content_type="audio/webm"
            )
        self.session.audio_gcs_path = f"{THERAPIST},{CLIENT}"
        self.sessions.update(self.session)
        self.purge = AudioOnSigning(
            retention_days=retention_days,
            session_repo=self.sessions,
            dictation_repo=self.dictations,
            storage=self.storage,
            bucket=self.bucket,
        )

    def stored(self) -> list[str]:
        return self.storage.list_names(bucket=self.bucket, prefix="")

    def recording_path(self) -> str | None:
        found = self.sessions.get(self.session.id, USER)
        assert found is not None
        return found.audio_gcs_path


@pytest.fixture
def db() -> Iterator[MagicMock]:
    """The request's database session; ``commit`` is what makes a signature stand."""
    session = MagicMock()
    with patch("app.services.audio_retention.get_db_session", return_value=session):
        yield session


def _world(
    tmp_path: Path,
    sessions: InMemoryTherapySessionRepository,
    patients: InMemoryPatientRepository,
    notes: InMemoryNotesRepository,
    dictations: InMemorySessionDictationRepository,
    retention_days: int | None,
) -> Recorded:
    return Recorded(tmp_path, sessions, patients, notes, dictations, retention_days=retention_days)


@pytest.fixture
def on_signing(
    tmp_path: Path,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_dictation_repo: InMemorySessionDictationRepository,
) -> Recorded:
    return _world(
        tmp_path,
        mock_session_repo,
        mock_repo,
        mock_notes_repo,
        mock_dictation_repo,
        DELETE_ON_SIGNING,
    )


@pytest.fixture
def kept_a_year(
    tmp_path: Path,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_dictation_repo: InMemorySessionDictationRepository,
) -> Recorded:
    return _world(tmp_path, mock_session_repo, mock_repo, mock_notes_repo, mock_dictation_repo, 365)


def _audit() -> MagicMock:
    return MagicMock()


class TestTheSetting:
    def test_only_zero_deletes_on_signing(self) -> None:
        assert deletes_on_signing(0)
        assert not deletes_on_signing(1)
        assert not deletes_on_signing(365)

    @pytest.mark.parametrize(
        ("days", "phrase"),
        [
            (0, "once your note is signed"),
            (1, "1 day after your session"),
            (2, "2 days after your session"),
            (2555, "2555 days after your session"),
        ],
    )
    def test_the_phrase_a_consent_document_shows(self, days: int, phrase: str) -> None:
        assert retention_phrase(days) == phrase


class TestDeletingOnSigning:
    def test_a_signed_notes_recording_and_clips_are_deleted(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        dictation_id, keep = on_signing.dictate()
        on_signing.service.run(dictation_id, USER, keep_edits=keep)
        on_signing.sign()
        audit = _audit()

        deleted = on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), audit)

        assert deleted == 3  # both channels and the dictated clip
        assert on_signing.stored() == []  # the staged speech copy went with its channel
        assert on_signing.recording_path() is None
        db.commit.assert_called_once()
        audit.log_session_action.assert_called_once()
        assert audit.log_session_action.call_args.args[0] == AuditAction.AUDIO_PURGED

    def test_an_unsigned_notes_audio_is_never_deleted(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        audit = _audit()

        deleted = on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), audit)

        assert deleted == 0
        assert on_signing.stored() == sorted([CLIENT, SPEECH, THERAPIST])
        assert on_signing.recording_path() == f"{THERAPIST},{CLIENT}"
        db.commit.assert_not_called()
        audit.log_session_action.assert_not_called()

    def test_a_practice_that_keeps_audio_for_days_is_untouched_by_signing(
        self, kept_a_year: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        kept_a_year.sign()

        deleted = kept_a_year.purge.after_signing(
            kept_a_year.note(), mock_user, MagicMock(), _audit()
        )

        assert deleted == 0
        assert len(kept_a_year.stored()) == 3
        db.commit.assert_not_called()

    def test_with_no_practice_nothing_is_deleted(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        on_signing.purge.retention_days = None
        on_signing.sign()

        assert (
            on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), _audit()) == 0
        )
        assert len(on_signing.stored()) == 3

    def test_the_signature_is_committed_before_anything_is_deleted(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        committed_first: list[bool] = []
        real_delete = on_signing.storage.delete

        def delete(*, bucket: str, object_name: str) -> None:
            committed_first.append(db.commit.called)
            real_delete(bucket=bucket, object_name=object_name)

        on_signing.storage.delete = delete  # type: ignore[method-assign]  # spy on one method
        on_signing.sign()

        on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), _audit())

        assert committed_first
        assert all(committed_first)

    def test_a_clip_still_transcribing_is_left_for_the_addendum(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        on_signing.sign()
        dictation_id, _ = on_signing.dictate()
        clip = on_signing.dictations.get(dictation_id)
        assert clip is not None
        assert clip.status == "transcribing"

        on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), _audit())

        assert on_signing.stored() == [clip.audio_path]

    def test_a_storage_failure_keeps_the_path_for_the_next_signing(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        def fail(*, bucket: str, object_name: str) -> None:
            raise RuntimeError("storage unavailable")

        on_signing.storage.delete = fail  # type: ignore[method-assign]  # one failing method
        on_signing.sign()
        audit = _audit()

        deleted = on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), audit)

        assert deleted == 0
        assert on_signing.recording_path() == f"{THERAPIST},{CLIENT}"
        db.commit.assert_called_once()  # the signature already stands
        audit.log_session_action.assert_not_called()

    def test_signing_again_finds_nothing_left(
        self, on_signing: Recorded, mock_user: User, db: MagicMock
    ) -> None:
        on_signing.sign()
        on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), _audit())
        audit = _audit()

        assert on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), audit) == 0
        audit.log_session_action.assert_not_called()


class TestTheSignRoute:
    """``POST /api/notes/{id}/sign`` deletes the audio once the note is signed."""

    @pytest.fixture
    def signing_client(self, client: TestClient, on_signing: Recorded) -> Iterator[TestClient]:
        app.dependency_overrides[get_audio_on_signing] = lambda: on_signing.purge
        yield client
        app.dependency_overrides.pop(get_audio_on_signing, None)

    def test_signing_deletes_the_recording(
        self,
        signing_client: TestClient,
        on_signing: Recorded,
        db: MagicMock,
    ) -> None:
        response = signing_client.post(
            f"/api/notes/{on_signing.note_id}/sign", json={"signer_name": "Sam Ortiz"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["finalized_at"] is not None
        assert on_signing.stored() == []
        assert on_signing.recording_path() is None

    def test_a_refused_signature_deletes_nothing(
        self, signing_client: TestClient, on_signing: Recorded, db: MagicMock
    ) -> None:
        response = signing_client.post(
            f"/api/notes/{on_signing.note_id}/sign", json={"signer_name": "  "}
        )

        assert response.status_code == 400
        assert len(on_signing.stored()) == 3
        db.commit.assert_not_called()


def test_the_session_date_is_not_what_decides_on_signing(
    on_signing: Recorded, mock_user: User, db: MagicMock
) -> None:
    """A recording from years ago stays while its note is open."""
    on_signing.session.session_date = datetime(2019, 1, 1, tzinfo=UTC)
    on_signing.sessions.update(on_signing.session)

    assert on_signing.purge.after_signing(on_signing.note(), mock_user, MagicMock(), _audit()) == 0
    assert len(on_signing.stored()) == 3
