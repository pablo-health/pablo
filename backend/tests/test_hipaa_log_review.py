# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the HIPAA log review job — parsing, GCS, notification plumbing.

The Claude/Vertex call itself is not tested here (integration-only); we
assert the orchestration logic around it.
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.jobs import hipaa_log_review


class TestLogTokenUsage:
    def test_logs_all_token_counts_keyed_by_mode_and_schema(self, caplog) -> None:  # type: ignore[no-untyped-def]
        usage = SimpleNamespace(
            prompt_token_count=1200,
            candidates_token_count=340,
            thoughts_token_count=512,
            total_token_count=2052,
        )
        resp = SimpleNamespace(usage_metadata=usage)
        with caplog.at_level(logging.INFO, logger="hipaa_log_review"):
            hipaa_log_review._log_token_usage(
                resp, review_mode="daily", tenant_schema="practice_abc"
            )
        line = caplog.text
        assert "token_usage" in line
        assert "schema=practice_abc" in line
        assert "mode=daily" in line
        assert "prompt=1200" in line
        assert "candidates=340" in line
        assert "thoughts=512" in line
        assert "total=2052" in line

    def test_no_usage_metadata_is_silent(self, caplog) -> None:  # type: ignore[no-untyped-def]
        resp = SimpleNamespace(usage_metadata=None)
        with caplog.at_level(logging.INFO, logger="hipaa_log_review"):
            hipaa_log_review._log_token_usage(resp, review_mode="daily", tenant_schema=None)
        assert "token_usage" not in caplog.text

    def test_missing_fields_log_as_none(self, caplog) -> None:  # type: ignore[no-untyped-def]
        # The SDK omits thoughts_token_count on non-thinking responses.
        usage = SimpleNamespace(prompt_token_count=10, total_token_count=10)
        resp = SimpleNamespace(usage_metadata=usage)
        with caplog.at_level(logging.INFO, logger="hipaa_log_review"):
            hipaa_log_review._log_token_usage(resp, review_mode="monthly", tenant_schema=None)
        assert "thoughts=None" in caplog.text
        assert "candidates=None" in caplog.text


class TestParseSeverity:
    # -- structured severity line (primary contract) --

    def test_overall_severity_line_wins(self) -> None:
        text = "## Summary\nQuiet day.\n## Anomalies\nNone.\n\nOverall severity: NONE\n"
        assert hipaa_log_review._parse_severity(text) == "NONE"

    def test_overall_severity_line_high(self) -> None:
        text = "## Anomalies\n1. Severity: HIGH — snooping\n\nOverall severity: HIGH\n"
        assert hipaa_log_review._parse_severity(text) == "HIGH"

    def test_overall_severity_line_bold_markdown(self) -> None:
        assert hipaa_log_review._parse_severity("**Overall severity: MEDIUM**") == "MEDIUM"

    def test_overall_severity_line_beats_stray_tokens(self) -> None:
        """The model computes the max itself; the structured line is the contract."""
        text = "## Anomalies\n1. [MEDIUM] odd export\n\nOverall severity: MEDIUM\n"
        assert hipaa_log_review._parse_severity(text) == "MEDIUM"

    # -- regression: negated mentions must not page (the original bug) --

    def test_no_high_findings_phrase_is_not_high(self) -> None:
        """'no HIGH-severity findings were identified' must not parse as HIGH."""
        text = (
            "## Summary\n"
            "No HIGH-severity findings were identified in this window.\n"
            "## Anomalies\n"
            "None identified.\n"
            "## No-op confirmations\n"
            "Checked for off-hours access: nothing unusual.\n"
        )
        assert hipaa_log_review._parse_severity(text) == "NONE"

    def test_negated_mention_inside_anomalies_section(self) -> None:
        text = "## Anomalies\nNo HIGH or MEDIUM severity anomalies were identified.\n"
        assert hipaa_log_review._parse_severity(text) == "NONE"

    def test_summary_prose_tokens_do_not_count(self) -> None:
        """Severity words in Summary/No-op prose must not outrank the anomalies."""
        text = (
            "## Summary\n"
            "We reviewed for HIGH-risk patterns such as bulk exports.\n"
            "## Anomalies\n"
            "1. Severity: LOW — single after-hours login, known user.\n"
        )
        assert hipaa_log_review._parse_severity(text) == "LOW"

    # -- fallback: per-anomaly markers when the structured line is missing --

    def test_fallback_bracket_markers_high_wins(self) -> None:
        text = "## Anomalies\n- [HIGH] foo\n- [MEDIUM] bar\n- [LOW] baz"
        assert hipaa_log_review._parse_severity(text) == "HIGH"

    def test_fallback_severity_label_format(self) -> None:
        text = "## Anomalies\n1. **Severity:** MEDIUM — same-surname access.\n"
        assert hipaa_log_review._parse_severity(text) == "MEDIUM"

    def test_fallback_numbered_item_leading_token(self) -> None:
        text = "## Anomalies\n1. HIGH — novel access to dormant patient.\n"
        assert hipaa_log_review._parse_severity(text) == "HIGH"

    def test_none_when_no_severity_token(self) -> None:
        assert hipaa_log_review._parse_severity("all clear") == "NONE"


class TestAskModelEmptyResponse:
    def test_empty_model_response_raises_instead_of_parsing_none(
        self,
        monkeypatch,  # type: ignore[no-untyped-def]
    ) -> None:
        """A transient empty response must fail the review loudly — a
        placeholder report would parse as NONE and a model outage would
        masquerade as a clean day."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        monkeypatch.setenv("HIPAA_REVIEW_MODEL", "gemini-test")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(text=None)
        with (
            patch("google.genai.Client", return_value=mock_client),
            pytest.raises(RuntimeError, match="empty report"),
        ):
            hipaa_log_review._ask_model({"entries": [{"id": "x"}], "user_aggregates": []})


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

    def test_tenant_schema_partitions_gcs_path(self) -> None:
        """Per-tenant GCS prefix keeps one practice's reports out of another's."""
        mock_client = MagicMock()
        with patch("google.cloud.storage.Client", return_value=mock_client):
            uri = hipaa_log_review._write_report(
                "r", gcs_bucket="b", tenant_schema="practice_abc123"
            )
        assert "/hipaa-log-review/practice_abc123/daily/" in uri
        mock_client.bucket.return_value.blob.assert_called_once()
        blob_path = mock_client.bucket.return_value.blob.call_args.args[0]
        assert blob_path.startswith("hipaa-log-review/practice_abc123/daily/")

    def test_tenant_schema_in_stdout_path_when_no_bucket(self, capsys) -> None:  # type: ignore[no-untyped-def]
        uri = hipaa_log_review._write_report("r", gcs_bucket=None, tenant_schema="practice_xyz")
        assert uri.startswith("stdout://hipaa-log-review/practice_xyz/daily/")
        assert uri.endswith(".md")
        capsys.readouterr()  # drain


class TestNotifyHighFinding:
    def test_logs_structured_error_for_cloud_monitoring(
        self,
        capsys,
        monkeypatch,  # type: ignore[no-untyped-def]
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

    def test_tenant_schema_included_in_stdout_payload(
        self,
        capsys,
        monkeypatch,  # type: ignore[no-untyped-def]
    ) -> None:
        """Alert routing needs the schema name to page the right tenant owner."""
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
        hipaa_log_review._notify_high_finding(
            "gs://bucket/report.md", tenant_schema="practice_abc123"
        )
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["tenant_schema"] == "practice_abc123"
        assert "schema=practice_abc123" in payload["message"]

    def test_tenant_schema_included_in_webhook_body(
        self,
        monkeypatch,  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hook")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            hipaa_log_review._notify_high_finding(
                "gs://bucket/report.md", tenant_schema="practice_xyz"
            )
        body = json.loads(mock_urlopen.call_args.args[0].data.decode())
        assert body["tenant_schema"] == "practice_xyz"
        assert "practice_xyz" in body["text"]


class TestMultiTenantRun:
    """run() must iterate every practice schema, isolating failures."""

    def test_each_tenant_gets_its_own_review(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.delenv("REVIEW_WINDOW_HOURS", raising=False)
        calls: list[str] = []

        def fake_review(
            practice_schema: str, window_hours: int, review_mode: str, gcs_bucket
        ) -> None:
            calls.append(practice_schema)

        with (
            patch.object(hipaa_log_review, "_assert_schema_flag_consistency", return_value=[]),
            patch.object(
                hipaa_log_review,
                "_list_practice_schemas",
                return_value=["practice_a", "practice_b", "practice_c"],
            ),
            patch.object(hipaa_log_review, "_review_tenant", side_effect=fake_review),
        ):
            exit_code = hipaa_log_review.run()

        assert exit_code == 0
        assert calls == ["practice_a", "practice_b", "practice_c"]

    def test_pentest_tenants_excluded_by_default(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """run() must call _list_practice_schemas with include_pentest=False
        so pentest tenants skip the anomaly review path."""
        monkeypatch.delenv("REVIEW_WINDOW_HOURS", raising=False)
        with (
            patch.object(hipaa_log_review, "_assert_schema_flag_consistency", return_value=[]),
            patch.object(hipaa_log_review, "_list_practice_schemas", return_value=[]) as mock_list,
            patch.object(hipaa_log_review, "_review_tenant"),
        ):
            hipaa_log_review.run()
        mock_list.assert_called_once_with(include_pentest=False)

    def test_invariant_violations_fire_high_alert(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Consistency check runs first — divergence → HIGH report even
        if every per-tenant review that follows is clean."""
        monkeypatch.delenv("REVIEW_WINDOW_HOURS", raising=False)
        with (
            patch.object(
                hipaa_log_review,
                "_assert_schema_flag_consistency",
                return_value=["schema=practice_abc matches pentest pattern..."],
            ),
            patch.object(hipaa_log_review, "_notify_invariant_violations") as mock_notify,
            patch.object(hipaa_log_review, "_list_practice_schemas", return_value=[]),
        ):
            hipaa_log_review.run()
        mock_notify.assert_called_once()

    def test_one_tenant_failure_does_not_abort_others(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """HIPAA § 164.308(a)(1)(ii)(D) says review must run; one broken tenant
        must not skip the rest. Exit code reflects partial failure."""
        monkeypatch.delenv("REVIEW_WINDOW_HOURS", raising=False)
        calls: list[str] = []

        def flaky(practice_schema: str, window_hours: int, review_mode: str, gcs_bucket) -> None:
            calls.append(practice_schema)
            if practice_schema == "practice_b":
                msg = "synthetic failure"
                raise RuntimeError(msg)

        with (
            patch.object(hipaa_log_review, "_assert_schema_flag_consistency", return_value=[]),
            patch.object(
                hipaa_log_review,
                "_list_practice_schemas",
                return_value=["practice_a", "practice_b", "practice_c"],
            ),
            patch.object(hipaa_log_review, "_review_tenant", side_effect=flaky),
        ):
            exit_code = hipaa_log_review.run()

        assert exit_code == 1
        assert calls == ["practice_a", "practice_b", "practice_c"]

    def test_high_report_pages_through_review_tenant(self) -> None:
        """The wiring that matters: a HIGH report must reach the notifier."""
        payload = {
            "entries": [{"id": "x"}],
            "user_aggregates": [],
            "summary": {"total_entries": 1, "entries_sent": 1, "needs_model_review": True},
        }
        report = "## Anomalies\n1. Severity: HIGH — snooping\n\nOverall severity: HIGH\n"
        with (
            patch.object(hipaa_log_review, "_load_review_payload", return_value=payload),
            patch.object(hipaa_log_review, "_ask_model", return_value=report),
            patch.object(hipaa_log_review, "_write_report", return_value="stdout://r"),
            patch.object(hipaa_log_review, "_notify_high_finding") as mock_notify,
        ):
            hipaa_log_review._review_tenant(
                practice_schema="practice_x",
                window_hours=24,
                review_mode="daily",
                gcs_bucket=None,
            )
        mock_notify.assert_called_once_with("stdout://r", tenant_schema="practice_x")

    def test_medium_report_does_not_page(self) -> None:
        payload = {
            "entries": [{"id": "x"}],
            "user_aggregates": [],
            "summary": {"total_entries": 1, "entries_sent": 1, "needs_model_review": True},
        }
        report = "## Anomalies\n1. Severity: MEDIUM — same surname\n\nOverall severity: MEDIUM\n"
        with (
            patch.object(hipaa_log_review, "_load_review_payload", return_value=payload),
            patch.object(hipaa_log_review, "_ask_model", return_value=report),
            patch.object(hipaa_log_review, "_write_report", return_value="stdout://r"),
            patch.object(hipaa_log_review, "_notify_high_finding") as mock_notify,
        ):
            hipaa_log_review._review_tenant(
                practice_schema="practice_x",
                window_hours=24,
                review_mode="daily",
                gcs_bucket=None,
            )
        mock_notify.assert_not_called()

    def test_routine_window_skips_model_call(self) -> None:
        """A window the deterministic pass classified routine (needs_model_review
        False) → deterministic clean report, no LLM invocation."""
        routine_payload = {
            "entries": [],
            "user_aggregates": [],
            "summary": {
                "total_entries": 12,
                "entries_sent": 0,
                "distinct_users": 1,
                "distinct_patients": 3,
                "needs_model_review": False,
            },
        }
        with (
            patch.object(hipaa_log_review, "_load_review_payload", return_value=routine_payload),
            patch.object(hipaa_log_review, "_ask_model") as mock_ask,
            patch.object(hipaa_log_review, "_write_report", return_value="stdout://x") as mock_write,
            patch.object(hipaa_log_review, "_notify_high_finding") as mock_notify,
        ):
            hipaa_log_review._review_tenant(
                practice_schema="practice_routine",
                window_hours=24,
                review_mode="daily",
                gcs_bucket=None,
            )

        mock_ask.assert_not_called()
        mock_notify.assert_not_called()
        # The deterministic clean report was still written, and it is honest:
        # severity NONE, no cross-tenant assurance claim.
        written = mock_write.call_args.args[0]
        assert "Overall severity: NONE" in written
        assert "cross-tenant" not in written.lower() or "foreign-actor" in written.lower()
        mock_notify.assert_not_called()
