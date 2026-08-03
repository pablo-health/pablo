# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for collect_cross_tenant_idor — the cross-tenant BOLA probe."""

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
            id_token="fake.jwt.token.a",  # noqa: S106 — fixture value, matches real creds shape
            email="pentestuser-1@pablo.health",
        ),
        user_b=SimpleNamespace(
            id_token="fake.jwt.token.b",  # noqa: S106 — fixture value, matches real creds shape
            email="pentestuser-2@pablo.health",
        ),
    )


def _make_http(responses: list[tuple[int, dict]]):
    calls: list[tuple[str, str]] = []
    iterator = iter(responses)

    def _http(method, url, headers, body=None):
        calls.append((method, url))
        return next(iterator)

    return _http, calls


class TestCrossTenantIdorCollector:
    def test_skipped_without_creds(self, bundle_dir, targets) -> None:
        artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, None)
        assert artifact.status == "skipped"

    def test_skipped_without_backend_url(self, bundle_dir, creds) -> None:
        artifact = collectors.collect_cross_tenant_idor(
            bundle_dir, collectors.Targets(project_id="test-project"), creds
        )
        assert artifact.status == "skipped"

    def test_skipped_without_user_b(self, bundle_dir, targets) -> None:
        creds_a_only = SimpleNamespace(
            user_a=SimpleNamespace(id_token="tok-a", email="a@pablo.health"),  # noqa: S106
            user_b=None,
        )
        artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds_a_only)
        assert artifact.status == "skipped"

    def test_pass_path_both_probes_404(self, bundle_dir, targets, creds) -> None:
        http, calls = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (201, {"id": "session-xyz"}),
                (404, {"error": "not found"}),
                (404, {"error": "not found"}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "ok"
        assert artifact.summary["verdict"] == "PASS"
        assert artifact.summary["by_severity"]["CRITICAL"] == 0
        assert [c[0] for c in calls] == ["POST", "POST", "GET", "GET", "DELETE"]

    def test_fail_path_patient_probe_returns_200(self, bundle_dir, targets, creds) -> None:
        http, _ = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (201, {"id": "session-xyz"}),
                (200, {"id": "patient-abc", "first_name": "IDOR"}),
                (404, {"error": "not found"}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "FAIL"
        assert artifact.summary["by_severity"]["CRITICAL"] == 1
        finding_ids = [f["id"] for f in artifact.summary["findings"]]
        assert "CROSS-TENANT-IDOR-PATIENT" in finding_ids

    def test_fail_path_session_probe_returns_200(self, bundle_dir, targets, creds) -> None:
        http, _ = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (201, {"id": "session-xyz"}),
                (404, {"error": "not found"}),
                (200, {"id": "session-xyz"}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "FAIL"
        finding_ids = [f["id"] for f in artifact.summary["findings"]]
        assert "CROSS-TENANT-IDOR-SESSION" in finding_ids

    def test_error_path_patient_create_fails(self, bundle_dir, targets, creds) -> None:
        http, calls = _make_http([(403, {"error": "forbidden"})])
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "ERROR"
        assert "403" in (artifact.error or "")
        # No patient id was ever minted, so there's nothing to clean up.
        assert [c[0] for c in calls] == ["POST"]

    def test_error_path_session_create_fails_still_cleans_up_patient(
        self, bundle_dir, targets, creds
    ) -> None:
        http, calls = _make_http(
            [
                (201, {"id": "patient-abc"}),
                (500, {"error": "boom"}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "ERROR"
        assert [c[0] for c in calls] == ["POST", "POST", "DELETE"]

    def test_cleanup_runs_on_exception_during_probe(self, bundle_dir, targets, creds) -> None:
        """A raised exception mid-probe (e.g. a network blip on the B GET)
        must not skip the DELETE cleanup of A's patient record."""
        responses = iter(
            [
                (201, {"id": "patient-abc"}),
                (201, {"id": "session-xyz"}),
            ]
        )
        calls: list[tuple[str, str]] = []

        def _http(method, url, headers, body=None):
            calls.append((method, url))
            if method == "GET":
                msg = "connection reset"
                raise ConnectionError(msg)
            return next(responses)

        with patch.object(collectors, "_http_json", side_effect=_http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "ERROR"
        assert "connection reset" in (artifact.error or "")
        assert [c[0] for c in calls] == ["POST", "POST", "GET", "DELETE"]

    def test_cleanup_failure_does_not_mask_probe_result(self, bundle_dir, targets, creds) -> None:
        """If the cleanup DELETE itself fails, the probe's own verdict (here
        a PASS) must still be reported — cleanup is best-effort."""
        responses = iter(
            [
                (201, {"id": "patient-abc"}),
                (201, {"id": "session-xyz"}),
                (404, {"error": "not found"}),
                (404, {"error": "not found"}),
            ]
        )
        calls: list[tuple[str, str]] = []

        def _http(method, url, headers, body=None):
            calls.append((method, url))
            if method == "DELETE":
                msg = "boom"
                raise RuntimeError(msg)
            return next(responses)

        with patch.object(collectors, "_http_json", side_effect=_http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "ok"
        assert artifact.summary["verdict"] == "PASS"
        assert [c[0] for c in calls] == ["POST", "POST", "GET", "GET", "DELETE"]
