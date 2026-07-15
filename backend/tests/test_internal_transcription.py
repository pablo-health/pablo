# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the internal transcription lifecycle endpoints.

Covers the off-request AssemblyAI submit worker and the poll hand-off. These
handlers open their own standalone DB session and are gated to Cloud Tasks, so
they're exercised as plain functions with the DB / storage / provider calls
mocked — no HTTP client, no tenant fixtures.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

from app.routes import internal_transcription as it

_JOB = {"transcript_id": "t1", "speaker": "Therapist", "original_offset": 0.0}


def _fake_session_db(session_row: object) -> tuple[MagicMock, MagicMock]:
    """A ``create_standalone_session`` stand-in whose one query returns ``session_row``."""
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = session_row
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm, db


class TestAssemblyAiSubmitWorker:
    def test_submits_channels_and_enqueues_poll(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata=None,
            audio_gcs_path="audio/s1/therapist.pcm,audio/s1/client.pcm",
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        fake_storage = MagicMock()
        fake_storage.download_bytes.side_effect = [b"therapist-bytes", b"client-bytes"]
        service = MagicMock()
        service.submit_dual_channel = AsyncMock(return_value=[_JOB])
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "file_storage_from_settings", return_value=fake_storage),
            patch.object(it, "AssemblyAiTranscriptionService", return_value=service),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "ok"
        service.submit_dual_channel.assert_awaited_once()
        # Both channel objects were downloaded from storage.
        assert fake_storage.download_bytes.call_count == 2
        # Provider job ids replaced the "submitting" marker.
        assert session_row.transcription_job_metadata == {"provider": "assemblyai", "jobs": [_JOB]}
        # Poll was queued.
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args.kwargs["endpoint_path"] == "/api/internal/transcription-poll"

    def test_already_submitted_is_idempotent(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [_JOB]},
            audio_gcs_path="audio/s1/therapist.pcm,audio/s1/client.pcm",
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        service = MagicMock()
        service.submit_dual_channel = AsyncMock(return_value=[_JOB])
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "AssemblyAiTranscriptionService", return_value=service),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "already_submitted"
        # A retry must not re-submit to the provider, but must still ensure the poller runs.
        service.submit_dual_channel.assert_not_awaited()
        mock_enqueue.assert_called_once()

    def test_missing_audio_marks_failed(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata=None,
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "error"
        assert session_row.status == it.SessionStatus.FAILED.value
        mock_enqueue.assert_not_called()

    def test_missing_session_is_dropped(self) -> None:
        cm, _db = _fake_session_db(None)
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "not_found"
        mock_enqueue.assert_not_called()


class TestTranscriptionPollLabel:
    def test_completion_hands_off_as_google_meet(self) -> None:
        """The AssemblyAI merged transcript is google_meet-shaped, NOT WebVTT.

        Labeling it "vtt" routes it to the _normalize_vtt parser, which finds
        no "-->" cues and returns "" — every SOAP then comes back empty. This
        guards that regression: the poll hand-off must pass google_meet.
        """
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [_JOB]},
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        settings = types.SimpleNamespace(
            is_development=True,
            multi_tenancy_enabled=False,
            transcription_task_queue="queue",
            assemblyai_api_key=MagicMock(get_secret_value=MagicMock(return_value="key")),
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(
                it,
                "_poll_assemblyai_jobs",
                return_value=([(_JOB, {"text": "hi"})], True, None),
            ),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "process_completed_jobs",
                return_value="[00:00:00]\nTherapist: hi",
            ),
            patch.object(
                it,
                "process_transcription_result",
                return_value={"id": "s1", "status": "processing", "message": "ok"},
            ) as mock_process,
        ):
            it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert mock_process.call_args.kwargs["transcript_format"] == "google_meet"

    def test_incomplete_reenqueues_and_does_not_hand_off(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [_JOB, _JOB]},
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        settings = types.SimpleNamespace(
            is_development=True,
            multi_tenancy_enabled=False,
            transcription_task_queue="queue",
            assemblyai_api_key=MagicMock(get_secret_value=MagicMock(return_value="key")),
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(
                it,
                "_poll_assemblyai_jobs",
                return_value=([(_JOB, {"text": "hi"})], False, None),
            ),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        mock_enqueue.assert_called_once()
        mock_process.assert_not_called()
