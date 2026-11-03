# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every point where a recorded session stops short of a note logs one shape.

A deployment alerts on ``event = "session_audio_failure"``. These pin that each
failure point carries it, with the stage and the clinician's id, and that a
recording that keeps failing to upload is reported once, on the third start.
"""

from __future__ import annotations

import logging
import types
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import SessionStatus, TherapySession, Transcript
from app.routes import internal_transcription as it
from app.routes import sessions as sessions_routes
from app.services import SOAPGenerationFailedError
from app.services.file_storage import UploadTarget
from app.services.session_audio_failure import (
    SESSION_AUDIO_FAILURE_EVENT,
    UPLOAD_STARTS_BEFORE_ALERT,
    session_audio_failure_extra,
)

if TYPE_CHECKING:
    import pytest


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == SESSION_AUDIO_FAILURE_EVENT]


def test_the_extra_carries_identifiers_and_a_stage_only() -> None:
    assert session_audio_failure_extra("note", "u1") == {
        "event": "session_audio_failure",
        "stage": "note",
        "user_id": "u1",
    }


# --- Upload: the third start that never finished ---------------------------------


def _seed(repo: object, owner: str, status: SessionStatus) -> TherapySession:
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=owner,
        patient_id=str(uuid.uuid4()),
        session_date=datetime(2026, 2, 4, tzinfo=UTC),
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content=""),
        created_at=datetime.now(UTC),
    )
    created: TherapySession = repo.create(session)  # type: ignore[attr-defined]
    return created


def _upload_settings() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        transcription_enabled=True,
        transcription_provider="assemblyai",
        transcription_audio_bucket="test-audio-bucket",
        transcription_audio_upload_url_ttl_seconds=3600,
        transcription_audio_max_bytes=500 * 1024 * 1024,
        pablo_edition="solo",
        is_development=False,
    )


def _start_upload(client: object, session_id: str) -> None:
    storage = MagicMock()
    storage.make_upload_target.return_value = UploadTarget(
        url="https://storage.example/put", method="PUT"
    )
    storage.fetch_metadata.return_value = None
    with (
        patch("app.routes.sessions.get_settings", return_value=_upload_settings()),
        patch("app.services.file_storage.file_storage_from_settings", return_value=storage),
    ):
        resp = client.post(f"/api/sessions/{session_id}/upload-audio/init")  # type: ignore[attr-defined]
    assert resp.status_code == 201, resp.text


def test_the_third_upload_start_without_a_finish_is_reported_once(
    client: object,
    mock_session_repo: object,
    mock_user_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _seed(mock_session_repo, mock_user_id, SessionStatus.RECORDING_COMPLETE)

    with caplog.at_level(logging.ERROR, logger=sessions_routes.logger.name):
        for _ in range(UPLOAD_STARTS_BEFORE_ALERT - 1):
            _start_upload(client, session.id)
        assert _failure_records(caplog) == []

        _start_upload(client, session.id)
        reported = _failure_records(caplog)
        assert len(reported) == 1
        assert reported[0].stage == "upload"  # type: ignore[attr-defined]
        assert reported[0].user_id == mock_user_id  # type: ignore[attr-defined]

        # Later retries are the same stuck recording, not new ones.
        _start_upload(client, session.id)
        assert len(_failure_records(caplog)) == 1

    stored = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
    assert stored.transcription_job_metadata["upload_starts"] == UPLOAD_STARTS_BEFORE_ALERT + 1


def test_starts_after_the_audio_arrived_are_not_counted(
    client: object,
    mock_session_repo: object,
    mock_user_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A stray re-upload of a session already transcribing is not a stuck recording.
    session = _seed(mock_session_repo, mock_user_id, SessionStatus.TRANSCRIBING)

    with caplog.at_level(logging.ERROR, logger=sessions_routes.logger.name):
        for _ in range(UPLOAD_STARTS_BEFORE_ALERT + 1):
            _start_upload(client, session.id)

    assert _failure_records(caplog) == []
    stored = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
    assert not (stored.transcription_job_metadata or {}).get("upload_starts")


# --- Transcription submit and poll -------------------------------------------------


def _fake_db(session_row: object) -> MagicMock:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = session_row
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm


def _submit(session_row: types.SimpleNamespace, service: MagicMock) -> None:
    storage = MagicMock()
    storage.fetch_metadata.return_value = (15, "application/octet-stream")
    storage.download_bytes.return_value = b"audio"
    settings = types.SimpleNamespace(
        transcription_audio_bucket="bucket",
        transcription_task_queue="queue",
        assemblyai_chart_keyterms=False,
        transcription_inline_audio_max_bytes=256 * 1024 * 1024,
    )
    with (
        patch.object(it, "_resolve_schema_for_user", return_value=None),
        patch.object(it, "create_standalone_session", return_value=_fake_db(session_row)),
        patch.object(it, "file_storage_from_settings", return_value=storage),
        patch.object(it, "AssemblyAiTranscriptionService", return_value=service),
        patch.object(it, "get_settings", return_value=settings),
        patch.object(it, "enqueue_cloud_task"),
    ):
        it.assemblyai_submit(
            it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1"), _invoker=None
        )


def _row(audio_gcs_path: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        video_platform=None,
        transcription_job_metadata=None,
        audio_gcs_path=audio_gcs_path,
        status="transcribing",
        error=None,
    )


def test_a_failed_transcription_submit_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    service = MagicMock()
    service.submit_dual_channel = AsyncMock(side_effect=RuntimeError("provider down"))

    with caplog.at_level(logging.ERROR, logger=it.logger.name):
        _submit(_row("audio/s1/therapist.pcm,audio/s1/client.pcm"), service)

    [record] = _failure_records(caplog)
    assert (record.stage, record.user_id) == ("transcription_submit", "u1")  # type: ignore[attr-defined]


def test_a_submit_with_no_audio_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger=it.logger.name):
        _submit(_row(None), MagicMock())

    [record] = _failure_records(caplog)
    assert (record.stage, record.user_id) == ("transcription_submit", "u1")  # type: ignore[attr-defined]


def _poll(poll_cycles: int, job_status: tuple[str, dict[str, str] | None]) -> types.SimpleNamespace:
    session_row = types.SimpleNamespace(
        video_platform=None,
        transcription_job_metadata={
            "provider": "assemblyai",
            "jobs": [{"transcript_id": "t1", "speaker": "Therapist", "original_offset": 0.0}],
            "poll_cycles": poll_cycles,
        },
        audio_gcs_path=None,
        status="transcribing",
        error=None,
    )
    settings = types.SimpleNamespace(
        is_development=True,
        multi_tenancy_enabled=False,
        transcription_task_queue="queue",
        assemblyai_api_key=MagicMock(get_secret_value=MagicMock(return_value="key")),
    )
    with (
        patch.object(it, "_resolve_schema_for_user", return_value=None),
        patch.object(it, "create_standalone_session", return_value=_fake_db(session_row)),
        patch.object(it, "get_settings", return_value=settings),
        patch.object(
            it.AssemblyAiTranscriptionService, "check_job_status", return_value=job_status
        ),
        patch.object(it, "enqueue_cloud_task"),
    ):
        it.transcription_poll(
            it.TranscriptionPollRequest(session_id="s1", user_id="u1"), _invoker=None
        )
    return session_row


def test_a_transcription_job_error_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger=it.logger.name):
        row = _poll(0, ("error", {"error": "audio could not be decoded"}))

    assert row.status == "failed"
    [record] = _failure_records(caplog)
    assert (record.stage, record.user_id) == ("transcription", "u1")  # type: ignore[attr-defined]


def test_a_transcription_that_never_finishes_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger=it.logger.name):
        row = _poll(it._MAX_POLL_CYCLES - 1, ("processing", None))

    assert row.status == "failed"
    [record] = _failure_records(caplog)
    assert (record.stage, record.user_id) == ("transcription", "u1")  # type: ignore[attr-defined]


# --- Note generation -----------------------------------------------------------------


def test_a_failed_note_generation_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.ERROR, logger=sessions_routes.logger.name),
        patch.object(
            sessions_routes, "run_soap_generation_job", side_effect=SOAPGenerationFailedError()
        ),
        patch.object(sessions_routes, "_is_final_soap_attempt", return_value=False),
    ):
        result = sessions_routes.generate_soap_job(
            sessions_routes.GenerateSoapJob(session_id="s1", user_id="u1"),
            http_request=MagicMock(),
            session_service=MagicMock(),
            user_repo=MagicMock(),
            audit=MagicMock(),
        )

    assert result == {"status": "failed"}
    [record] = _failure_records(caplog)
    assert (record.stage, record.user_id) == ("note", "u1")  # type: ignore[attr-defined]
