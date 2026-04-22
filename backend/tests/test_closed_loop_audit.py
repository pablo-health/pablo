# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for collect_closed_loop_audit — the audit-pipeline contract test."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.jobs import pentest_collectors as collectors


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def targets() -> collectors.Targets:
    return collectors.Targets(
        project_id="test-project",
        backend_url="https://backend.example.com",
    )


@pytest.fixture
def creds() -> SimpleNamespace:
    return SimpleNamespace(
        user_a=SimpleNamespace(
            id_token="fake.jwt.token",  # noqa: S106 — fixture value; attribute name must match real creds shape
            email="pentestuser-1@pablo.health",
        )
    )


def _make_http(responses: list[tuple[int, dict]]):
    calls: list[tuple[str, str]] = []
    iterator = iter(responses)

    def _http(method, url, headers, body=None):
        calls.append((method, url))
        return next(iterator)

    return _http, calls


class TestClosedLoopAuditCollector:
    def test_skipped_without_creds(self, bundle_dir, targets) -> None:
        artifact = collectors.collect_closed_loop_audit(bundle_dir, targets, None)
        assert artifact.status == "skipped"

    def test_skipped_without_backend_url(self, bundle_dir, creds) -> None:
        artifact = collectors.collect_closed_loop_audit(
            bundle_dir, collectors.Targets(project_id="test-project"), creds
        )
        assert artifact.status == "skipped"

    def test_happy_path_no_findings(self, bundle_dir, targets, creds) -> None:
        http, calls = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (
                    200,
                    {
                        "data": [
                            {
                                "action": "self_audit_viewed",
                                "resource_id": "alice",
                            },
                            {
                                "action": "patient_created",
                                "resource_id": "patient-abc",
                            },
                        ],
                        "limit": 20,
                    },
                ),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_closed_loop_audit(bundle_dir, targets, creds)
        assert artifact.status == "ok"
        assert artifact.summary["findings_count"] == "0"
        assert [c[0] for c in calls] == ["POST", "GET", "DELETE"]

    def test_missing_audit_row_is_high(self, bundle_dir, targets, creds) -> None:
        http, _ = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (200, {"data": [], "limit": 20}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_closed_loop_audit(bundle_dir, targets, creds)
        assert artifact.status == "error"
        assert artifact.summary["highest_severity"] == "HIGH"
        assert artifact.summary["findings_count"] == "1"

    def test_audit_endpoint_unreachable_is_high(
        self, bundle_dir, targets, creds
    ) -> None:
        http, _ = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (500, {"error": "boom"}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_closed_loop_audit(bundle_dir, targets, creds)
        assert artifact.status == "error"
        assert artifact.summary["highest_severity"] == "HIGH"

    def test_create_failure_is_medium(self, bundle_dir, targets, creds) -> None:
        # If POST itself fails, downgrade to MEDIUM — could be a validation
        # bug rather than an audit pipeline regression.
        http, _ = _make_http([(403, {"error": "forbidden"})])
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_closed_loop_audit(bundle_dir, targets, creds)
        assert artifact.status == "error"
        assert artifact.summary["highest_severity"] == "MEDIUM"
