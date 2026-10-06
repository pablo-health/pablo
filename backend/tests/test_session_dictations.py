# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Dictating more about a session after its recording stopped.

An unsigned note is redrafted with the dictation as the clinician's own
addendum, through the one redraft path, with the clinician's edits kept by
default. A signed note is never touched: the dictation becomes a draft
addendum the clinician signs. Dictations are their own append-only records,
audited, and never part of the session's transcript or timing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.main import app
from app.models import Patient, SessionStatus, TherapySession, Transcript
from app.models.notes import RedraftEdits
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.repositories.session_dictation import InMemorySessionDictationRepository
from app.routes import notes as notes_routes
from app.routes import session_dictations as dictation_routes
from app.routes.notes import get_note_generation_service
from app.services import note_redraft
from app.services.dictation_transcription import (
    AssemblyAiDictationTranscriber,
    DictationTranscriptionError,
    HttpDictationTranscriber,
    TransientDictationTranscriptionError,
    get_dictation_transcriber,
)
from app.services.file_storage import LocalFileStorage
from app.services.note_generation_service import GeneratedNote, NoteGenerationService
from app.services.note_redraft import DICTATED_HEADING, NoteRedraftService
from app.services.note_service import NoteService
from app.services.session_dictation_service import (
    DictationNotPendingError,
    DictationUnavailableError,
    SessionDictationService,
    clean_dictation,
    draft_addendum,
)
from app.services.session_service import SOAPGenerationFailedError, TransientSOAPGenerationError
from app.settings import Settings
from pydantic import SecretStr, ValidationError

from .test_note_redraft import USER, VISIT, _visit

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from app.notes.chart_context import ChartContext
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from fastapi.testclient import TestClient

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
SAID = "Um, next session: two weeks from today"


class Transcriber:
    def __init__(self, text: str = SAID) -> None:
        self.text = text
        self.error: Exception | None = None
        self.heard: list[tuple[bytes, str]] = []

    def transcribe(self, audio: bytes, content_type: str) -> str:
        self.heard.append((audio, content_type))
        if self.error is not None:
            raise self.error
        return self.text


class Generator(NoteGenerationService):
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.next_content: dict[str, Any] = _visit("Redrafted.", "Redrafted plan.")

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
        inputs: Mapping[str, str] | None = None,
        definition: NoteTypeDefinition | None = None,
        client_present_end_seconds: float | None = None,
        chart: ChartContext | None = None,
        current_note: Mapping[str, Any] | None = None,
    ) -> GeneratedNote:
        self.transcripts.append(transcript.content)
        return GeneratedNote(note_type=note_type, content=self.next_content)


@pytest.fixture(autouse=True)
def registry(monkeypatch: pytest.MonkeyPatch) -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.register(VISIT)
    monkeypatch.setattr(note_redraft, "get_default_registry", lambda: registry)
    return registry


class World:
    """One clinician's session with a drafted note, and the dictation service."""

    def __init__(
        self,
        tmp_path: Path,
        sessions: InMemoryTherapySessionRepository,
        patients: InMemoryPatientRepository,
        notes: InMemoryNotesRepository,
        dictations: InMemorySessionDictationRepository,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.sessions = sessions
        self.notes = notes
        self.dictations = dictations
        self.generator = Generator()
        self.transcriber = transcriber if transcriber is not None else Transcriber()
        now = datetime.now(UTC)
        patient = patients.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name="Test",
                last_name="Client",
                created_at=now,
                updated_at=now,
            ),
            USER,
        )
        self.session = sessions.create(
            TherapySession(
                id=str(uuid.uuid4()),
                user_id=USER,
                patient_id=patient.id,
                session_date=now,
                session_number=1,
                status=SessionStatus.PENDING_REVIEW,
                transcript=Transcript(format="txt", content="[00:01] Therapist: Hello"),
                created_at=now,
                started_at=now - timedelta(minutes=50),
                ended_at=now,
                duration_minutes=50,
            )
        )
        self.note_service = NoteService(notes)
        self.note_id = self.note_service.create_or_update_for_session(
            session_id=self.session.id,
            patient_id=patient.id,
            note_type=VISIT.key,
            content=_visit("First draft.", "First plan."),
            user_id=USER,
            note_inputs={"visit_code": "99213"},
        ).id
        self.service = SessionDictationService(
            session_repo=sessions,
            note_service=self.note_service,
            dictation_repo=dictations,
            redraft_service=NoteRedraftService(
                sessions, patients, self.note_service, self.generator, dictations
            ),
            storage=LocalFileStorage(),
            bucket=str(tmp_path),
            transcriber=self.transcriber,
        )

    def note(self) -> Any:
        return self.notes.get(self.note_id, USER)

    def sign(self) -> None:
        note = self.note()
        note.finalized_at = datetime.now(UTC)
        self.notes.update(note, USER)

    def dictate(self, edits: RedraftEdits | None = None) -> tuple[str, bool]:
        dictation, _, keep = self.service.start(
            self.session.id,
            USER,
            audio=WEBM,
            content_type="audio/webm",
            duration_seconds=42,
            edits=edits,
        )
        return dictation.id, keep


@pytest.fixture
def world(
    tmp_path: Path,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_dictation_repo: InMemorySessionDictationRepository,
) -> World:
    return World(tmp_path, mock_session_repo, mock_repo, mock_notes_repo, mock_dictation_repo)


def test_cleaning_drops_filler_and_adds_nothing() -> None:
    assert clean_dictation("um, next session: uh two weeks from today") == (
        "Next session: two weeks from today."
    )
    assert clean_dictation("  Called the pharmacy.  ") == "Called the pharmacy."
    assert clean_dictation("um uh") == ""


class TestUnsignedNote:
    def test_a_dictation_redrafts_the_note_as_the_clinicians_addendum(self, world: World) -> None:
        dictation_id, keep = world.dictate()
        assert keep is True
        assert world.note().status == "processing"

        dictation, note = world.service.run(dictation_id, USER, keep_edits=keep)

        assert dictation.status == "transcribed"
        assert dictation.used_as == "redraft"
        assert world.transcriber.heard == [(WEBM, "audio/webm")]
        assert world.generator.transcripts == [
            f"[00:01] Therapist: Hello\n\n{DICTATED_HEADING}\n\n{SAID}"
        ]
        assert note is not None
        assert note.content == _visit("Redrafted.", "Redrafted plan.")
        assert note.status == "complete"

    def test_edits_are_kept_by_default_and_the_rest_redrafted(self, world: World) -> None:
        world.note_service.update_note_edits(world.note_id, _visit("Mine.", "First plan."), USER)
        dictation_id, keep = world.dictate()

        _, note = world.service.run(dictation_id, USER, keep_edits=keep)

        assert note is not None
        assert note.content_edited == _visit("Mine.", "Redrafted plan.")

    def test_redraft_everything_replaces_the_edits(self, world: World) -> None:
        world.note_service.update_note_edits(world.note_id, _visit("Mine."), USER)
        dictation_id, keep = world.dictate(RedraftEdits.REPLACE)
        assert keep is False

        _, note = world.service.run(dictation_id, USER, keep_edits=keep)

        assert note is not None
        assert note.content_edited is None

    def test_every_dictation_reaches_a_later_redraft(self, world: World) -> None:
        first, keep = world.dictate()
        world.service.run(first, USER, keep_edits=keep)
        world.transcriber.text = "Called the pharmacy."
        second, keep = world.dictate()

        world.service.run(second, USER, keep_edits=keep)

        assert world.generator.transcripts[-1].endswith(f"{SAID}\n\nCalled the pharmacy.")

    def test_a_clip_that_cant_be_transcribed_leaves_the_note_as_it_was(self, world: World) -> None:
        dictation_id, keep = world.dictate()
        world.transcriber.error = DictationTranscriptionError("refused")

        with pytest.raises(SOAPGenerationFailedError):
            world.service.run(dictation_id, USER, keep_edits=keep)

        assert world.dictations.get(dictation_id).status == "failed"  # type: ignore[union-attr]  # just written
        assert world.note().status == "complete"
        assert world.note().content == _visit("First draft.", "First plan.")

    def test_a_provider_that_doesnt_answer_is_retried_then_failed(self, world: World) -> None:
        dictation_id, keep = world.dictate()
        world.transcriber.error = TransientDictationTranscriptionError("timeout")

        with pytest.raises(TransientSOAPGenerationError):
            world.service.run(dictation_id, USER, keep_edits=keep)
        assert world.dictations.get(dictation_id).status == "transcribing"  # type: ignore[union-attr]  # exists

        with pytest.raises(SOAPGenerationFailedError):
            world.service.run(dictation_id, USER, keep_edits=keep, transient_is_terminal=True)
        assert world.dictations.get(dictation_id).status == "failed"  # type: ignore[union-attr]  # exists

    def test_a_second_delivery_does_nothing_more(self, world: World) -> None:
        dictation_id, keep = world.dictate()
        world.service.run(dictation_id, USER, keep_edits=keep)

        with pytest.raises(DictationNotPendingError):
            world.service.run(dictation_id, USER, keep_edits=keep)
        assert len(world.transcriber.heard) == 1
        assert len(world.generator.transcripts) == 1


class TestSignedNote:
    def test_a_dictation_becomes_a_draft_addendum_and_the_body_is_untouched(
        self, world: World
    ) -> None:
        world.sign()
        signed = world.note()
        dictation_id, keep = world.dictate()
        assert world.note().status == "complete"

        with pytest.raises(DictationNotPendingError):
            world.service.run(dictation_id, USER, keep_edits=keep)

        dictation = world.dictations.get(dictation_id)
        assert dictation is not None
        assert dictation.used_as == "addendum"
        assert draft_addendum(dictation) == "Next session: two weeks from today."
        assert world.generator.transcripts == []
        note = world.note()
        assert (note.content, note.content_edited, note.finalized_at) == (
            signed.content,
            signed.content_edited,
            signed.finalized_at,
        )

    def test_signed_while_transcribing_still_becomes_an_addendum(self, world: World) -> None:
        dictation_id, keep = world.dictate()
        world.sign()

        with pytest.raises(DictationNotPendingError):
            world.service.run(dictation_id, USER, keep_edits=keep)

        assert world.dictations.get(dictation_id).used_as == "addendum"  # type: ignore[union-attr]  # exists
        assert world.note().status == "complete"
        assert world.generator.transcripts == []


class TestRecord:
    def test_dictation_is_kept_apart_from_the_sessions_time_and_transcript(
        self, world: World
    ) -> None:
        before = world.sessions.get(world.session.id, USER)
        dictation_id, keep = world.dictate()
        world.service.run(dictation_id, USER, keep_edits=keep)

        after = world.sessions.get(world.session.id, USER)
        assert before is not None
        assert after is not None
        assert (after.started_at, after.ended_at, after.duration_minutes) == (
            before.started_at,
            before.ended_at,
            before.duration_minutes,
        )
        assert after.transcript.content == "[00:01] Therapist: Hello"
        assert world.dictations.get(dictation_id).duration_seconds == 42  # type: ignore[union-attr]  # exists

    def test_the_repository_offers_no_way_to_delete_or_rewrite(self) -> None:
        public = {
            name for name in dir(InMemorySessionDictationRepository) if not name.startswith("_")
        }
        assert public == {"add", "get", "list_for_session", "record_transcript", "link_addendum"}

    def test_no_transcriber_means_no_dictation(self, world: World) -> None:
        world.service.transcriber = None
        with pytest.raises(DictationUnavailableError):
            world.dictate()


class TestTranscribers:
    def _settings(self, **overrides: Any) -> Settings:
        return Settings(database_url="postgresql://x:x@localhost:5432/x", **overrides)

    def test_the_stand_in_is_used_where_configured(self) -> None:
        settings = self._settings(
            environment="development", dictation_transcription_base_url="http://fake/t"
        )
        assert isinstance(get_dictation_transcriber(settings), HttpDictationTranscriber)

    def test_the_provider_is_used_when_transcription_is_on(self) -> None:
        settings = self._settings(
            transcription_enabled=True,
            transcription_provider="assemblyai",
            assemblyai_api_key=SecretStr("k"),
        )
        assert isinstance(get_dictation_transcriber(settings), AssemblyAiDictationTranscriber)

    def test_nothing_to_transcribe_with(self) -> None:
        assert get_dictation_transcriber(self._settings()) is None

    def test_the_stand_in_is_refused_outside_development(self) -> None:
        with pytest.raises(ValidationError, match="DICTATION_TRANSCRIPTION_BASE_URL"):
            self._settings(environment="production", dictation_transcription_base_url="http://x")


# --- Routes ---


@pytest.fixture
def route_world(
    client: TestClient,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[World]:
    app.dependency_overrides[get_note_generation_service] = lambda: world.generator
    monkeypatch.setattr(
        dictation_routes, "file_storage_from_settings", lambda _s: world.service.storage
    )
    monkeypatch.setattr(
        dictation_routes,
        "get_settings",
        lambda: Settings(
            database_url="postgresql://x:x@localhost:5432/x",
            transcription_audio_bucket=world.service.bucket,
        ),
    )
    monkeypatch.setattr(dictation_routes, "get_dictation_transcriber", lambda _s: world.transcriber)
    yield world
    app.dependency_overrides.pop(get_note_generation_service, None)


def _audit_actions(audit: Any) -> list[str]:
    return [call.args[0].action for call in audit._repo.append.call_args_list]


class TestRoutes:
    def test_a_dictation_is_taken_audited_and_queued(
        self,
        client: TestClient,
        route_world: World,
        monkeypatch: pytest.MonkeyPatch,
        mock_audit_service: Any,
    ) -> None:
        queued: list[dict[str, Any]] = []
        monkeypatch.setattr(
            dictation_routes, "enqueue", lambda _q, _p, payload, **_k: queued.append(payload)
        )

        response = client.post(
            f"/api/sessions/{route_world.session.id}/dictations",
            files={"audio": ("clip.webm", WEBM, "audio/webm;codecs=opus")},
            data={"duration_seconds": "42"},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "transcribing"
        assert queued == [{"dictation_id": body["id"], "user_id": USER, "keep_edits": True}]
        assert route_world.note().status == "processing"
        assert "session_dictation_added" in _audit_actions(mock_audit_service)

    def test_something_that_isnt_audio_is_refused(
        self, client: TestClient, route_world: World
    ) -> None:
        response = client.post(
            f"/api/sessions/{route_world.session.id}/dictations",
            files={"audio": ("clip.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 415

    def test_without_a_transcriber_dictation_is_unavailable(
        self, client: TestClient, route_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dictation_routes, "get_dictation_transcriber", lambda _s: None)
        response = client.post(
            f"/api/sessions/{route_world.session.id}/dictations",
            files={"audio": ("clip.webm", WEBM, "audio/webm")},
        )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "DICTATION_UNAVAILABLE"

    def test_signing_the_draft_addendum_attaches_it_and_ends_the_draft(
        self,
        client: TestClient,
        route_world: World,
        monkeypatch: pytest.MonkeyPatch,
        mock_audit_service: Any,
    ) -> None:
        route_world.sign()
        dictation_id, keep = route_world.dictate()
        with pytest.raises(DictationNotPendingError):
            route_world.service.run(dictation_id, USER, keep_edits=keep)
        monkeypatch.setattr(
            notes_routes, "get_session_dictation_repository", lambda: route_world.dictations
        )

        listed = client.get(f"/api/sessions/{route_world.session.id}/dictations").json()["data"]
        assert listed[0]["draft_addendum"] == "Next session: two weeks from today."

        added = client.post(
            f"/api/notes/{route_world.note_id}/addenda",
            json={
                "text": "Next session: two weeks from today, same time.",
                "signer_name": "Sam Ortiz",
                "dictation_id": dictation_id,
            },
        )
        assert added.status_code == 201, added.text

        listed = client.get(f"/api/sessions/{route_world.session.id}/dictations").json()["data"]
        assert listed[0]["draft_addendum"] is None
        assert listed[0]["addendum_id"] == added.json()["id"]
        actions = _audit_actions(mock_audit_service)
        assert "session_dictations_viewed" in actions
        assert "note_addendum_added" in actions

    def test_an_addendum_cant_claim_someone_elses_dictation(
        self, client: TestClient, route_world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        route_world.sign()
        monkeypatch.setattr(
            notes_routes, "get_session_dictation_repository", lambda: route_world.dictations
        )
        added = client.post(
            f"/api/notes/{route_world.note_id}/addenda",
            json={"text": "x", "signer_name": "Sam Ortiz", "dictation_id": str(uuid.uuid4())},
        )
        assert added.status_code == 404

    def test_the_job_transcribes_and_redrafts(self, route_world: World) -> None:
        dictation_id, keep = route_world.dictate()
        result = dictation_routes.session_dictation_job(
            dictation_routes.SessionDictationJob(
                dictation_id=dictation_id, user_id=USER, keep_edits=keep
            ),
            MagicMock(headers={}),
            None,
            route_world.service,
            MagicMock(),
            MagicMock(),
        )
        assert result == {"status": "ok"}
        assert route_world.note().content == _visit("Redrafted.", "Redrafted plan.")


@pytest.fixture(autouse=True)
def _scoped_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dictation_routes, "resolve_tenant_schema_for_user", lambda _u: "p_x")
    monkeypatch.setattr(dictation_routes, "get_db_session", MagicMock)
    monkeypatch.setattr(dictation_routes, "set_tenant_schema", MagicMock())
    monkeypatch.setattr(dictation_routes, "arm_current_user_id", MagicMock())
