# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Unit tests for the optional compliance hard-purge Cloud Run Job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from app.jobs import hard_purge_cron


def test_run_exits_zero_when_no_stub_writer_registered() -> None:
    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=None,
        ),
        patch("app.jobs.hard_purge_cron.get_engine") as ge,
    ):
        assert hard_purge_cron.run([]) == 0
        ge.assert_not_called()


def test_parse_purge_before_defaults_to_roughly_now_minus_30_days() -> None:
    freeze = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    with patch("app.jobs.hard_purge_cron.datetime") as dt_mod:
        dt_mod.UTC = UTC
        dt_mod.now.return_value = freeze
        dt_mod.timedelta = timedelta
        got = hard_purge_cron._parse_purge_before(None)
    assert got == freeze - timedelta(days=30)


def test_parse_purge_before_iso_string() -> None:
    got = hard_purge_cron._parse_purge_before("2026-02-01T00:00:00Z")
    assert got == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)


def test_run_exits_two_when_stub_writer_unsupported() -> None:
    mock_conn = MagicMock()
    stub_writer = MagicMock()
    stub_writer.is_supported.return_value = False

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = None
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm

    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=stub_writer,
        ),
        patch("app.jobs.hard_purge_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.hard_purge_cron.list_active_practice_registry",
            side_effect=AssertionError("must not list tenants until supported"),
        ),
    ):
        assert hard_purge_cron.run([]) == 2


def test_run_dry_run_exits_zero_when_stub_writer_supported() -> None:
    mock_conn = MagicMock()
    stub_writer = MagicMock()
    stub_writer.is_supported.return_value = True

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = None
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm

    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=stub_writer,
        ),
        patch("app.jobs.hard_purge_cron.get_engine", return_value=mock_engine),
        patch("app.jobs.hard_purge_cron.list_active_practice_registry", return_value=[]),
    ):
        assert hard_purge_cron.run(["--dry-run"]) == 0


# ─── THERAPY-zu4: audio blob cleanup inside the per-patient txn ────────────


def test_audio_objects_for_patient_parses_single_and_stereo_paths() -> None:
    """``audio_gcs_path`` may hold one object name or comma-separated stereo."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        ("2026/05/05/sess-a/abc.wav",),
        ("2026/05/05/sess-b/therapist.wav, 2026/05/05/sess-b/client.wav",),
        (None,),
        ("",),
    ]
    objects = hard_purge_cron._audio_objects_for_patient(mock_conn, "practice", "pt-1")
    assert objects == [
        "2026/05/05/sess-a/abc.wav",
        "2026/05/05/sess-b/therapist.wav",
        "2026/05/05/sess-b/client.wav",
    ]


def test_delete_audio_blobs_noop_when_empty() -> None:
    with patch("app.jobs.hard_purge_cron._resolve_audio_storage") as resolver:
        hard_purge_cron._delete_audio_blobs([])
        resolver.assert_not_called()


def test_delete_audio_blobs_invokes_provider_delete_per_object() -> None:
    storage = MagicMock()
    with patch(
        "app.jobs.hard_purge_cron._resolve_audio_storage",
        return_value=(storage, "audio-bucket"),
    ):
        hard_purge_cron._delete_audio_blobs(["obj-a.wav", "obj-b.wav"])
    assert storage.delete.call_args_list == [
        call(bucket="audio-bucket", object_name="obj-a.wav"),
        call(bucket="audio-bucket", object_name="obj-b.wav"),
    ]


def test_delete_audio_blobs_propagates_errors() -> None:
    """Storage failures must surface so the surrounding txn rolls back.

    (Already-gone objects never raise — provider deletes are idempotent;
    see test_file_storage.py.)
    """
    storage = MagicMock()
    storage.delete.side_effect = RuntimeError("storage timeout")
    with (
        patch(
            "app.jobs.hard_purge_cron._resolve_audio_storage",
            return_value=(storage, "audio-bucket"),
        ),
        pytest.raises(RuntimeError, match="storage timeout"),
    ):
        hard_purge_cron._delete_audio_blobs(["obj.wav"])


# ─── PABLO-1w0: chat-row + document-blob cleanup on hard purge ─────────────


def test_delete_clinical_rows_purges_chat_and_documents_and_patient() -> None:
    """A hard purge must remove chat history and the patient — not just the
    legacy appointments/notes/sessions set (which left chat orphaned)."""
    mock_conn = MagicMock()
    hard_purge_cron._delete_clinical_rows(mock_conn, "practice", "pt-1")
    executed = " | ".join(str(call.args[0]) for call in mock_conn.execute.call_args_list)
    for table in (
        "DELETE FROM appointments",
        "DELETE FROM notes",
        "DELETE FROM therapy_sessions",
        "DELETE FROM ical_client_mappings",
        "DELETE FROM chat_messages",
        "DELETE FROM chat_conversations",
        "DELETE FROM patients",
    ):
        assert table in executed, f"missing {table!r}"


def test_document_objects_for_patient_collects_gcs_paths() -> None:
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        ("docs/pt-1/intake.pdf",),
        ("  docs/pt-1/labs.pdf  ",),
        (None,),
        ("",),
    ]
    objects = hard_purge_cron._document_objects_for_patient(mock_conn, "practice", "pt-1")
    assert objects == ["docs/pt-1/intake.pdf", "docs/pt-1/labs.pdf"]


def test_delete_document_blobs_noop_when_empty() -> None:
    with patch("app.jobs.hard_purge_cron._resolve_documents_storage") as resolver:
        hard_purge_cron._delete_document_blobs([])
        resolver.assert_not_called()


def test_delete_document_blobs_invokes_provider_delete_per_object() -> None:
    storage = MagicMock()
    with patch(
        "app.jobs.hard_purge_cron._resolve_documents_storage",
        return_value=(storage, "docs-bucket"),
    ):
        hard_purge_cron._delete_document_blobs(["docs/a.pdf", "docs/b.pdf"])
    assert storage.delete.call_args_list == [
        call(bucket="docs-bucket", object_name="docs/a.pdf"),
        call(bucket="docs-bucket", object_name="docs/b.pdf"),
    ]


def test_delete_document_blobs_logs_when_bucket_unconfigured() -> None:
    """Documents present but no bucket configured must not silently drop them
    nor raise — it logs loudly and returns (orphan visibility)."""
    with patch("app.jobs.hard_purge_cron._resolve_documents_storage", return_value=None):
        hard_purge_cron._delete_document_blobs(["docs/orphan.pdf"])  # no raise


def test_delete_document_blobs_propagates_errors() -> None:
    storage = MagicMock()
    storage.delete.side_effect = RuntimeError("storage timeout")
    with (
        patch(
            "app.jobs.hard_purge_cron._resolve_documents_storage",
            return_value=(storage, "docs-bucket"),
        ),
        pytest.raises(RuntimeError, match="storage timeout"),
    ):
        hard_purge_cron._delete_document_blobs(["docs/x.pdf"])
