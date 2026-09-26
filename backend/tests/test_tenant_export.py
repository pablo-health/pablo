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
* **Psychotherapy notes are opt-in.** The flag reaches the service, the
  notes query filters restricted rows unless it is set, and the manifest
  and the audit row record the flag and how many shipped. The
  two-clinician proof against real row policy lives in
  ``tests_integration/database/test_tenant_export_restricted_notes.py``.

We do **not** materialize the full archive in tests — we open the
stream, read enough bytes to confirm a tar.gz signature, and then
drain the iterator so Starlette runs the BackgroundTask that emits
the audit row.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user, require_admin
from app.db import get_db_session
from app.db.models import NoteRow
from app.models import User
from app.models.audit import AuditAction
from app.routes.admin import TenantExportRequest
from app.routes.admin import router as admin_router
from app.services import AuditService, get_audit_service
from app.services.tenant_export_service import (
    PSYCHOTHERAPY_NOTES_SCOPE,
    TenantExportState,
    TenantExportSummary,
    stream_tenant_archive,
    visible_counts_payload,
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

    def test_non_admin_gets_403_in_production(
        self, non_admin_user: User, audit_service: AuditService
    ) -> None:
        """When require_admin runs in prod against a non-admin, 403.

        Deterministic version of the previously-flaky case (THERAPY-5ex):
        we override ``get_current_user`` via ``app.dependency_overrides``
        rather than ``mock.patch``, because patches don't reliably flow
        through FastAPI's ``Depends()`` resolution — the dependency
        callable is captured at route registration. ``get_settings`` is
        called as a plain function inside ``require_admin`` (not via
        Depends), so patching the module-level binding is fine.
        """
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(admin_router)
        app.dependency_overrides[get_audit_service] = lambda: audit_service
        app.dependency_overrides[get_current_user] = lambda: non_admin_user
        _stub_db = MagicMock()
        app.dependency_overrides[get_db_session] = lambda: _stub_db

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                environment="production",
                database_url="postgresql://test:test@localhost:5432/test",
            )
            client = TestClient(app)
            resp = client.post("/api/admin/tenant-export", json={"format": "json"})

        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ADMIN_REQUIRED"


class TestTenantExportHappyPath:
    """Stream open + audit emission."""

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

        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
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
            # without buffering the whole archive. Pull one iterator
            # and drain the rest from it — a second call to
            # iter_bytes() on the same response reads the body twice
            # and raises httpx.StreamConsumed.
            body = resp.iter_bytes()
            first_chunk = next(body)
            assert first_chunk.startswith(b"\x1f\x8b")
            # Drain the rest so Starlette runs the BackgroundTask that
            # emits the audit row.
            for _ in body:
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
        assert changes["partial_possible"] is True
        assert changes["counts"] == {
            "patients": {"visible_count": 3, "total_count": None},
            "therapy_sessions": {"visible_count": 5, "total_count": None},
            "notes": {"visible_count": 4, "total_count": None},
            "audit_logs": {"visible_count": 12, "total_count": None},
        }

    def test_include_audio_request_is_coerced_to_false(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """v1 ignores include_audio; manifest+audit always record False."""

        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
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

    def test_psychotherapy_notes_default_off_and_recorded(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """No flag on the wire: the service is told to leave restricted notes
        out, and the audit row says none shipped."""
        seen: dict[str, bool] = {}

        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
            seen["flag"] = include_psychotherapy_notes
            yield b"\x1f\x8b\x08\x00"
            state.summary = TenantExportSummary(size_bytes=4, counts={"notes": 2})

        with (
            patch("app.routes.admin.stream_tenant_archive", side_effect=_fake_stream),
            client.stream("POST", "/api/admin/tenant-export", json={"format": "json"}) as resp,
        ):
            assert resp.status_code == 200
            for _ in resp.iter_bytes():
                pass

        assert seen["flag"] is False
        changes = captured_audit_entries[0]["changes"]
        assert changes["include_psychotherapy_notes"] is False
        assert changes["psychotherapy_notes_included"] == 0

    def test_psychotherapy_notes_flag_is_passed_through_and_audited(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """The audit row carries the flag and the count, nothing about the notes."""
        seen: dict[str, bool] = {}

        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
            seen["flag"] = include_psychotherapy_notes
            yield b"\x1f\x8b\x08\x00"
            state.summary = TenantExportSummary(
                size_bytes=4,
                counts={"notes": 3},
                include_psychotherapy_notes=True,
                psychotherapy_notes_included=1,
            )

        with (
            patch("app.routes.admin.stream_tenant_archive", side_effect=_fake_stream),
            client.stream(
                "POST",
                "/api/admin/tenant-export",
                json={"format": "json", "include_psychotherapy_notes": True},
            ) as resp,
        ):
            assert resp.status_code == 200
            for _ in resp.iter_bytes():
                pass

        assert seen["flag"] is True
        changes = captured_audit_entries[0]["changes"]
        assert changes["include_psychotherapy_notes"] is True
        assert changes["psychotherapy_notes_included"] == 1

    def test_audit_changes_carry_visible_counts_and_partial_flag(
        self, client: TestClient, captured_audit_entries: list
    ) -> None:
        """The audit payload reports what shipped, not a schema total."""

        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
            yield b"\x1f\x8b\x08\x00"
            state.summary = TenantExportSummary(
                size_bytes=4,
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
            for _ in resp.iter_bytes():
                pass

        changes = captured_audit_entries[0]["changes"]
        assert changes["partial_possible"] is True
        assert changes["counts"]["patients"] == {"visible_count": 3, "total_count": None}
        assert changes["counts"]["therapy_sessions"] == {
            "visible_count": 5,
            "total_count": None,
        }
        assert changes["counts"]["notes"] == {"visible_count": 4, "total_count": None}
        assert changes["counts"]["audit_logs"] == {"visible_count": 12, "total_count": None}

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

        def _aborting_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
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
            manifest = json.loads(tar.extractfile("manifest.json").read())
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

        assert manifest["partial_possible"] is True
        assert manifest["counts"]["patients"] == {"visible_count": 0, "total_count": None}
        assert manifest["counts"]["audit_logs"] == {"visible_count": 0, "total_count": None}
        assert manifest["include_psychotherapy_notes"] is False
        assert manifest["psychotherapy_notes_included"] == 0
        assert "psychotherapy_notes_scope" not in manifest

    def test_notes_query_filters_restricted_unless_asked(self) -> None:
        """Only the notes query changes with the flag, and only by that filter."""
        for flag, expect_filter in ((False, True), (True, False)):
            db = _db_returning({})
            _drain(db, include_psychotherapy_notes=flag)
            notes_sql = [
                str(call.args[0])
                for call in db.execute.call_args_list
                if _entity(call.args[0]) is NoteRow
            ]
            assert len(notes_sql) == 1
            assert ("restricted IS false" in notes_sql[0]) is expect_filter

    def test_manifest_counts_the_restricted_notes_that_shipped(self) -> None:
        """With the flag, the manifest records the count and says whose they are."""
        db = _db_returning({NoteRow: [_note(restricted=False), _note(restricted=True)]})
        state = TenantExportState()
        manifest, notes = _drain(db, include_psychotherapy_notes=True, state=state)

        assert [n["restricted"] for n in notes] == [False, True]
        assert manifest["include_psychotherapy_notes"] is True
        assert manifest["psychotherapy_notes_included"] == 1
        assert manifest["psychotherapy_notes_scope"] == PSYCHOTHERAPY_NOTES_SCOPE
        assert manifest["counts"]["notes"] == {"visible_count": 2, "total_count": None}
        assert state.summary is not None
        assert state.summary.include_psychotherapy_notes is True
        assert state.summary.psychotherapy_notes_included == 1

    def test_unknown_format_rejected_at_schema_layer(self) -> None:
        """Pydantic rejects format values outside the literal."""
        with pytest.raises(ValidationError):
            TenantExportRequest(format="xml")  # type: ignore[arg-type]

    def test_visible_counts_payload_reports_no_total(self) -> None:
        """Each table gets its shipped count; the schema-wide total stays null."""
        payload = visible_counts_payload({"patients": 3, "notes": 0})
        assert payload == {
            "patients": {"visible_count": 3, "total_count": None},
            "notes": {"visible_count": 0, "total_count": None},
        }


def _entity(statement: Any) -> Any:
    return statement.column_descriptions[0]["entity"]


def _db_returning(rows_by_model: dict[type, list[Any]]) -> MagicMock:
    """A session stub that answers each table's select with the given rows."""

    def _execute(statement: Any) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value = rows_by_model.get(_entity(statement), [])
        return result

    db = MagicMock()
    db.execute.side_effect = _execute
    return db


def _note(*, restricted: bool) -> NoteRow:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return NoteRow(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        note_type="psychotherapy" if restricted else "narrative",
        status="complete",
        restricted=restricted,
        created_at=now,
        updated_at=now,
    )


def _drain(
    db: MagicMock,
    *,
    include_psychotherapy_notes: bool,
    state: TenantExportState | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the real stream; return its manifest and its notes."""
    archive = b"".join(
        stream_tenant_archive(
            db,
            export_format="json",
            include_psychotherapy_notes=include_psychotherapy_notes,
            state=state,
        )
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        notes = json.loads(tar.extractfile("notes.json").read())
    return manifest, notes
