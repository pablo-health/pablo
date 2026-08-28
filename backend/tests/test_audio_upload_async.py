# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Async audio-upload behaviour for the AssemblyAI provider.

The multipart ``/upload-audio`` streams both channels to object storage and
hands the provider submit to a Cloud Task (202) instead of buffering the bytes
and submitting inline; the signed-URL ``init``/``finalize`` pair now supports
AssemblyAI as well as Whisper. These tests pin that contract with storage, the
queue, and the provider mocked.
"""

from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.models import SessionStatus, TherapySession, Transcript
from app.services.file_storage import UploadTarget


def _seed_recording_complete(
    repo: object, owner: str, status: SessionStatus = SessionStatus.RECORDING_COMPLETE
) -> TherapySession:
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


def _assemblyai_settings(**overrides: object) -> types.SimpleNamespace:
    base = {
        "transcription_enabled": True,
        "transcription_provider": "assemblyai",
        "transcription_audio_bucket": "test-audio-bucket",
        "transcription_task_queue": "test-transcription-queue",
        "patient_documents_upload_url_ttl_seconds": 900,
        "transcription_audio_upload_url_ttl_seconds": 3600,
        "pablo_edition": "solo",
        "is_development": False,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


class TestUploadAudioAssemblyAiAsync:
    def test_streams_to_storage_and_enqueues_submit(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(mock_session_repo, mock_user_id)
        fake_storage = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue") as mock_enqueue,
            patch(
                "app.services.assemblyai_transcription_service."
                "AssemblyAiTranscriptionService.submit_dual_channel"
            ) as mock_submit,
        ):
            resp = client.post(  # type: ignore[attr-defined]
                f"/api/sessions/{session.id}/upload-audio",
                files={
                    "therapist_audio": ("t.pcm", b"therapist-audio", "application/octet-stream"),
                    "client_audio": ("c.pcm", b"client-audio", "application/octet-stream"),
                },
            )

        assert resp.status_code == 202, resp.text
        assert resp.json()["provider"] == "assemblyai"

        # Both channels streamed to storage — never buffered whole, never submitted inline.
        assert fake_storage.upload_stream.call_count == 2
        streamed_objects = {
            call.kwargs["object_name"] for call in fake_storage.upload_stream.call_args_list
        }
        assert streamed_objects == {
            f"audio/{session.id}/therapist.pcm",
            f"audio/{session.id}/client.pcm",
        }
        mock_submit.assert_not_called()

        # The submit worker was queued, not run in-request.
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args.args[1] == "/api/internal/assemblyai-submit"
        assert mock_enqueue.call_args.args[2] == {
            "session_id": session.id,
            "user_id": mock_user_id,
        }

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        assert updated.status == SessionStatus.TRANSCRIBING
        assert updated.audio_gcs_path == (
            f"audio/{session.id}/therapist.pcm,audio/{session.id}/client.pcm"
        )
        assert updated.transcription_job_metadata == {
            "provider": "assemblyai",
            "state": "submitting",
        }

    def test_finalize_commits_the_audio_path_before_dispatching_the_worker(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        """The submit worker must never be dispatched before the row is committed.

        The worker is a separate request that re-reads the session to find
        ``audio_gcs_path``. ``update()`` only flushes — the commit lands when this
        request ends — so enqueueing first races the worker, and the loser reads an
        empty path, logs "has no dual-channel audio path", and marks the session
        FAILED. The audio is uploaded and the note never arrives.

        Observed in the wild on dev: two identical harness runs minutes apart, one
        reached "SOAP generation queued", the other died at submit.

        The sibling test above asserts only the end state, which is identical
        whether the enqueue happens before or after the write — which is how this
        shipped. This pins the ordering instead.
        """
        session = _seed_recording_complete(mock_session_repo, mock_user_id)
        fake_storage = MagicMock()
        fake_storage.fetch_metadata.return_value = (1024, "application/octet-stream")

        # One parent records the interleaving of both calls.
        recorder = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.release_db_connection", recorder.commit),
            patch("app.routes.sessions.enqueue", recorder.enqueue),
        ):
            resp = client.post(  # type: ignore[attr-defined]
                f"/api/sessions/{session.id}/upload-audio/finalize",
            )

        assert resp.status_code == 202, resp.text
        order = [name for name, _, _ in recorder.mock_calls]
        assert "commit" in order, "the transaction must be committed before the worker runs"
        assert "enqueue" in order
        assert order.index("commit") < order.index("enqueue"), (
            f"worker dispatched before the audio path was committed: {order}"
        )


class TestSignedUrlAssemblyAi:
    def test_init_mints_targets_for_assemblyai(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(mock_session_repo, mock_user_id)
        fake_storage = MagicMock()
        fake_storage.make_upload_target.return_value = UploadTarget(
            url="https://storage.example/put", method="PUT"
        )

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
        ):
            resp = client.post(  # type: ignore[attr-defined]
                f"/api/sessions/{session.id}/upload-audio/init",
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["therapist"]["gcs_path"] == f"signed/{session.id}/therapist.pcm"
        assert body["client"]["gcs_path"] == f"signed/{session.id}/client.pcm"

        # Audio uploads get their own (longer) TTL, not the document TTL.
        assert fake_storage.make_upload_target.call_count == 2
        for call in fake_storage.make_upload_target.call_args_list:
            assert call.kwargs["ttl_seconds"] == 3600
            assert call.kwargs["ttl_seconds"] != 900

    def test_finalize_enqueues_submit_for_assemblyai(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(mock_session_repo, mock_user_id)
        fake_storage = MagicMock()
        # Both channel blobs are present.
        fake_storage.fetch_metadata.return_value = (1024, "application/octet-stream")

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue") as mock_enqueue,
        ):
            resp = client.post(  # type: ignore[attr-defined]
                f"/api/sessions/{session.id}/upload-audio/finalize",
            )

        assert resp.status_code == 202, resp.text
        assert resp.json()["provider"] == "assemblyai"
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args.args[1] == "/api/internal/assemblyai-submit"

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        assert updated.status == SessionStatus.TRANSCRIBING
        assert updated.audio_gcs_path == (
            f"signed/{session.id}/therapist.pcm,signed/{session.id}/client.pcm"
        )
        assert updated.transcription_job_metadata == {
            "provider": "assemblyai",
            "state": "submitting",
        }


class TestSegmentedUpload:
    """Mid-session (segmented) upload: opted into via ``segment_index``."""

    def _upload(
        self,
        client: object,
        session_id: str,
        *,
        segment_index: int,
        segment_offset_seconds: float,
        final: bool = False,
        body: bytes = b"\x00" * 1024,
    ) -> object:
        return client.post(  # type: ignore[attr-defined]
            f"/api/sessions/{session_id}/upload-audio",
            files={
                "therapist_audio": ("t.pcm", body, "application/octet-stream"),
                "client_audio": ("c.pcm", body, "application/octet-stream"),
            },
            data={
                "segment_index": str(segment_index),
                "segment_offset_seconds": str(segment_offset_seconds),
                "final": "true" if final else "false",
            },
        )

    def test_first_segment_on_in_progress_session_is_accepted(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        fake_storage = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue") as mock_enqueue,
        ):
            resp = self._upload(client, session.id, segment_index=0, segment_offset_seconds=0)

        assert resp.status_code == 202, resp.text  # type: ignore[attr-defined]

        streamed_objects = {
            call.kwargs["object_name"] for call in fake_storage.upload_stream.call_args_list
        }
        assert streamed_objects == {
            f"audio/{session.id}/therapist.seg000.pcm",
            f"audio/{session.id}/client.seg000.pcm",
        }

        assert mock_enqueue.call_args.args[1] == "/api/internal/assemblyai-submit"
        assert mock_enqueue.call_args.args[2] == {
            "session_id": session.id,
            "user_id": mock_user_id,
            "segment_index": 0,
        }
        assert mock_enqueue.call_args.kwargs["dedup_key"] == f"aai-submit-{session.id}-0"

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        assert updated.status == SessionStatus.TRANSCRIBING
        assert updated.transcription_job_metadata == {
            "provider": "assemblyai",
            "final": False,
            "segments": [
                {
                    "index": 0,
                    "offset_seconds": 0.0,
                    "therapist": f"audio/{session.id}/therapist.seg000.pcm",
                    "client": f"audio/{session.id}/client.seg000.pcm",
                }
            ],
        }

    def test_second_segment_appends_and_enqueues_with_its_own_dedup_key(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        fake_storage = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue") as mock_enqueue,
        ):
            self._upload(client, session.id, segment_index=0, segment_offset_seconds=0)
            resp = self._upload(
                client, session.id, segment_index=1, segment_offset_seconds=300, final=True
            )

        assert resp.status_code == 202, resp.text  # type: ignore[attr-defined]
        assert mock_enqueue.call_count == 2
        assert mock_enqueue.call_args.args[2] == {
            "session_id": session.id,
            "user_id": mock_user_id,
            "segment_index": 1,
        }
        assert mock_enqueue.call_args.kwargs["dedup_key"] == f"aai-submit-{session.id}-1"

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        metadata = updated.transcription_job_metadata
        assert metadata["final"] is True
        assert [s["index"] for s in metadata["segments"]] == [0, 1]
        assert metadata["segments"][1]["offset_seconds"] == 300.0

    def test_reuploading_a_segment_index_replaces_its_entry(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        fake_storage = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue"),
        ):
            self._upload(client, session.id, segment_index=0, segment_offset_seconds=0)
            self._upload(client, session.id, segment_index=0, segment_offset_seconds=5)

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        segments = updated.transcription_job_metadata["segments"]
        assert len(segments) == 1
        assert segments[0]["offset_seconds"] == 5.0

    def test_final_is_sticky_once_set(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        fake_storage = MagicMock()

        with (
            patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()),
            patch(
                "app.services.file_storage.file_storage_from_settings",
                return_value=fake_storage,
            ),
            patch("app.routes.sessions.enqueue"),
        ):
            self._upload(client, session.id, segment_index=0, segment_offset_seconds=0, final=True)
            self._upload(client, session.id, segment_index=1, segment_offset_seconds=300)

        updated = mock_session_repo.get(session.id, mock_user_id)  # type: ignore[attr-defined]
        assert updated.transcription_job_metadata["final"] is True

    def test_segment_fields_with_whisper_provider_are_rejected(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )

        with patch(
            "app.routes.sessions.get_settings",
            return_value=_assemblyai_settings(transcription_provider="whisper"),
        ):
            resp = self._upload(client, session.id, segment_index=0, segment_offset_seconds=0)

        assert resp.status_code == 400, resp.text  # type: ignore[attr-defined]
        assert resp.json()["error"]["code"] == "SEGMENTS_UNSUPPORTED"  # type: ignore[attr-defined]

    def test_segment_index_without_offset_is_unprocessable(
        self, client: object, mock_session_repo: object, mock_user_id: str
    ) -> None:
        session = _seed_recording_complete(
            mock_session_repo, mock_user_id, status=SessionStatus.IN_PROGRESS
        )

        with patch("app.routes.sessions.get_settings", return_value=_assemblyai_settings()):
            resp = client.post(  # type: ignore[attr-defined]
                f"/api/sessions/{session.id}/upload-audio",
                files={
                    "therapist_audio": ("t.pcm", b"\x00" * 1024, "application/octet-stream"),
                    "client_audio": ("c.pcm", b"\x00" * 1024, "application/octet-stream"),
                },
                data={"segment_index": "0"},
            )

        assert resp.status_code == 422, resp.text  # type: ignore[attr-defined]
