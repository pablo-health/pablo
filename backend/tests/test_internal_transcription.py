# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the internal transcription lifecycle endpoints.

Covers the off-request AssemblyAI submit worker and the poll hand-off. These
handlers open their own standalone DB session and are gated to Cloud Tasks, so
they're exercised as plain functions with the DB / storage / provider calls
mocked — no HTTP client, no tenant fixtures.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
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
        # The staging factory writes the prepared speech audio next to the
        # original object and hands back a presigned GET for AssemblyAI.
        factory = service.submit_dual_channel.await_args.kwargs["audio_url_factory"]
        fake_storage.make_download_url.return_value = "https://signed.example/speech"
        assert factory("Therapist", b"wav-bytes") == "https://signed.example/speech"
        fake_storage.upload_bytes.assert_called_once_with(
            bucket="bucket",
            object_name="audio/s1/therapist.pcm.speech.wav",
            data=b"wav-bytes",
            content_type="audio/wav",
        )
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

    def test_segment_submit_appends_tagged_job_with_shifted_offsets_and_skips_poll(self) -> None:
        """A non-final segment's jobs are appended and tagged, but the poller

        must not be enqueued yet — polling before the session finishes
        recording would burn the poll budget on a job that isn't done.
        """
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "final": False,
                "segments": [
                    {
                        "index": 0,
                        "offset_seconds": 300.0,
                        "therapist": "audio/s1/therapist.seg000.pcm",
                        "client": "audio/s1/client.seg000.pcm",
                    }
                ],
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        fake_storage = MagicMock()
        fake_storage.download_bytes.side_effect = [b"therapist-bytes", b"client-bytes"]
        submitted_job = {
            "transcript_id": "t1",
            "speaker": "Therapist",
            "offset_map": [[0.0, 0.0], [5.0, 5.0]],
            "diarized": False,
        }
        service = MagicMock()
        service.submit_dual_channel = AsyncMock(return_value=[dict(submitted_job)])
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )
        real_shift_job_offsets = it.AssemblyAiTranscriptionService.shift_job_offsets

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "file_storage_from_settings", return_value=fake_storage),
            patch.object(it, "AssemblyAiTranscriptionService", return_value=service) as mock_cls,
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            # The class itself is mocked out to intercept the instance
            # (``submit_dual_channel``), but ``shift_job_offsets`` is a real
            # staticmethod call the worker makes on the class — restore it so
            # the shift actually runs.
            mock_cls.shift_job_offsets = real_shift_job_offsets
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1", segment_index=0),
                _invoker=None,
            )

        assert result["status"] == "ok"
        jobs = session_row.transcription_job_metadata["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["segment_index"] == 0
        assert jobs[0]["offset_map"] == [[0.0, 300.0], [5.0, 305.0]]
        mock_enqueue.assert_not_called()

    def test_second_segment_submit_is_not_short_circuited_by_first_segments_jobs(self) -> None:
        """The whole-session ``already_submitted`` short-circuit (metadata has

        ``jobs``) must not swallow a later segment's own submit — only a job
        already tagged with *this* segment's index should short-circuit it.
        """
        prior_job = {"transcript_id": "t0", "speaker": "Therapist", "segment_index": 0}
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "final": True,
                "segments": [
                    {
                        "index": 0,
                        "offset_seconds": 0.0,
                        "therapist": "audio/s1/therapist.seg000.pcm",
                        "client": "audio/s1/client.seg000.pcm",
                    },
                    {
                        "index": 1,
                        "offset_seconds": 300.0,
                        "therapist": "audio/s1/therapist.seg001.pcm",
                        "client": "audio/s1/client.seg001.pcm",
                    },
                ],
                "jobs": [prior_job],
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        fake_storage = MagicMock()
        fake_storage.download_bytes.side_effect = [b"therapist-bytes", b"client-bytes"]
        submitted_job = {"transcript_id": "t1", "speaker": "Therapist", "diarized": False}
        service = MagicMock()
        service.submit_dual_channel = AsyncMock(return_value=[dict(submitted_job)])
        settings = types.SimpleNamespace(
            transcription_audio_bucket="bucket", transcription_task_queue="queue"
        )
        real_shift_job_offsets = it.AssemblyAiTranscriptionService.shift_job_offsets

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "file_storage_from_settings", return_value=fake_storage),
            patch.object(it, "AssemblyAiTranscriptionService", return_value=service) as mock_cls,
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            mock_cls.shift_job_offsets = real_shift_job_offsets
            result = it.assemblyai_submit(
                it.AssemblyAiSubmitRequest(session_id="s1", user_id="u1", segment_index=1),
                _invoker=None,
            )

        assert result["status"] == "ok"
        service.submit_dual_channel.assert_awaited_once()
        jobs = session_row.transcription_job_metadata["jobs"]
        assert [job.get("segment_index") for job in jobs] == [0, 1]
        # Both manifest segments now have a job and the manifest is final.
        mock_enqueue.assert_called_once()


def _poll_settings() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        is_development=True,
        multi_tenancy_enabled=False,
        transcription_task_queue="queue",
        assemblyai_api_key=MagicMock(get_secret_value=MagicMock(return_value="key")),
    )


class TestTranscriptionPoll:
    def test_completion_hands_off_as_google_meet(self) -> None:
        """The AssemblyAI merged transcript is google_meet-shaped, NOT WebVTT.

        Labeling it "vtt" routes it to the _normalize_vtt parser, which finds
        no "-->" cues and returns "" — every SOAP then comes back empty. This
        guards that regression: the poll hand-off must pass google_meet.
        """
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [dict(_JOB)]},
            audio_gcs_path="audio/s1/therapist.pcm,audio/s1/client.pcm",
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                return_value=("completed", {"text": "hi", "words": []}),
            ),
            patch.object(
                it,
                "process_transcription_result",
                return_value={"id": "s1", "status": "processing", "message": "ok"},
            ) as mock_process,
            patch.object(it, "_delete_staged_speech_objects") as mock_cleanup,
        ):
            it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert mock_process.call_args.kwargs["transcript_format"] == "google_meet"
        assert "Therapist: hi" in mock_process.call_args.kwargs["transcript_content"]
        mock_cleanup.assert_called_once_with("audio/s1/therapist.pcm,audio/s1/client.pcm", "s1")

    def test_completion_deletes_the_transcript_after_the_merge_hands_off(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [dict(_JOB)]},
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                return_value=("completed", {"text": "hi", "words": []}),
            ),
            patch.object(
                it,
                "process_transcription_result",
                return_value={"id": "s1", "status": "processing", "message": "ok"},
            ) as mock_process,
            patch.object(it.AssemblyAiTranscriptionService, "delete_transcript") as mock_delete,
        ):
            it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        mock_process.assert_called_once()
        mock_delete.assert_called_once_with("key", "t1")

    def test_delete_failure_is_logged_and_does_not_fail_the_poll(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [dict(_JOB)]},
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                return_value=("completed", {"text": "hi", "words": []}),
            ),
            patch.object(
                it,
                "process_transcription_result",
                return_value={"id": "s1", "status": "processing", "message": "ok"},
            ),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "delete_transcript",
                side_effect=httpx.HTTPStatusError(
                    "boom", request=MagicMock(), response=MagicMock(status_code=500)
                ),
            ),
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "processing"

    def test_incomplete_persists_progress_and_reenqueues_with_delay(self) -> None:
        """Fetch-once: a completed job's utterances are persisted so later
        cycles only poll the pending job, and the next cycle is scheduled
        with a delay instead of hot-looping."""
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "jobs": [
                    dict(_JOB),
                    {"transcript_id": "t2", "speaker": "Client", "original_offset": 0.0},
                ],
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                side_effect=[("completed", {"text": "hi", "words": []}), ("processing", None)],
            ),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        assert result["detail"] == "1/2 complete"
        mock_process.assert_not_called()
        # The completed job's parsed utterances were committed before the
        # re-enqueue so a retry never refetches them.
        metadata = session_row.transcription_job_metadata
        assert metadata["jobs"][0]["utterances"] == [
            {"start": 0.0, "end": 0.0, "speaker": "Therapist", "text": "hi"}
        ]
        assert "utterances" not in metadata["jobs"][1]
        assert metadata["poll_cycles"] == 1
        db.commit.assert_called_once()
        mock_enqueue.assert_called_once()
        assert (
            mock_enqueue.call_args.kwargs["schedule_delay_seconds"] == it._POLL_CYCLE_DELAY_SECONDS
        )

    def test_second_cycle_skips_already_fetched_jobs(self) -> None:
        completed_job = {
            **_JOB,
            "utterances": [{"start": 0.0, "end": 1.0, "speaker": "Therapist", "text": "hi"}],
        }
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "jobs": [
                    completed_job,
                    {"transcript_id": "t2", "speaker": "Client", "original_offset": 0.0},
                ],
                "poll_cycles": 1,
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                return_value=("processing", None),
            ) as mock_check,
            patch.object(it, "enqueue_cloud_task"),
        ):
            it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        # Only the pending job hit the provider.
        mock_check.assert_called_once_with("key", "t2")
        assert session_row.transcription_job_metadata["poll_cycles"] == 2

    def test_poll_budget_exhaustion_fails_the_session(self) -> None:
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "jobs": [dict(_JOB)],
                "poll_cycles": it._MAX_POLL_CYCLES - 1,
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                return_value=("processing", None),
            ),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "error"
        assert session_row.status == "failed"
        assert "timed out" in session_row.error
        mock_enqueue.assert_not_called()

    def test_provider_http_status_error_retries_next_cycle(self) -> None:
        """A transient provider 5xx must not fail the session or bubble up as

        a 500: the job stays pending and the existing re-enqueue path retries
        it next cycle, exactly like the submit path already tolerates
        provider failures.
        """
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [dict(_JOB)]},
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        request = httpx.Request("GET", "https://api.assemblyai.com/v2/transcript/t1")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Server error '503'", request=request, response=response)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                side_effect=error,
            ),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        assert session_row.status == "transcribing"
        assert session_row.error is None
        mock_process.assert_not_called()
        metadata = session_row.transcription_job_metadata
        assert "utterances" not in metadata["jobs"][0]
        assert metadata["poll_cycles"] == 1
        mock_enqueue.assert_called_once()
        assert (
            mock_enqueue.call_args.kwargs["schedule_delay_seconds"] == it._POLL_CYCLE_DELAY_SECONDS
        )

    def test_provider_read_timeout_retries_next_cycle(self) -> None:
        """Same tolerance as the 5xx case, for a network-level timeout."""
        session_row = types.SimpleNamespace(
            transcription_job_metadata={"provider": "assemblyai", "jobs": [dict(_JOB)]},
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)
        request = httpx.Request("GET", "https://api.assemblyai.com/v2/transcript/t1")
        error = httpx.ReadTimeout("timed out", request=request)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                side_effect=error,
            ),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        assert session_row.status == "transcribing"
        assert session_row.error is None
        mock_process.assert_not_called()
        metadata = session_row.transcription_job_metadata
        assert "utterances" not in metadata["jobs"][0]
        assert metadata["poll_cycles"] == 1
        mock_enqueue.assert_called_once()
        assert (
            mock_enqueue.call_args.kwargs["schedule_delay_seconds"] == it._POLL_CYCLE_DELAY_SECONDS
        )

    def test_segmented_all_jobs_complete_but_not_final_reenqueues(self) -> None:
        """Every recorded job is done, but the manifest isn't ``final`` yet —

        more segments are still coming, so this must not generate the note
        from a partial transcript.
        """
        completed_job = {
            **_JOB,
            "segment_index": 0,
            "utterances": [{"start": 0.0, "end": 1.0, "speaker": "Therapist", "text": "hi"}],
        }
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "final": False,
                "segments": [{"index": 0, "offset_seconds": 0.0, "therapist": "a", "client": "b"}],
                "jobs": [completed_job],
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        mock_process.assert_not_called()
        mock_enqueue.assert_called_once()

    def test_segmented_final_but_a_segment_still_lacks_a_job_reenqueues(self) -> None:
        """``final`` is set, but segment 1's submit task hasn't landed yet —

        its manifest entry exists with no matching job. Must not complete on
        the strength of segment 0's job alone.
        """
        completed_job = {
            **_JOB,
            "segment_index": 0,
            "utterances": [{"start": 0.0, "end": 1.0, "speaker": "Therapist", "text": "hi"}],
        }
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "final": True,
                "segments": [
                    {"index": 0, "offset_seconds": 0.0, "therapist": "a", "client": "b"},
                    {"index": 1, "offset_seconds": 300.0, "therapist": "c", "client": "d"},
                ],
                "jobs": [completed_job],
            },
            audio_gcs_path=None,
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(it, "enqueue_cloud_task") as mock_enqueue,
            patch.object(it, "process_transcription_result") as mock_process,
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "polling"
        mock_process.assert_not_called()
        mock_enqueue.assert_called_once()

    def test_segmented_final_and_all_segments_complete_merges_in_original_order(self) -> None:
        """Two segments (offsets 0 and 300) merge into one time-ordered transcript.

        Jobs were appended out of order (segment 1's submit task landed
        first) — the merge must still order by each utterance's
        original-timeline start, not by job list order.
        """
        job_segment_1 = {
            "transcript_id": "seg1",
            "speaker": "Therapist",
            "offset_map": [[0.0, 300.0]],
            "segment_index": 1,
        }
        job_segment_0 = {
            "transcript_id": "seg0",
            "speaker": "Therapist",
            "offset_map": [[0.0, 0.0]],
            "segment_index": 0,
        }
        session_row = types.SimpleNamespace(
            transcription_job_metadata={
                "provider": "assemblyai",
                "final": True,
                "segments": [
                    {"index": 0, "offset_seconds": 0.0, "therapist": "a", "client": "b"},
                    {"index": 1, "offset_seconds": 300.0, "therapist": "c", "client": "d"},
                ],
                "jobs": [job_segment_1, job_segment_0],
            },
            audio_gcs_path="audio/s1/base-therapist.pcm,audio/s1/base-client.pcm",
            status="transcribing",
            error=None,
        )
        cm, _db = _fake_session_db(session_row)

        def _check_job_status(_api_key: str, transcript_id: str) -> tuple[str, dict]:
            if transcript_id == "seg1":
                return ("completed", {"words": [{"start": 0, "end": 500, "text": "later"}]})
            return ("completed", {"words": [{"start": 0, "end": 500, "text": "earlier"}]})

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=_poll_settings()),
            patch.object(
                it.AssemblyAiTranscriptionService,
                "check_job_status",
                side_effect=_check_job_status,
            ),
            patch.object(
                it,
                "process_transcription_result",
                return_value={"id": "s1", "status": "processing", "message": "ok"},
            ) as mock_process,
            patch.object(it, "_delete_staged_speech_objects"),
        ):
            result = it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"),
                _invoker=None,
            )

        assert result["status"] == "processing"
        transcript = mock_process.call_args.kwargs["transcript_content"]
        assert transcript.index("earlier") < transcript.index("later")


class TestDeleteStagedSpeechObjects:
    def test_deletes_the_staged_sibling_for_each_channel(self) -> None:
        fake_storage = MagicMock()
        fake_storage.list_names.side_effect = [
            ["audio/s1/therapist.pcm.speech.wav"],
            ["audio/s1/client.pcm.speech.aac"],
        ]
        settings = types.SimpleNamespace(transcription_audio_bucket="bucket")

        with (
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "file_storage_from_settings", return_value=fake_storage),
        ):
            it._delete_staged_speech_objects("audio/s1/therapist.pcm,audio/s1/client.pcm", "s1")

        assert fake_storage.list_names.call_args_list == [
            call(bucket="bucket", prefix="audio/s1/therapist.pcm.speech."),
            call(bucket="bucket", prefix="audio/s1/client.pcm.speech."),
        ]
        assert fake_storage.delete.call_args_list == [
            call(bucket="bucket", object_name="audio/s1/therapist.pcm.speech.wav"),
            call(bucket="bucket", object_name="audio/s1/client.pcm.speech.aac"),
        ]

    def test_noop_when_path_missing_or_single_object(self) -> None:
        with patch.object(it, "file_storage_from_settings") as resolver:
            it._delete_staged_speech_objects(None, "s1")
            it._delete_staged_speech_objects("audio/s1/only.pcm", "s1")
        resolver.assert_not_called()

    def test_storage_failure_is_logged_without_the_object_name_and_does_not_raise(self) -> None:
        fake_storage = MagicMock()
        fake_storage.list_names.side_effect = RuntimeError("storage timeout")
        settings = types.SimpleNamespace(transcription_audio_bucket="bucket")

        with (
            patch.object(it, "get_settings", return_value=settings),
            patch.object(it, "file_storage_from_settings", return_value=fake_storage),
            patch.object(it, "logger") as mock_logger,
        ):
            it._delete_staged_speech_objects("audio/s1/therapist.pcm,audio/s1/client.pcm", "s1")

        logged = " | ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "audio/s1/therapist.pcm" not in logged
        assert "audio/s1/client.pcm" not in logged
        assert mock_logger.warning.call_count == 2
