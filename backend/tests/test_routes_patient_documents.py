# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/patients/{id}/documents + /api/documents
(THERAPY-ak6m.2).

Exercises the five-endpoint surface, audit emission for all four
patient-document event types, soft-delete semantics, and cross-user
isolation (same-tenant RLS proxied by the in-memory repo).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pytest
from app.main import app
from app.models import Patient
from app.models.audit import AuditAction, ResourceType
from app.repositories import (
    InMemoryPatientDocumentRepository,
    InMemoryPatientRepository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes.patient_documents import (
    get_patient_document_repository as docs_route_doc_repo,
)
from app.routes.patient_documents import (
    get_patient_documents_service,
)
from app.routes.patient_documents import (
    get_patient_repository as docs_route_patient_repo,
)
from app.services import AuditService, PatientDocumentsService, get_audit_service
from app.settings import Settings
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type
from google.cloud.exceptions import NotFound
from reportlab.pdfgen import canvas

# ---- fake GCS plumbing (mirrors test_patient_documents_service) ------


class _FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.size: int | None = None
        self.content_type: str | None = None
        self._data: bytes = b""

    def reload(self) -> None:
        if self.size is None:
            raise NotFound("blob not found")

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._data = data
        self.size = len(data)
        self.content_type = content_type

    def download_as_bytes(self) -> bytes:
        return self._data

    def generate_signed_url(self, **kwargs: Any) -> str:
        return f"https://fake.googleusercontent.example/{self.name}?sig=xyz"


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self._blobs: dict[str, _FakeBlob] = {}

    def blob(self, object_name: str) -> _FakeBlob:
        return self._blobs.setdefault(object_name, _FakeBlob(object_name))


class _FakeStorageClient:
    def __init__(self) -> None:
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self._buckets.setdefault(name, _FakeBucket(name))


def _native_text_pdf_bytes() -> bytes:
    """Real PDF with multi-line body so PyMuPDF clears the 100-char gate.

    A single drawString hits the right edge of the page and the
    extracted text gets clipped — short-line layout avoids that.
    """
    lines = [
        "Fixture PDF for ak6m.2 route tests.",
        "PyMuPDF should return all lines.",
        "This needs to be over 100 chars",
        "to clear the scanned-PDF threshold.",
        "Add one more line for headroom.",
    ]
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


# ---- fixtures --------------------------------------------------------


@pytest.fixture
def documents_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        patient_documents_gcs_bucket="pablo-docs-test",
        patient_documents_max_bytes=25 * 1024 * 1024,
        patient_documents_upload_url_ttl_seconds=300,
        patient_documents_download_url_ttl_seconds=300,
    )


@pytest.fixture
def doc_repo() -> InMemoryPatientDocumentRepository:
    # Default grant for the patient + the test_user_id that conftest's
    # `client` fixture authenticates as. The grant model mirrors
    # patient_clinicians; tests that exercise cross-user access can
    # call grant_access for other users.
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access("patient-1", "test-user-123")
    return repo


@pytest.fixture
def fake_gcs() -> _FakeStorageClient:
    return _FakeStorageClient()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def documents_client(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    doc_repo: InMemoryPatientDocumentRepository,
    documents_settings: Settings,
    fake_gcs: _FakeStorageClient,
    audit_repo: InMemoryAuditRepository,
) -> TestClient:
    # Patient under test
    patient = Patient(
        id="patient-1",
        first_name="Test",
        last_name="Patient",
        email=None,
        phone=None,
        status="active",
        date_of_birth=None,
        diagnosis=None,
        session_count=0,
        last_session_date=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_repo.create(patient, mock_user_id)

    service = PatientDocumentsService(
        repo=doc_repo,
        settings=documents_settings,
        storage_client_factory=lambda: fake_gcs,
        tenant_id="tenant-A",
    )
    app.dependency_overrides[docs_route_doc_repo] = lambda: doc_repo
    app.dependency_overrides[docs_route_patient_repo] = lambda: mock_repo
    app.dependency_overrides[get_patient_documents_service] = lambda: service
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    return client


def _put_blob(
    fake_gcs: _FakeStorageClient,
    bucket: str,
    object_name: str,
    data: bytes,
    content_type: str,
) -> None:
    fake_gcs.bucket(bucket).blob(object_name).upload_from_string(data, content_type=content_type)


def _init_upload(
    client: TestClient,
    patient_id: str,
    *,
    filename: str = "report.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 2048,
) -> dict[str, Any]:
    response = client.post(
        f"/api/patients/{patient_id}/documents/init",
        json={
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ---- init endpoint ---------------------------------------------------


class TestInit:
    def test_returns_signed_url_and_document_id(self, documents_client: TestClient) -> None:
        body = _init_upload(documents_client, "patient-1")
        assert body["document_id"]
        assert body["upload_url"].startswith("https://fake.googleusercontent.example/")
        assert body["required_content_type"] == "application/pdf"
        assert body["max_bytes"] == 25 * 1024 * 1024
        assert body["required_size_header"] == "x-goog-content-length-range"

    def test_rejects_unsupported_mime(self, documents_client: TestClient) -> None:
        response = documents_client.post(
            "/api/patients/patient-1/documents/init",
            json={
                "filename": "evil.exe",
                "mime_type": "application/x-msdownload",
                "size_bytes": 100,
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"

    def test_rejects_oversized(self, documents_client: TestClient) -> None:
        response = documents_client.post(
            "/api/patients/patient-1/documents/init",
            json={
                "filename": "big.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 26 * 1024 * 1024,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_rejects_unknown_patient(self, documents_client: TestClient) -> None:
        response = documents_client.post(
            "/api/patients/no-such-patient/documents/init",
            json={
                "filename": "x.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 100,
            },
        )
        assert response.status_code == 404


# ---- finalize endpoint -----------------------------------------------


class TestFinalize:
    def test_marks_finalized_and_extracts_text(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        response = documents_client.post(f"/api/documents/{init['document_id']}/finalize")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["finalized_at"] is not None
        assert body["text_extraction_failed"] is False
        assert body["size_bytes"] > 0

    def test_returns_400_when_blob_missing(self, documents_client: TestClient) -> None:
        init = _init_upload(documents_client, "patient-1")
        response = documents_client.post(f"/api/documents/{init['document_id']}/finalize")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UPLOAD_NOT_COMPLETE"


# ---- list ------------------------------------------------------------


class TestList:
    def test_excludes_pre_finalize_rows(self, documents_client: TestClient) -> None:
        _init_upload(documents_client, "patient-1")  # never finalized
        response = documents_client.get("/api/patients/patient-1/documents")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_returns_finalized_rows(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1", filename="visible.pdf")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        response = documents_client.get("/api/patients/patient-1/documents")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["data"][0]["filename"] == "visible.pdf"


# ---- get + download + delete -----------------------------------------


class TestDocumentSurface:
    def test_get_returns_extracted_text(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        response = documents_client.get(f"/api/documents/{init['document_id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["extracted_text"] is not None
        assert "Fixture PDF for ak6m.2" in body["extracted_text"]

    def test_download_302s_to_signed_url(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        response = documents_client.get(
            f"/api/documents/{init['document_id']}/file",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith("https://fake.googleusercontent.example/")

    def test_soft_delete_removes_row_from_list_and_get(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        delete = documents_client.delete(f"/api/documents/{init['document_id']}")
        assert delete.status_code == 200

        # list excludes it
        list_response = documents_client.get("/api/patients/patient-1/documents")
        assert list_response.json()["total"] == 0

        # get returns 404
        get_response = documents_client.get(f"/api/documents/{init['document_id']}")
        assert get_response.status_code == 404


# ---- audit emission --------------------------------------------------


class TestAuditEmission:
    def test_four_events_fire_with_phi_free_payload(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(
            documents_client,
            "patient-1",
            filename="my-secret-filename.pdf",
            size_bytes=4096,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")
        documents_client.get(f"/api/documents/{init['document_id']}")
        documents_client.get(
            f"/api/documents/{init['document_id']}/file",
            follow_redirects=False,
        )
        documents_client.delete(f"/api/documents/{init['document_id']}")

        actions = [e.action for e in audit_repo._entries]
        assert AuditAction.PATIENT_DOCUMENT_UPLOAD_INITIATED.value in actions
        assert AuditAction.PATIENT_DOCUMENT_UPLOADED.value in actions
        assert AuditAction.PATIENT_DOCUMENT_VIEWED.value in actions
        assert AuditAction.PATIENT_DOCUMENT_DOWNLOADED.value in actions
        assert AuditAction.PATIENT_DOCUMENT_DELETED.value in actions

        for entry in audit_repo._entries:
            if not entry.action.startswith("patient_document_"):
                continue
            assert entry.resource_type == ResourceType.PATIENT_DOCUMENT.value
            assert entry.patient_id == "patient-1"
            # PHI-free: changes must not contain filename or extracted text.
            assert "filename" not in (entry.changes or {})
            assert "extracted_text" not in (entry.changes or {})
            # filename should not appear in any other field either.
            for field in (entry.resource_id, entry.user_id, entry.patient_id or ""):
                assert "my-secret-filename" not in field


# ---- access predicate (patient grants + private flag) ----------------


class TestAccessPredicate:
    """Combined RLS shape: patient_access for non-private rows, uploader-
    only for private rows. The Postgres path enforces this via the
    ``rls_patient_doc_access`` policy created by
    ``enable_rls_on_schema``. At the route layer we mirror the same
    contract via the repository, and the tests below fix both halves
    so a regression in either layer is visible.
    """

    def test_user_without_grant_cannot_see_docs(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        # An unrelated user with no patient_clinicians grant sees nothing.
        assert doc_repo.get(init["document_id"], "user-no-grant") is None
        assert doc_repo.list_for_patient("patient-1", "user-no-grant") == []

    def test_co_treater_with_grant_sees_non_private_doc(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
    ) -> None:
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        # Grant a co-treater — they can see the (non-private) doc.
        doc_repo.grant_access("patient-1", "user-co-treater")
        assert doc_repo.get(init["document_id"], "user-co-treater") is not None

    @pytest.mark.parametrize(
        "restricted_category",
        ["therapist_private", "psychotherapy_notes"],
    )
    def test_restricted_doc_hidden_from_co_treaters_with_grant(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        mock_user_id: str,
        restricted_category: str,
    ) -> None:
        response = documents_client.post(
            "/api/patients/patient-1/documents/init",
            json={
                "filename": "restricted.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 1000,
                "category": restricted_category,
            },
        )
        assert response.status_code == 201, response.text
        init = response.json()
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")

        # Co-treater has a grant but the doc is in a restricted category
        # — invisible regardless of which specific restricted value.
        doc_repo.grant_access("patient-1", "user-co-treater")
        assert doc_repo.get(init["document_id"], "user-co-treater") is None
        # And the route surfaces the category so the UI can pick the
        # right treatment (lock icon, label, etc.).
        get_response = documents_client.get(f"/api/documents/{init['document_id']}")
        assert get_response.status_code == 200
        assert get_response.json()["category"] == restricted_category

    def test_psychotherapy_notes_read_emits_restricted_audit_action(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        """Reads of psychotherapy_notes emit the *_RESTRICTED audit action.

        Compliance dashboards filter on action alone for sensitive-
        document access reporting — without having to parse the
        changes payload. The specific category still rides in the
        payload to distinguish therapist_private from psychotherapy_notes.
        """
        response = documents_client.post(
            "/api/patients/patient-1/documents/init",
            json={
                "filename": "notes.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 1000,
                "category": "psychotherapy_notes",
            },
        )
        init = response.json()
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")
        audit_repo.list_for_user(mock_user_id).clear()
        documents_client.get(f"/api/documents/{init['document_id']}")
        documents_client.get(
            f"/api/documents/{init['document_id']}/file",
            follow_redirects=False,
        )

        actions = [entry.action for entry in audit_repo.list_for_user(mock_user_id)]
        assert "patient_document_viewed_restricted" in actions
        assert "patient_document_downloaded_restricted" in actions
        # The category rides on the payload to distinguish
        # therapist_private from psychotherapy_notes within the same
        # restricted action.
        restricted_entries = [
            e
            for e in audit_repo.list_for_user(mock_user_id)
            if e.action.endswith("_restricted")
        ]
        for entry in restricted_entries:
            assert (entry.changes or {}).get("category") == "psychotherapy_notes"

    def test_chart_read_emits_regular_audit_action(
        self,
        documents_client: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        """Reads of chart docs use the regular VIEWED/DOWNLOADED actions
        (no _RESTRICTED suffix) so compliance reporting can cleanly
        separate the volumes."""
        init = _init_upload(documents_client, "patient-1")
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            doc_repo.get(init["document_id"], mock_user_id).gcs_path,  # type: ignore[union-attr]
            _native_text_pdf_bytes(),
            "application/pdf",
        )
        documents_client.post(f"/api/documents/{init['document_id']}/finalize")
        audit_repo.list_for_user(mock_user_id).clear()
        documents_client.get(f"/api/documents/{init['document_id']}")

        actions = [entry.action for entry in audit_repo.list_for_user(mock_user_id)]
        assert "patient_document_viewed" in actions
        assert "patient_document_viewed_restricted" not in actions
