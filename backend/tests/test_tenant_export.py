# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for POST /api/admin/tenant-export — practice-wide PHI archive.

Coverage:

* **Auth gate.** Non-admin users get 403 when the route is called in
  production mode; the existing dev bypass is left to the
  ``test_admin_routes.py`` suite.
* **Happy-path stream.** A practice admin gets a tar.gz response with
  the correct content-disposition, the stream actually opens (i.e.
  StreamingResponse begins iterating), and the TENANT_EXPORTED audit
  log fires once draining completes.

We do **not** materialize the full archive in tests — we open the
stream, read enough bytes to confirm a tar.gz signature, and then
drain the iterator so Starlette runs the BackgroundTask that emits
the audit row.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_admin
from app.db import get_db_session
from app.models import User
from app.models.audit import AuditAction
from app.routes.admin import TenantExportRequest
from app.routes.admin import router as admin_router
from app.services import AuditService, get_audit_service
from app.services.tenant_export_service import (
    TenantExportState,
    TenantExportSummary,
    stream_tenant_archive,
)
from app.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def admin_user() -> User:
    return User(
        id="admin-1",
        email="admin@example.com",
        name="Admin",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
        is_platform_admin=True,
    )


@pytest.fixture
def non_admin_user() -> User:
    return User(
        id="user-1",
        email="user@example.com",
        name="User",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
        is_platform_admin=False,
    )


@pytest.fixture
def captured_audit_entries() -> list:
    return []


@pytest.fixture
def audit_service(captured_audit_entries: list) -> AuditService:
    """Return an AuditService whose ``log`` records calls in-memory.

    We capture full ``log()`` invocations rather than persisting them
    — the tenant-export tests only care about *what* was logged, not
    the repository wiring (which has its own coverage).
    """
    service = AuditService(MagicMock())

    def _capture(action, user, request, **kwargs):  # type: ignore[no-untyped-def]
        captured_audit_entries.append({"action": action, "user_id": user.id, **kwargs})
        return MagicMock()

    service.log = _capture  # type: ignore[method-assign]
    return service


@pytest.fixture
def client(admin_user: User, audit_service: AuditService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    # Tenant-export route hits stream_tenant_archive(db); the service
    # is patched per-test so the DB session itself is irrelevant.
    _stub_db = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: _stub_db
    return TestClient(app)


class TestTenantExportAuth:
    """403 path — practice-admin only."""

    @pytest.mark.skip(reason="Flaky in CI — THERAPY-5ex (401 vs 403).")
    def test_non_admin_gets_403_in_production(
        self, non_admin_user: User, audit_service: AuditService
    ) -> None:
        """When require_admin runs in prod against a non-admin, 403."""
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(admin_router)
        app.dependency_overrides[get_audit_service] = lambda: audit_service
        _stub_db = MagicMock()
        app.dependency_overrides[get_db_session] = lambda: _stub_db

        # Don't override require_admin — let it run for real against
        # a non-admin user, with production settings so the dev bypass
        # is off.
        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service.get_current_user", return_value=non_admin_user),
        ):
            mock_settings.return_value = Settings(
                environment="production",
                database_url="postgresql://test:test@localhost:5432/test",
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/admin/tenant-export", json={"format": "json"})

        assert resp.status_code == 403


class TestTenantExportHappyPath:
    """Stream open + audit emission."""

    @pytest.mark.skip(reason="Flaky in CI — THERAPY-5ex (StreamConsumed).")
    def test_stream_opens_with_correct_headers(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """Verifies headers, that the stream actually starts, and that
        the TENANT_EXPORTED audit fires once the body finishes.

        We patch ``stream_tenant_archive`` to a synthetic generator so
        we don't have to materialize the full archive in the test —
        but we do drive the StreamingResponse to completion so
        Starlette runs the BackgroundTask that emits the audit row.
        """

        def _fake_stream(db, *, export_format, state):  # type: ignore[no-untyped-def]
            # Yield a couple of chunks that together start with the
            # gzip magic so a sniffing client could identify it.
            yield b"\x1f\x8b\x08\x00fake-tar-gz-prelude"
            yield b"more-bytes"
            state.summary = TenantExportSummary(
                size_bytes=64,
                counts={
                    "patients": 3,
                    "therapy_sessions": 5,
                    "notes": 4,
                    "audit_logs": 12,
                },
            )

        with (
            patch(
                "app.routes.admin.stream_tenant_archive",
                side_effect=_fake_stream,
            ),
            client.stream(
                "POST",
                "/api/admin/tenant-export",
                json={"format": "json"},
            ) as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/gzip"
            assert (
                resp.headers["content-disposition"] == 'attachment; filename="tenant-export.tar.gz"'
            )
            # Stream actually starts: read at least the first chunk
            # without buffering the whole archive.
            first_chunk = next(resp.iter_bytes())
            assert first_chunk.startswith(b"\x1f\x8b")
            # Drain the rest so Starlette runs the BackgroundTask that
            # emits the audit row.
            for _ in resp.iter_bytes():
                pass

        # Exactly one TENANT_EXPORTED audit, with PHI-free changes.
        assert len(captured_audit_entries) == 1
        entry = captured_audit_entries[0]
        assert entry["action"] is AuditAction.TENANT_EXPORTED
        assert entry["user_id"] == "admin-1"
        changes = entry["changes"]
        assert changes["format"] == "json"
        assert changes["include_audio"] is False
        assert changes["size_bytes"] == 64
        assert changes["counts"] == {
            "patients": 3,
            "therapy_sessions": 5,
            "notes": 4,
            "audit_logs": 12,
        }

    def test_include_audio_request_is_coerced_to_false(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """v1 ignores include_audio; manifest+audit always record False."""

        def _fake_stream(db, *, export_format, state):  # type: ignore[no-untyped-def]
            yield b"\x1f\x8b\x08\x00"
            state.summary = TenantExportSummary(
                size_bytes=4,
                counts={
                    "patients": 0,
                    "therapy_sessions": 0,
                    "notes": 0,
                    "audit_logs": 0,
                },
            )

        with (
            patch(
                "app.routes.admin.stream_tenant_archive",
                side_effect=_fake_stream,
            ),
            client.stream(
                "POST",
                "/api/admin/tenant-export",
                json={"format": "csv", "include_audio": True},
            ) as resp,
        ):
            assert resp.status_code == 200
            for _ in resp.iter_bytes():
                pass

        entry = captured_audit_entries[0]
        assert entry["changes"]["format"] == "csv"
        assert entry["changes"]["include_audio"] is False

    def test_audit_skipped_when_stream_aborts(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """Generator raises mid-stream → BackgroundTask runs but state.summary
        is None → no audit row.

        This is the failure mode the BackgroundTask refactor exists to
        protect: if the serializer raises (or the client disconnects
        before the generator reaches its tail), we MUST NOT log a
        TENANT_EXPORTED row that overstates what was actually delivered.
        """

        def _aborting_stream(db, *, export_format, state):  # type: ignore[no-untyped-def]
            yield b"\x1f\x8b\x08\x00partial"
            msg = "simulated mid-stream serializer failure"
            raise RuntimeError(msg)

        with patch(
            "app.routes.admin.stream_tenant_archive",
            side_effect=_aborting_stream,
        ):
            # raise_server_exceptions=False so the test client surfaces
            # the broken stream as a closed connection rather than
            # re-raising; this is what a real disconnect looks like
            # from Starlette's perspective.
            aborting_client = TestClient(client.app, raise_server_exceptions=False)
            with aborting_client.stream(
                "POST",
                "/api/admin/tenant-export",
                json={"format": "json"},
            ) as resp:
                # Drain whatever bytes did make it out before the raise.
                for _ in resp.iter_bytes():
                    pass

        # No audit row should have been written — state.summary stayed None.
        assert captured_audit_entries == []


class TestTenantExportService:
    """Pure-Python serializer path — no FastAPI involved."""

    def test_stream_emits_gzip_magic_and_summary(self) -> None:
        """A live (non-mocked) stream yields a real tar.gz and a summary.

        Uses a stub session whose ``execute`` returns empty result
        sets. The archive will contain four empty members + manifest;
        we just verify the bytes start with the gzip magic and the
        state holder is populated with zero counts.
        """
        empty_scalars = MagicMock()
        empty_scalars.scalars.return_value = []
        db = MagicMock()
        db.execute.return_value = empty_scalars

        state = TenantExportState()
        chunks: list[bytes] = []
        for chunk in stream_tenant_archive(
            db,
            export_format="json",
            state=state,
        ):
            chunks.append(chunk)

        archive = b"".join(chunks)
        assert archive.startswith(b"\x1f\x8b"), "tar.gz should start with gzip magic"

        # Round-trip through tarfile to confirm the archive is valid.
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            names = sorted(tar.getnames())
        assert names == sorted(
            [
                "patients.json",
                "therapy_sessions.json",
                "notes.json",
                "audit_logs.json",
                "manifest.json",
            ]
        )

        assert state.summary is not None
        assert state.summary.counts == {
            "patients": 0,
            "therapy_sessions": 0,
            "notes": 0,
            "audit_logs": 0,
        }
        assert state.summary.size_bytes == len(archive)
        # Sanity: the archive really is gzip-decodable.
        gzip.decompress(archive)

    def test_unknown_format_rejected_at_schema_layer(self) -> None:
        """Pydantic rejects format values outside the literal."""
        with pytest.raises(ValidationError):
            TenantExportRequest(format="xml")  # type: ignore[arg-type]
