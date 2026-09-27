# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice export: one streamed ZIP holding every chart's archive as the
chart's own Export builds it, the practice-wide CSV files, the audit log, and
a manifest whose checksums hold; and the audit rows the route writes once the
stream has finished.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_admin
from app.db import get_db_session
from app.models import Patient, User
from app.models.audit import AuditAction
from app.models.export import (
    ExportOptions,
    ExportPatient,
    PatientExportDocument,
    Practitioner,
)
from app.routes.admin import router as admin_router
from app.routes.patients import get_export_service, get_patient_repository
from app.services import AuditService, get_audit_service
from app.services.practice_export_service import (
    PracticeExportState,
    stream_practice_archive,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

_T0 = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _patient(patient_id: str, first_name: str) -> Patient:
    return Patient(
        id=patient_id, first_name=first_name, last_name="Ash", created_at=_T0, updated_at=_T0
    )


def _document(patient: Patient) -> PatientExportDocument:
    return PatientExportDocument(
        exported_at=_T0,
        options=ExportOptions(),
        patient=ExportPatient(
            identifier=patient.id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            status="active",
            created_at=_T0,
            updated_at=_T0,
        ),
        practitioner=Practitioner(),
        sessions=[],
        standalone_notes=[],
        documents=[],
        appointments=[],
        outcome_measures=[],
        message_threads=[],
        medications=[],
        diagnoses=[],
        charges=[],
        coverage=[],
        claims=[],
    )


def _archive_for(patient: Patient) -> dict[str, Any]:
    """What the chart export returns for ``zip``: a small real ZIP and the document."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("patient.json", json.dumps({"identifier": patient.id}))
    return {
        "content": buffer.getvalue(),
        "content_type": "application/zip",
        "filename": f"patient_{patient.id}_export_2026-03-02.zip",
        "document": _document(patient),
        "documents": [],
        "intake_assignment_ids": [],
        "message_threads": [("thread-" + patient.id, 1)],
        "charge_ids": ["charge-" + patient.id],
        "coverage_ids": [],
        "claim_ids": [],
        "statement": True,
        "superbill": False,
        "balance_cents": 0,
    }


_PATIENTS = [_patient("p-1", "Robin"), _patient("p-2", "Sam")]


def _unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rows(text: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.decode().removeprefix("﻿"))))


class TestStream:
    def test_the_archive_holds_each_chart_the_practice_files_and_a_true_manifest(self) -> None:
        state = PracticeExportState()
        built: list[str] = []

        def build(patient_id: str) -> dict[str, Any]:
            built.append(patient_id)
            return _archive_for(next(p for p in _PATIENTS if p.id == patient_id))

        chunks = list(
            stream_practice_archive(
                patients=_PATIENTS,
                build=build,
                audit_log=lambda: b"id,action\r\n1,patient_exported\r\n",
                options=ExportOptions(include_transcripts=True),
                exported_at=_T0,
                state=state,
            )
        )

        assert len(chunks) > 3, "streamed in pieces, not handed over at the end"
        files = _unzip(b"".join(chunks))
        assert sorted(files) == [
            "appointments.csv",
            "audit_log.csv",
            "clients.csv",
            "manifest.json",
            "patients/p-1/patient_p-1_export_2026-03-02.zip",
            "patients/p-2/patient_p-2_export_2026-03-02.zip",
        ]
        assert built == ["p-1", "p-2"]
        # Each chart's archive is in as the chart export built it, byte for byte.
        for patient in _PATIENTS:
            path = f"patients/{patient.id}/patient_{patient.id}_export_2026-03-02.zip"
            assert files[path] == _archive_for(patient)["content"]
        assert [r["client_id"] for r in _rows(files["clients.csv"])] == ["p-1", "p-2"]
        assert _rows(files["appointments.csv"]) == []
        assert files["audit_log.csv"].startswith(b"id,action")

        manifest = json.loads(files["manifest.json"])
        assert manifest["options"] == {
            "include_transcripts": True,
            "include_psychotherapy_notes": False,
        }
        listed = {entry["path"]: entry for entry in manifest["files"]}
        assert set(listed) == set(files) - {"manifest.json"}
        for path, entry in listed.items():
            assert entry["bytes"] == len(files[path]), path
            assert entry["sha256"] == hashlib.sha256(files[path]).hexdigest(), path
        assert listed["patients/p-1/patient_p-1_export_2026-03-02.zip"]["kind"] == "archive"
        assert listed["clients.csv"]["kind"] == "csv"

        assert state.summary is not None
        assert (state.summary.patients, state.summary.files) == (2, 6)
        assert state.summary.size_bytes == len(b"".join(chunks))
        assert [p.id for p, _ in state.exported] == ["p-1", "p-2"]

    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        def once() -> bytes:
            return b"".join(
                stream_practice_archive(
                    patients=_PATIENTS,
                    build=_archive_for_id,
                    audit_log=lambda: b"",
                    options=ExportOptions(),
                    exported_at=_T0,
                )
            )

        assert once() == once()

    def test_a_practice_with_no_charts_still_has_its_files(self) -> None:
        state = PracticeExportState()
        files = _unzip(
            b"".join(
                stream_practice_archive(
                    patients=[],
                    build=_archive_for_id,
                    audit_log=lambda: b"",
                    options=ExportOptions(),
                    exported_at=_T0,
                    state=state,
                )
            )
        )
        assert sorted(files) == [
            "appointments.csv",
            "audit_log.csv",
            "clients.csv",
            "manifest.json",
        ]
        assert _rows(files["clients.csv"]) == []
        assert state.summary is not None
        assert state.summary.patients == 0

    def test_a_build_that_raises_leaves_no_summary(self) -> None:
        state = PracticeExportState()

        def build(_patient_id: str) -> dict[str, Any]:
            msg = "the chart could not be read"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            list(
                stream_practice_archive(
                    patients=_PATIENTS,
                    build=build,
                    audit_log=lambda: b"",
                    options=ExportOptions(),
                    exported_at=_T0,
                    state=state,
                )
            )
        assert state.summary is None


def _archive_for_id(patient_id: str) -> dict[str, Any]:
    return _archive_for(next(p for p in _PATIENTS if p.id == patient_id))


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def _stub_session() -> MagicMock:
    """The audit log reader is patched, so the session is never read. Named,
    because FastAPI reads an override's signature for sub-dependencies."""
    return MagicMock()


@pytest.fixture
def admin_user() -> User:
    return User(
        id="admin-1",
        email="admin@example.com",
        name="Admin",
        created_at=_T0,
        baa_accepted_at=_T0,
        baa_version="2024-01-01",
        is_platform_admin=True,
    )


@pytest.fixture
def captured() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def audit_service(captured: list[dict[str, Any]]) -> AuditService:
    service = AuditService(MagicMock())

    def _log(action, user, request, **kwargs):  # type: ignore[no-untyped-def]
        captured.append({"action": action, "user_id": user.id, **kwargs})
        return MagicMock()

    def _log_patient_action(action, user, request, patient, changes=None):  # type: ignore[no-untyped-def]
        captured.append(
            {"action": action, "user_id": user.id, "patient_id": patient.id, "changes": changes}
        )
        return MagicMock()

    def _log_patient_message_action(action, user, request, **kwargs):  # type: ignore[no-untyped-def]
        captured.append({"action": action, "user_id": user.id, **kwargs})
        return MagicMock()

    service.log = _log  # type: ignore[method-assign]
    service.log_patient_action = _log_patient_action  # type: ignore[method-assign]
    service.log_patient_message_action = _log_patient_message_action  # type: ignore[method-assign]
    return service


@pytest.fixture
def client(admin_user: User, audit_service: AuditService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: admin_user

    export_service = MagicMock()
    export_service.get_patient_export_data.side_effect = (
        lambda patient_id, _user_id, _fmt, **_options: _archive_for_id(patient_id)
    )
    # Two pages of one, so the route's paging is exercised.
    patients = MagicMock()

    def list_by_user(_user_id: str, *, page: int = 1, page_size: int = 100) -> tuple[list, int]:
        assert page_size == 1, "the route pages by its own page size"
        return [_PATIENTS[page - 1]] if page <= len(_PATIENTS) else [], len(_PATIENTS)

    patients.list_by_user.side_effect = list_by_user

    app.dependency_overrides[get_audit_service] = lambda: audit_service
    app.dependency_overrides[get_db_session] = _stub_session
    app.dependency_overrides[get_export_service] = lambda: export_service
    app.dependency_overrides[get_patient_repository] = lambda: patients
    return TestClient(app)


class TestRoute:
    def test_the_default_is_the_practice_archive_with_a_row_per_chart(
        self, client: TestClient, captured: list[dict[str, Any]]
    ) -> None:
        with (
            patch("app.routes.admin.audit_log_csv", return_value=b"id\r\n"),
            patch("app.routes.admin._EXPORT_PAGE_SIZE", 1),
            client.stream(
                "POST",
                "/api/admin/tenant-export",
                json={"include_psychotherapy_notes": True},
            ) as resp,
        ):
            assert resp.status_code == 200, resp.read()
            assert resp.headers["content-type"] == "application/zip"
            assert (
                resp.headers["content-disposition"] == 'attachment; filename="practice-export.zip"'
            )
            body = b"".join(resp.iter_bytes())

        files = _unzip(body)
        assert {name.split("/")[1] for name in files if name.startswith("patients/")} == {
            "p-1",
            "p-2",
        }
        assert json.loads(files["manifest.json"])["options"] == {
            "include_transcripts": False,
            "include_psychotherapy_notes": True,
        }

        actions = [entry["action"] for entry in captured]
        assert actions[0] is AuditAction.TENANT_EXPORTED
        practice = captured[0]["changes"]
        assert practice["format"] == "zip"
        assert practice["patients"] == 2
        assert practice["include_psychotherapy_notes"] is True
        assert practice["size_bytes"] == len(body)
        # Then, per chart, the rows the chart's own export writes.
        exported = [e for e in captured if e["action"] is AuditAction.PATIENT_EXPORTED]
        assert [e["patient_id"] for e in exported] == ["p-1", "p-2"]
        assert exported[0]["changes"] == {
            "export_format": "zip",
            "include_transcripts": False,
            "include_psychotherapy_notes": True,
        }
        assert actions.count(AuditAction.PATIENT_MESSAGE_THREAD_EXPORTED) == 2
        assert actions.count(AuditAction.PATIENT_CHARGES_VIEWED) == 2
        assert actions.count(AuditAction.STATEMENT_GENERATED) == 2

    def test_raw_still_streams_the_table_dump(self, client: TestClient) -> None:
        def _fake_stream(db, *, export_format, include_psychotherapy_notes, state):  # type: ignore[no-untyped-def]
            yield b"\x1f\x8b\x08\x00"

        with (
            patch("app.routes.admin.stream_tenant_archive", side_effect=_fake_stream),
            client.stream("POST", "/api/admin/tenant-export", json={"raw": True}) as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/gzip"
