# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for HIPAA attestation document generator."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.jobs import hipaa_attestation
from app.jobs.hipaa_attestation import Evidence


def _evidence(**overrides) -> Evidence:  # type: ignore[no-untyped-def]
    defaults = {
        "generated_at": "2026-04-17T12:00:00Z",
        "project_id": "pablohealth-test",
        "pablo_version": "v1.2.3",
        "audit_row_count": 1234,
        "audit_oldest_ts": "2025-10-01T00:00:00Z",
        "audit_newest_ts": "2026-04-17T11:59:00Z",
        "audit_retention_days": 2555,
        "daily_review_count_30d": 29,
        "monthly_review_count_1y": 6,
        "pentest_count_90d": 12,
        "heartbeat_count_30d": 4,
        "last_daily_review": "hipaa-log-review/daily/2026-04-17T07-00-00Z.md",
        "last_pentest": "pentest/2026-04-14T02-00-00Z.md",
        "last_heartbeat": "heartbeat/2026-04-14T09-00-00Z.txt",
        "compliance_bucket": "pablohealth-test-compliance-reports",
        "bucket_retention_days": 2555,
        "bucket_retention_locked": True,
    }
    defaults.update(overrides)
    return Evidence(**defaults)


class TestRenderMarkdown:
    def test_contains_hipaa_citations(self) -> None:
        md = hipaa_attestation._render_markdown(_evidence())
        for citation in (
            "§ 164.308(a)(1)(ii)(D)",
            "§ 164.308(a)(8)",
            "§ 164.312(b)",
            "§ 164.312(d)",
            "§ 164.312(e)(1)",
            "§ 164.314(a)",
            "§ 164.316(b)(2)(i)",
        ):
            assert citation in md, f"missing citation: {citation}"

    def test_includes_nist_references(self) -> None:
        md = hipaa_attestation._render_markdown(_evidence())
        assert "NIST SP 800-66" in md
        assert "NIST SP 800-53" in md

    def test_includes_audit_stats(self) -> None:
        md = hipaa_attestation._render_markdown(_evidence(audit_row_count=42))
        assert "42" in md

    def test_reports_retention_locked_status(self) -> None:
        md_locked = hipaa_attestation._render_markdown(
            _evidence(bucket_retention_locked=True)
        )
        assert "LOCKED" in md_locked
        md_unlocked = hipaa_attestation._render_markdown(
            _evidence(bucket_retention_locked=False)
        )
        assert "not yet locked" in md_unlocked

    def test_handles_empty_bucket_gracefully(self) -> None:
        md = hipaa_attestation._render_markdown(
            _evidence(
                compliance_bucket=None,
                bucket_retention_days=None,
                bucket_retention_locked=None,
                last_daily_review=None,
                last_pentest=None,
                last_heartbeat=None,
            )
        )
        assert "(none)" in md
        assert "<unconfigured>" in md
        assert "not configured" in md

    def test_retention_flags_hipaa_6_year_floor(self) -> None:
        md = hipaa_attestation._render_markdown(_evidence(audit_retention_days=2555))
        assert "6 years" in md
        assert "2190" in md


class TestBucketInventory:
    def test_counts_blobs_within_window(self) -> None:
        now = datetime.now(UTC)
        blobs = [
            _fake_blob("heartbeat/a.txt", now - timedelta(days=5)),
            _fake_blob("heartbeat/b.txt", now - timedelta(days=20)),
            _fake_blob("heartbeat/c.txt", now - timedelta(days=45)),
        ]
        assert hipaa_attestation._count_since(blobs, now - timedelta(days=30)) == 2

    def test_latest_name_by_time_created(self) -> None:
        now = datetime.now(UTC)
        blobs = [
            _fake_blob("old.txt", now - timedelta(days=30)),
            _fake_blob("newer.txt", now - timedelta(days=2)),
            _fake_blob("newest.txt", now - timedelta(hours=1)),
        ]
        assert hipaa_attestation._latest_name(blobs) == "newest.txt"

    def test_latest_name_empty(self) -> None:
        assert hipaa_attestation._latest_name([]) is None


class TestWriteReport:
    def test_stdout_when_no_bucket(self, capsys) -> None:  # type: ignore[no-untyped-def]
        uri = hipaa_attestation._write_report("# body\n", gcs_bucket=None)
        assert uri.startswith("stdout://")
        assert "# body" in capsys.readouterr().out

    def test_gcs_upload_when_bucket_set(self) -> None:
        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value.blob.return_value = mock_blob
        with patch("google.cloud.storage.Client", return_value=mock_client):
            uri = hipaa_attestation._write_report("body", gcs_bucket="b")
        assert uri.startswith("gs://b/attestations/")
        mock_blob.upload_from_string.assert_called_once_with(
            "body", content_type="text/markdown"
        )


def _fake_blob(name: str, time_created: datetime) -> MagicMock:
    blob = MagicMock()
    blob.name = name
    blob.time_created = time_created
    return blob
