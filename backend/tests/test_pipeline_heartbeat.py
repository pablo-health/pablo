# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the weekly compliance pipeline heartbeat."""

import logging
from unittest.mock import MagicMock, patch

from app.jobs import pipeline_heartbeat


class TestHeartbeat:
    def test_emits_structured_error_log(
        self, caplog, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.delenv("COMPLIANCE_REPORT_BUCKET", raising=False)
        with caplog.at_level(logging.ERROR, logger="pipeline_heartbeat"):
            rc = pipeline_heartbeat.run()
        assert rc == 0
        err_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert err_records, "heartbeat must emit ERROR-level log so alert fires"
        assert getattr(err_records[0], "alert_type", None) == "pipeline_heartbeat"
        assert getattr(err_records[0], "timestamp", None)

    def test_writes_gcs_marker_when_bucket_set(
        self, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.setenv("COMPLIANCE_REPORT_BUCKET", "my-compliance")
        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob
        with patch("google.cloud.storage.Client", return_value=mock_client):
            rc = pipeline_heartbeat.run()
        assert rc == 0
        mock_blob.upload_from_string.assert_called_once()
        args, kwargs = mock_blob.upload_from_string.call_args
        assert "heartbeat" in args[0]
        assert kwargs.get("content_type") == "text/plain"

    def test_exits_zero_even_if_gcs_upload_fails(
        self, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        """GCS flakiness must not mark the pipeline as broken — the
        structured log emission is the primary signal."""
        monkeypatch.setenv("COMPLIANCE_REPORT_BUCKET", "my-compliance")
        mock_client = MagicMock()
        mock_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = (
            RuntimeError("simulated gcs outage")
        )
        with patch("google.cloud.storage.Client", return_value=mock_client):
            rc = pipeline_heartbeat.run()
        assert rc == 0
