# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for compliance evidence-document routes.

Covers upload, list, download, and delete happy paths, plus key
error cases: wrong owner, storage not configured, oversized file, and
unsupported MIME type. Uses the same in-memory repository pattern as
``test_routes_compliance.py`` — no DB, no real storage bucket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.repositories import get_compliance_document_repository, get_compliance_item_repository
from app.repositories.postgres.compliance_document import ComplianceDocument
from app.repositories.postgres.compliance_item import ComplianceItem
from app.routes.compliance import get_compliance_documents_storage
from app.services.compliance_storage import ComplianceStorageBackend
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class _InMemoryComplianceItemRepository:
    def __init__(self) -> None:
        self._items: dict[str, ComplianceItem] = {}

    def list_by_user(self, user_id: str) -> list[ComplianceItem]:
        return [i for i in self._items.values() if i.user_id == user_id]

    def get(self, item_id: str, user_id: str) -> ComplianceItem | None:
        item = self._items.get(item_id)
        if item is None or item.user_id != user_id:
            return None
        return item

    def create(self, item: ComplianceItem) -> ComplianceItem:
        self._items[item.id] = item
        return item

    def update(self, item: ComplianceItem) -> ComplianceItem:
        self._items[item.id] = item
        return item

    def delete(self, item_id: str, user_id: str) -> bool:
        item = self._items.get(item_id)
        if item is None or item.user_id != user_id:
            return False
        del self._items[item_id]
        return True


class _InMemoryComplianceDocumentRepository:
    def __init__(self) -> None:
        self._docs: dict[str, ComplianceDocument] = {}

    def create(self, doc: ComplianceDocument) -> ComplianceDocument:
        self._docs[doc.id] = doc
        return doc

    def get(self, doc_id: str) -> ComplianceDocument | None:
        return self._docs.get(doc_id)

    def list_for_item(self, compliance_item_id: str) -> list[ComplianceDocument]:
        return sorted(
            (d for d in self._docs.values() if d.compliance_item_id == compliance_item_id),
            key=lambda d: d.uploaded_at,
            reverse=True,
        )

    def delete(self, doc_id: str) -> bool:
        if doc_id not in self._docs:
            return False
        del self._docs[doc_id]
        return True


class _InMemoryStorageBackend(ComplianceStorageBackend):
    """In-memory stand-in for GCS / local-fs storage.

    Extends ComplianceStorageBackend with ``storage_root="mem://"`` (never
    parsed by the real logic) and overrides the three public methods so tests
    never touch the filesystem or a cloud bucket.
    """

    def __init__(self) -> None:
        super().__init__("mem://test")
        self._store: dict[str, bytes] = {}

    def put(self, object_key: str, data: bytes, mime_type: str) -> str:
        uri = f"mem://{object_key}"
        self._store[uri] = data
        return uri

    def get_stream(self, uri: str):  # type: ignore[override]
        data = self._store.get(uri)
        if data is None:
            raise FileNotFoundError(f"not found: {uri}")
        yield data

    def delete(self, uri: str) -> None:
        self._store.pop(uri, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def item_repo() -> _InMemoryComplianceItemRepository:
    return _InMemoryComplianceItemRepository()


@pytest.fixture
def doc_repo() -> _InMemoryComplianceDocumentRepository:
    return _InMemoryComplianceDocumentRepository()


@pytest.fixture
def storage() -> _InMemoryStorageBackend:
    return _InMemoryStorageBackend()


@pytest.fixture
def doc_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        compliance_documents_storage_root="mem://test",
        compliance_documents_max_bytes=5 * 1024 * 1024,
    )


@pytest.fixture
def doc_client(
    client: TestClient,
    item_repo: _InMemoryComplianceItemRepository,
    doc_repo: _InMemoryComplianceDocumentRepository,
    storage: _InMemoryStorageBackend,
    doc_settings: Settings,
) -> Generator[TestClient, None, None]:
    """TestClient with compliance-document deps overridden."""
    app.dependency_overrides[get_compliance_item_repository] = lambda: item_repo
    app.dependency_overrides[get_compliance_document_repository] = lambda: doc_repo
    app.dependency_overrides[get_compliance_documents_storage] = lambda: storage
    app.dependency_overrides[get_settings] = lambda: doc_settings
    yield client
    app.dependency_overrides.pop(get_compliance_item_repository, None)
    app.dependency_overrides.pop(get_compliance_document_repository, None)
    app.dependency_overrides.pop(get_compliance_documents_storage, None)
    app.dependency_overrides.pop(get_settings, None)


def _seed_item(
    repo: _InMemoryComplianceItemRepository,
    user_id: str,
    *,
    item_type: str = "license",
    label: str = "NY LMHC",
) -> ComplianceItem:
    now = datetime.now(UTC)
    item = ComplianceItem(
        id=str(uuid.uuid4()),
        user_id=user_id,
        item_type=item_type,
        label=label,
        due_date=date(2027, 6, 30),
        notes=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    repo.create(item)
    return item


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj\n%%EOF"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class TestUploadDocument:
    def test_upload_happy_path(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        storage: _InMemoryStorageBackend,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)

        response = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("license.pdf", _pdf_bytes(), "application/pdf")},
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["compliance_item_id"] == item.id
        assert body["filename"] == "license.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["document_type"] == "license"
        assert body["size_bytes"] == len(_pdf_bytes())
        assert body["uploaded_by_user_id"] == mock_user_id
        # Row must be in the doc repo.
        stored = doc_repo.get(body["id"])
        assert stored is not None
        # Bytes must be in the fake storage backend.
        assert len(storage._store) == 1

    def test_upload_png_accepted(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal PNG header
        response = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("cert.png", png, "image/png")},
        )
        assert response.status_code == 201, response.text

    def test_upload_returns_404_for_other_users_item(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
    ) -> None:
        other_item = _seed_item(item_repo, "other-user")
        response = doc_client.post(
            f"/api/compliance/{other_item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("x.pdf", _pdf_bytes(), "application/pdf")},
        )
        assert response.status_code == 404

    def test_upload_rejects_unsupported_mime(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        response = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("evil.exe", b"\x4d\x5a", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"

    def test_upload_rejects_oversized_file(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_settings: Settings,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        oversized = b"%PDF " + b"x" * (doc_settings.compliance_documents_max_bytes + 1)
        response = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("big.pdf", oversized, "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_upload_rejects_empty_file(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        response = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "EMPTY_FILE"

    def test_upload_500_when_storage_not_configured(
        self,
        client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        mock_user_id: str,
    ) -> None:
        """Storage root=None → 500 with STORAGE_NOT_CONFIGURED code."""
        unconfigured = ComplianceStorageBackend(None)
        app.dependency_overrides[get_compliance_item_repository] = lambda: item_repo
        app.dependency_overrides[get_compliance_document_repository] = lambda: doc_repo
        app.dependency_overrides[get_compliance_documents_storage] = lambda: unconfigured
        try:
            item = _seed_item(item_repo, mock_user_id)
            response = client.post(
                f"/api/compliance/{item.id}/documents",
                data={"document_type": "license"},
                files={"file": ("x.pdf", _pdf_bytes(), "application/pdf")},
            )
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "STORAGE_NOT_CONFIGURED"
        finally:
            app.dependency_overrides.pop(get_compliance_item_repository, None)
            app.dependency_overrides.pop(get_compliance_document_repository, None)
            app.dependency_overrides.pop(get_compliance_documents_storage, None)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListDocuments:
    def test_list_returns_docs_for_item(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        storage: _InMemoryStorageBackend,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        # Upload two documents via the API so both repos and storage are in sync.
        for i in range(2):
            doc_client.post(
                f"/api/compliance/{item.id}/documents",
                data={"document_type": "license"},
                files={"file": (f"doc{i}.pdf", _pdf_bytes(), "application/pdf")},
            )

        response = doc_client.get(f"/api/compliance/{item.id}/documents")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 2
        assert all(d["compliance_item_id"] == item.id for d in body)

    def test_list_returns_empty_when_no_docs(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        response = doc_client.get(f"/api/compliance/{item.id}/documents")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_returns_404_for_other_users_item(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
    ) -> None:
        other_item = _seed_item(item_repo, "other-user")
        response = doc_client.get(f"/api/compliance/{other_item.id}/documents")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


class TestDownloadDocument:
    def test_download_streams_bytes(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        upload_resp = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("license.pdf", _pdf_bytes(), "application/pdf")},
        )
        doc_id = upload_resp.json()["id"]

        response = doc_client.get(f"/api/compliance/documents/{doc_id}/file")
        assert response.status_code == 200
        assert response.content == _pdf_bytes()
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "license.pdf" in response.headers.get("content-disposition", "")

    def test_download_returns_404_for_missing_doc(
        self,
        doc_client: TestClient,
    ) -> None:
        response = doc_client.get(f"/api/compliance/documents/{uuid.uuid4()}/file")
        assert response.status_code == 404

    def test_download_returns_404_for_other_users_doc(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        storage: _InMemoryStorageBackend,
        mock_user_id: str,
    ) -> None:
        # Seed an item + doc belonging to a different user directly in the repos.
        other_item = _seed_item(item_repo, "other-user")
        uri = storage.put(f"other-user/{other_item.id}/doc-x", _pdf_bytes(), "application/pdf")
        other_doc = ComplianceDocument(
            id=str(uuid.uuid4()),
            compliance_item_id=other_item.id,
            filename="other.pdf",
            mime_type="application/pdf",
            size_bytes=len(_pdf_bytes()),
            storage_uri=uri,
            document_type="license",
            description=None,
            uploaded_at=datetime.now(UTC),
            uploaded_by_user_id="other-user",
        )
        doc_repo.create(other_doc)

        response = doc_client.get(f"/api/compliance/documents/{other_doc.id}/file")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    def test_delete_removes_doc_and_storage(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        storage: _InMemoryStorageBackend,
        mock_user_id: str,
    ) -> None:
        item = _seed_item(item_repo, mock_user_id)
        upload_resp = doc_client.post(
            f"/api/compliance/{item.id}/documents",
            data={"document_type": "license"},
            files={"file": ("license.pdf", _pdf_bytes(), "application/pdf")},
        )
        doc_id = upload_resp.json()["id"]
        assert len(storage._store) == 1

        response = doc_client.delete(f"/api/compliance/documents/{doc_id}")
        assert response.status_code == 204
        assert doc_repo.get(doc_id) is None
        assert len(storage._store) == 0

    def test_delete_returns_404_for_missing_doc(
        self,
        doc_client: TestClient,
    ) -> None:
        response = doc_client.delete(f"/api/compliance/documents/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_delete_returns_404_for_other_users_doc(
        self,
        doc_client: TestClient,
        item_repo: _InMemoryComplianceItemRepository,
        doc_repo: _InMemoryComplianceDocumentRepository,
        storage: _InMemoryStorageBackend,
    ) -> None:
        other_item = _seed_item(item_repo, "other-user")
        uri = storage.put(f"other-user/{other_item.id}/doc-y", _pdf_bytes(), "application/pdf")
        other_doc = ComplianceDocument(
            id=str(uuid.uuid4()),
            compliance_item_id=other_item.id,
            filename="other.pdf",
            mime_type="application/pdf",
            size_bytes=len(_pdf_bytes()),
            storage_uri=uri,
            document_type="license",
            description=None,
            uploaded_at=datetime.now(UTC),
            uploaded_by_user_id="other-user",
        )
        doc_repo.create(other_doc)

        response = doc_client.delete(f"/api/compliance/documents/{other_doc.id}")
        assert response.status_code == 404
        # Doc must still exist in the repo.
        assert doc_repo.get(other_doc.id) is not None
