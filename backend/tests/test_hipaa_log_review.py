# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the HIPAA log review job — parsing, GCS, notification plumbing.

The Claude/Vertex call itself is not tested here (integration-only); we
assert the orchestration logic around it.
"""

import json
from unittest.mock import MagicMock, patch

from app.jobs import hipaa_log_review


class TestParseSeverity:
    def test_high_wins_over_medium_and_low(self) -> None:
        text = "## Anomalies\n- [HIGH] foo\n- [MEDIUM] bar\n- [LOW] baz"
        assert hipaa_log_review._parse_severity(text) == "HIGH"

    def test_medium_wins_over_low(self) -> None:
        assert hipaa_log_review._parse_severity("MEDIUM finding, LOW finding") == "MEDIUM"

    def test_none_when_no_severity_token(self) -> None:
        assert hipaa_log_review._parse_severity("all clear") == "NONE"


class TestWriteReport:
    def test_stdout_when_no_bucket(self, capsys) -> None:  # type: ignore[no-untyped-def]
        uri = hipaa_log_review._write_report("hello", gcs_bucket=None)
        assert uri.startswith("stdout://")
        assert "hello" in capsys.readouterr().out

    def test_gcs_upload_when_bucket_set(self) -> None:
        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob
        with patch("google.cloud.storage.Client", return_value=mock_client):
            uri = hipaa_log_review._write_report("report body", gcs_bucket="my-bucket")
        assert uri.startswith("gs://my-bucket/hipaa-log-review/")
        mock_blob.upload_from_string.assert_called_once_with(
            "report body", content_type="text/markdown"
        )


class TestNotifyHighFinding:
    def test_logs_structured_error_for_cloud_monitoring(
        self, capsys, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        """Cloud Monitoring's alert policy filters on jsonPayload.alert_type —
        logger.extra={} emits textPayload, so we write JSON to stdout directly."""
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
        hipaa_log_review._notify_high_finding("gs://bucket/report.md")
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["severity"] == "ERROR"
        assert payload["alert_type"] == "hipaa_review_high"
        assert payload["report"] == "gs://bucket/report.md"

    def test_webhook_silent_when_unset(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
        with patch("urllib.request.urlopen") as mock_urlopen:
            hipaa_log_review._notify_high_finding("gs://bucket/report.md")
        mock_urlopen.assert_not_called()

    def test_webhook_posts_when_set(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hook")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            hipaa_log_review._notify_high_finding("gs://bucket/report.md")
        req = mock_urlopen.call_args.args[0]
        assert req.full_url == "https://example.com/hook"
        body = req.data.decode()
        assert "HIGH" in body
        assert "gs://bucket/report.md" in body
