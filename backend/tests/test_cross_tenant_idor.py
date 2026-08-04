# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for collect_cross_tenant_idor — the deterministic BOLA probe."""

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
        user_a=SimpleNamespace(id_token="fake.jwt.a", email="pentestuser-a@pablo.health"),  # noqa: S106
        user_b=SimpleNamespace(id_token="fake.jwt.b", email="pentestuser-b@pablo.health"),  # noqa: S106
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

    def test_skipped_without_second_identity(self, bundle_dir, targets) -> None:
        solo = SimpleNamespace(user_a=SimpleNamespace(id_token="tok-a", email="a@pablo.health"))  # noqa: S106
        artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, solo)
        assert artifact.status == "skipped"

    def test_pass_path_when_b_gets_404s(self, bundle_dir, targets, creds) -> None:
        http, calls = _make_http(
            [
                (201, {"id": "patient-a"}),  # POST /api/patients (as A)
                (201, {"id": "session-a"}),  # POST /api/sessions/schedule (as A)
                (404, {}),  # GET /api/patients/<id> (as B)
                (404, {}),  # GET /api/sessions/<id> (as B)
                (204, {}),  # DELETE /api/patients/<id> (cleanup)
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "ok"
        assert artifact.summary["verdict"] == "PASS"
        assert artifact.summary["probed_paths"] == [
            "/api/patients/{patient_id}",
            "/api/sessions/{session_id}",
        ]
        assert [c[0] for c in calls] == ["POST", "POST", "GET", "GET", "DELETE"]

    def test_fail_path_when_b_gets_200(self, bundle_dir, targets, creds) -> None:
        http, _calls = _make_http(
            [
                (201, {"id": "patient-a"}),
                (201, {"id": "session-a"}),
                (200, {"id": "patient-a"}),  # cross-tenant leak
                (404, {}),
                (204, {}),
            ]
        )
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "FAIL"
        assert artifact.summary["by_severity"]["CRITICAL"] == 1
        finding_ids = {f["id"] for f in artifact.summary["findings"]}
        assert "CROSS-TENANT-IDOR-PATIENT" in finding_ids

    def test_error_path_when_create_fails(self, bundle_dir, targets, creds) -> None:
        http, calls = _make_http([(403, {"error": "forbidden"})])
        with patch.object(collectors, "_http_json", side_effect=http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "ERROR"
        assert artifact.error is not None
        # No patient id was ever assigned, so cleanup must not attempt a DELETE.
        assert [c[0] for c in calls] == ["POST"]

    def test_cleanup_runs_on_exception(self, bundle_dir, targets, creds) -> None:
        def _http(method, url, headers, body=None):
            if method == "POST" and "/api/patients" in url:
                return 201, {"id": "patient-a"}
            if method == "POST" and "schedule" in url:
                msg = "synthetic network failure"
                raise RuntimeError(msg)
            if method == "DELETE":
                return 204, {}
            raise AssertionError("unexpected call: " + method + " " + url)

        with patch.object(collectors, "_http_json", side_effect=_http):
            artifact = collectors.collect_cross_tenant_idor(bundle_dir, targets, creds)

        assert artifact.status == "error"
        assert artifact.summary["verdict"] == "ERROR"
        assert "synthetic network failure" in (artifact.error or "")
        assert "DELETE /api/patients/<A-owned-id> (as A, cleanup) -> 204" in (
            open(bundle_dir / artifact.path).read()  # noqa: PTH123, SIM115 — small test-only read
        )
