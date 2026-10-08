# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session drafts on the end-to-end stack.

The stack has no task queue, so an uploaded transcript would sit in
``processing`` forever. Where the drafting stand-in is configured
(``note_generation_base_url``, development only), the upload runs the
generate-soap job itself, after the response, with the stand-in drafting.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.models import Patient, SessionStatus
from app.routes import notes as notes_routes
from app.routes import sessions as sessions_routes
from app.routes.sessions import GenerateSoapJob, _draft_in_process
from app.services.note_generation_service import RegistryNoteGenerationService
from app.settings import Settings

from .test_note_generation_stand_in import BASE_URL
from .test_routes_sessions import _seed_session

if TYPE_CHECKING:
    from app.repositories import InMemoryPatientRepository, InMemoryTherapySessionRepository
    from fastapi.testclient import TestClient


def _settings(*, base_url: str | None) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment="development",
        note_generation_base_url=base_url,
    )


def test_session_drafts_come_from_the_same_generator_as_standalone_notes() -> None:
    """One factory, so the stand-in setting reaches the session path too."""
    assert sessions_routes.get_note_generation_service is notes_routes.get_note_generation_service


def _upload(client: TestClient, patient_id: str) -> Any:
    return client.post(
        f"/api/patients/{patient_id}/sessions/upload",
        json={
            "patient_id": patient_id,
            "session_date": "2026-10-05T14:00:00",
            "transcript": {"format": "txt", "content": "[00:01] Therapist: Hello"},
        },
    )


@pytest.fixture
def patient_id(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> str:
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return mock_repo.create(patient, mock_user_id).id


@pytest.mark.parametrize(("base_url", "drafted"), [(BASE_URL, True), (None, False)])
def test_an_upload_drafts_in_process_only_where_the_stand_in_is_configured(
    client: TestClient,
    patient_id: str,
    mock_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str | None,
    *,
    drafted: bool,
) -> None:
    jobs: list[GenerateSoapJob] = []
    monkeypatch.setattr(sessions_routes, "get_settings", lambda: _settings(base_url=base_url))
    monkeypatch.setattr(sessions_routes, "enqueue", lambda *_a, **_k: None)
    monkeypatch.setattr(sessions_routes, "_draft_in_process", lambda job, *_rest: jobs.append(job))

    response = _upload(client, patient_id)

    assert response.status_code == 202, response.text
    expected = [GenerateSoapJob(session_id=response.json()["id"], user_id=mock_user_id)]
    assert jobs == (expected if drafted else [])


@pytest.mark.parametrize(("base_url", "drafted"), [(BASE_URL, True), (None, False)])
def test_a_transcript_added_to_a_session_drafts_in_process_too(
    client: TestClient,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str | None,
    *,
    drafted: bool,
) -> None:
    """The session a scheduled visit started is drafted the same way an upload is."""
    jobs: list[GenerateSoapJob] = []
    monkeypatch.setattr(sessions_routes, "get_settings", lambda: _settings(base_url=base_url))
    monkeypatch.setattr(sessions_routes, "enqueue", lambda *_a, **_k: None)
    monkeypatch.setattr(sessions_routes, "_draft_in_process", lambda job, *_rest: jobs.append(job))
    session = _seed_session(
        mock_session_repo, owner=mock_user_id, status=SessionStatus.RECORDING_COMPLETE
    )

    response = client.post(
        f"/api/sessions/{session.id}/transcript",
        json={"format": "txt", "content": "[00:00] Hello."},
    )

    assert response.status_code == 202, response.text
    expected = [GenerateSoapJob(session_id=session.id, user_id=mock_user_id)]
    assert jobs == (expected if drafted else [])


def test_the_in_process_draft_runs_the_worker_job_in_the_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped: list[tuple[str, str]] = []
    ran: list[tuple[GenerateSoapJob, Any]] = []
    generator = RegistryNoteGenerationService()
    monkeypatch.setattr(
        sessions_routes, "resolve_tenant_schema_for_user", lambda _uid: "practice_abc"
    )

    def _tenant_db_session(schema: str, user_id: str) -> nullcontext[None]:
        scoped.append((schema, user_id))
        return nullcontext()

    def _job(payload: GenerateSoapJob, _req: Any, _invoker: None, service: Any, *_: Any) -> None:
        ran.append((payload, service))

    monkeypatch.setattr(sessions_routes, "tenant_db_session", _tenant_db_session)
    monkeypatch.setattr(sessions_routes, "generate_soap_job", _job)
    monkeypatch.setattr(sessions_routes, "_session_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_patient_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_notes_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_problem_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_medication_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_history_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "_proposal_repo_factory", MagicMock)
    monkeypatch.setattr(sessions_routes, "get_user_repository", MagicMock)
    monkeypatch.setattr(sessions_routes, "get_audit_service", MagicMock)
    job = GenerateSoapJob(session_id="s-1", user_id="u-1")

    _draft_in_process(job, MagicMock(), generator)

    assert scoped == [("practice_abc", "u-1")]
    assert [payload for payload, _service in ran] == [job]
    assert ran[0][1].note_generation_service is generator


def test_the_in_process_draft_skips_a_user_with_no_practice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions_routes, "resolve_tenant_schema_for_user", lambda _uid: None)
    job_runner = MagicMock()
    monkeypatch.setattr(sessions_routes, "generate_soap_job", job_runner)

    _draft_in_process(
        GenerateSoapJob(session_id="s-1", user_id="u-1"),
        MagicMock(),
        RegistryNoteGenerationService(),
    )

    job_runner.assert_not_called()
