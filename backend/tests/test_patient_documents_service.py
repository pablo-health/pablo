# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for PatientDocumentsService (THERAPY-ak6m.2).

Exercises mime / size validation, the two-phase signed-URL upload
contract, PyMuPDF text extraction (native-text vs scanned PDF), and
soft-delete semantics. All GCS calls go through an in-memory fake so
the tests never hit a real bucket.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.models import DocumentCategory
from app.repositories import InMemoryPatientDocumentRepository
from app.services.file_storage import GcsFileStorage
from app.services.patient_documents_service import (
    FileTooLargeError,
    PatientDocumentsService,
    UnsupportedMimeTypeError,
    UploadNotCompleteError,
    _extract_pdf_text,
)
from app.settings import Settings
from google.cloud.exceptions import NotFound
from reportlab.pdfgen import canvas

# ---- fake GCS plumbing ------------------------------------------------


class _FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.size: int | None = None
        self.content_type: str | None = None
        self._data: bytes = b""

    def reload(self) -> None:  # GCS Blob API parity
        if self.size is None:
            raise NotFound("blob not found")

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._data = data
        self.size = len(data)
        self.content_type = content_type

    def download_as_bytes(self) -> bytes:
        return self._data

    def delete(self) -> None:
        self._data = b""
        self.size = None

    def generate_signed_url(self, **kwargs: Any) -> str:
        return f"https://fake.googleusercontent.example/{self.name}?sig=xyz"


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self._blobs: dict[str, _FakeBlob] = {}

    def blob(self, object_name: str) -> _FakeBlob:
        return self._blobs.setdefault(object_name, _FakeBlob(object_name))


class _FakeStorageClient:
    """In-memory stand-in for ``google.cloud.storage.Client``."""

    def __init__(self) -> None:
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self._buckets.setdefault(name, _FakeBucket(name))


# ---- fixtures ---------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        patient_documents_gcs_bucket="pablo-docs-test",
        patient_documents_max_bytes=25 * 1024 * 1024,
        patient_documents_upload_url_ttl_seconds=300,
        patient_documents_download_url_ttl_seconds=300,
    )


@pytest.fixture
def repo() -> InMemoryPatientDocumentRepository:
    # Default grant for the common-case (patient-1, user-1) used by
    # most tests. Cross-user / no-grant tests construct their own
    # repo or call grant_access() explicitly.
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access("patient-1", "user-1")
    return repo


@pytest.fixture
def fake_gcs() -> _FakeStorageClient:
    return _FakeStorageClient()


@pytest.fixture
def service(
    repo: InMemoryPatientDocumentRepository,
    settings: Settings,
    fake_gcs: _FakeStorageClient,
) -> PatientDocumentsService:
    return PatientDocumentsService(
        repo=repo,
        settings=settings,
        storage=GcsFileStorage(client_factory=lambda: fake_gcs),
        tenant_id="tenant-A",
    )


def _put_blob(
    fake_gcs: _FakeStorageClient,
    bucket: str,
    object_name: str,
    data: bytes,
    content_type: str,
) -> None:
    fake_gcs.bucket(bucket).blob(object_name).upload_from_string(data, content_type=content_type)


# ---- init validation --------------------------------------------------


class TestInitUploadValidation:
    def test_rejects_unsupported_mime(self, service: PatientDocumentsService) -> None:
        with pytest.raises(UnsupportedMimeTypeError):
            service.init_upload(
                patient_id="patient-1",
                user_id="user-1",
                filename="report.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=1000,
            )

    def test_rejects_executable_mime(self, service: PatientDocumentsService) -> None:
        with pytest.raises(UnsupportedMimeTypeError):
            service.init_upload(
                patient_id="patient-1",
                user_id="user-1",
                filename="evil.exe",
                mime_type="application/x-msdownload",
                size_bytes=1000,
            )

    def test_rejects_oversized(self, service: PatientDocumentsService) -> None:
        with pytest.raises(FileTooLargeError):
            service.init_upload(
                patient_id="patient-1",
                user_id="user-1",
                filename="big.pdf",
                mime_type="application/pdf",
                size_bytes=26 * 1024 * 1024,
            )

    def test_rejects_zero_size(self, service: PatientDocumentsService) -> None:
        with pytest.raises(FileTooLargeError):
            service.init_upload(
                patient_id="patient-1",
                user_id="user-1",
                filename="empty.pdf",
                mime_type="application/pdf",
                size_bytes=0,
            )

    def test_returns_signed_url_and_inserts_placeholder(
        self,
        service: PatientDocumentsService,
        repo: InMemoryPatientDocumentRepository,
    ) -> None:
        result = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="report.pdf",
            mime_type="application/pdf",
            size_bytes=1234,
        )
        assert result.upload_url.startswith("https://fake.googleusercontent.example/")
        # Per-tenant prefix is reflected in the object name.
        assert result.document.gcs_path.startswith("tenant-A/")
        # Placeholder row exists but is pre-finalize.
        assert repo.get(result.document.id, "user-1") is not None
        assert repo.get(result.document.id, "user-1").finalized_at is None  # type: ignore[union-attr]
        # And it doesn't show up in the patient's list yet.
        assert service.list_for_patient("patient-1", "user-1") == []


# ---- finalize ---------------------------------------------------------


def _native_text_pdf() -> bytes:
    """Build a tiny PDF whose extracted text exceeds 100 chars.

    Uses reportlab (already in pyproject) so the fixture is real and
    PyMuPDF gets a parseable document instead of hand-built bytes.
    Lines are short enough that reportlab doesn't clip them at the
    page edge — multiple drawString calls keep the total over the
    100-char scanned-PDF threshold.
    """
    lines = [
        "Fixture PDF for ak6m.2 tests.",
        "The PyMuPDF extractor should",
        "return all of these lines as",
        "text. We need enough text to",
        "clear the 100-char threshold.",
    ]
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def _empty_pdf() -> bytes:
    """A PDF whose extracted text is short — stands in for a scanned PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "x")
    c.save()
    return buf.getvalue()


class TestFinalize:
    def test_extracts_text_from_native_pdf(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="native.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
        )
        pdf = _native_text_pdf()
        _put_blob(fake_gcs, "pablo-docs-test", init.document.gcs_path, pdf, "application/pdf")

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        assert document.finalized_at is not None
        assert document.extracted_text is not None
        assert "Fixture PDF" in document.extracted_text
        assert document.size_bytes == len(pdf)

    def test_extraction_returns_null_for_scanned_pdf(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="scan.pdf",
            mime_type="application/pdf",
            size_bytes=200,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _empty_pdf(),
            "application/pdf",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        assert document.finalized_at is not None
        assert document.extracted_text is None

    def test_image_finalize_skips_extraction(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="photo.png",
            mime_type="image/png",
            size_bytes=100,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            b"\x89PNG\r\n\x1a\n" + b"x" * 20,
            "image/png",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")
        assert document.finalized_at is not None
        assert document.extracted_text is None

    def test_rejects_finalize_when_blob_missing(self, service: PatientDocumentsService) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="never_uploaded.pdf",
            mime_type="application/pdf",
            size_bytes=100,
        )
        with pytest.raises(UploadNotCompleteError):
            service.finalize_upload(document_id=init.document.id, user_id="user-1")

    def test_rejects_finalize_when_blob_too_large(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="bypass.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        oversized = b"x" * (26 * 1024 * 1024)
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            oversized,
            "application/pdf",
        )

        with pytest.raises(FileTooLargeError):
            service.finalize_upload(document_id=init.document.id, user_id="user-1")

    def test_finalize_is_idempotent(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="native.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        first = service.finalize_upload(document_id=init.document.id, user_id="user-1")
        second = service.finalize_upload(document_id=init.document.id, user_id="user-1")
        assert first.id == second.id
        assert first.finalized_at == second.finalized_at


# ---- reads + access control ------------------------------------------


class TestReadsAndAccessControl:
    """Combined access predicate: patient grant + private-flag escape hatch."""

    def test_co_treaters_with_grants_see_each_others_non_private_docs(
        self,
        service: PatientDocumentsService,
        repo: InMemoryPatientDocumentRepository,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        # Both clinicians have patient_clinicians grants on patient-1.
        repo.grant_access("patient-1", "user-A")
        repo.grant_access("patient-1", "user-B")

        a = service.init_upload(
            patient_id="patient-1",
            user_id="user-A",
            filename="A.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs, "pablo-docs-test", a.document.gcs_path, _native_text_pdf(), "application/pdf"
        )
        service.finalize_upload(document_id=a.document.id, user_id="user-A")

        b = service.init_upload(
            patient_id="patient-1",
            user_id="user-B",
            filename="B.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs, "pablo-docs-test", b.document.gcs_path, _native_text_pdf(), "application/pdf"
        )
        service.finalize_upload(document_id=b.document.id, user_id="user-B")

        # Co-treaters see both docs — they share the patient chart.
        a_visible = {d.id for d in service.list_for_patient("patient-1", "user-A")}
        b_visible = {d.id for d in service.list_for_patient("patient-1", "user-B")}
        assert a_visible == {a.document.id, b.document.id}
        assert b_visible == {a.document.id, b.document.id}

    def test_user_without_patient_grant_sees_nothing(
        self,
        service: PatientDocumentsService,
        repo: InMemoryPatientDocumentRepository,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        repo.grant_access("patient-1", "user-A")
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-A",
            filename="A.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        service.finalize_upload(document_id=init.document.id, user_id="user-A")

        # user-B has no grant on patient-1 — nothing visible.
        assert service.get(init.document.id, "user-B") is None
        assert service.list_for_patient("patient-1", "user-B") == []

    @pytest.mark.parametrize(
        "restricted_category",
        [DocumentCategory.THERAPIST_PRIVATE, DocumentCategory.PSYCHOTHERAPY_NOTES],
    )
    def test_restricted_doc_is_uploader_only_even_with_grants(
        self,
        service: PatientDocumentsService,
        repo: InMemoryPatientDocumentRepository,
        fake_gcs: _FakeStorageClient,
        restricted_category: DocumentCategory,
    ) -> None:
        # Both clinicians have patient grants — but the doc is in a
        # restricted category, so only the uploader sees it. Same
        # access behavior for both restricted categories; the
        # downstream divergence is in release-of-records and patient
        # right-of-access, not here.
        repo.grant_access("patient-1", "user-A")
        repo.grant_access("patient-1", "user-B")
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-A",
            filename="restricted.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
            category=restricted_category,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        service.finalize_upload(document_id=init.document.id, user_id="user-A")

        assert service.get(init.document.id, "user-A") is not None
        assert service.get(init.document.id, "user-B") is None
        # Same for list — restricted doc filtered out of co-treater's view.
        assert {d.id for d in service.list_for_patient("patient-1", "user-A")} == {init.document.id}
        assert service.list_for_patient("patient-1", "user-B") == []

    def test_category_persists_on_response_and_namespaces_gcs_path(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="notes.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
            category=DocumentCategory.PSYCHOTHERAPY_NOTES,
        )
        # GCS path is category-namespaced so a bucket-level audit can
        # grep for psychotherapy-notes traffic without a DB join.
        assert "/psychotherapy_notes/" in init.document.gcs_path
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        doc = service.finalize_upload(document_id=init.document.id, user_id="user-1")
        assert doc.category is DocumentCategory.PSYCHOTHERAPY_NOTES


# ---- soft delete ------------------------------------------------------


class TestSoftDelete:
    def test_deleted_row_disappears_from_list_and_get(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        service.finalize_upload(document_id=init.document.id, user_id="user-1")

        deleted = service.soft_delete(init.document.id, "user-1")
        assert deleted is not None
        assert deleted.deleted_at is not None

        assert service.get(init.document.id, "user-1") is None
        assert service.list_for_patient("patient-1", "user-1") == []

    def test_co_treater_cannot_delete_another_clinicians_upload(
        self,
        service: PatientDocumentsService,
        repo: InMemoryPatientDocumentRepository,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        """Read is shared with patient access; destructive ops stay
        with the uploader. A co-treater with a grant can SEE the doc
        but cannot delete it."""
        repo.grant_access("patient-1", "user-A")
        repo.grant_access("patient-1", "user-B")
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-A",
            filename="A.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        service.finalize_upload(document_id=init.document.id, user_id="user-A")

        # user-B can see it...
        assert service.get(init.document.id, "user-B") is not None
        # ...but cannot delete it.
        assert service.soft_delete(init.document.id, "user-B") is None
        # Still visible to both after the failed delete.
        assert service.get(init.document.id, "user-A") is not None
        assert service.get(init.document.id, "user-B") is not None

    def test_double_delete_is_safe(
        self,
        service: PatientDocumentsService,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=1000,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )
        service.finalize_upload(document_id=init.document.id, user_id="user-1")
        assert service.soft_delete(init.document.id, "user-1") is not None
        assert service.soft_delete(init.document.id, "user-1") is None


# ---- helper-level PyMuPDF sanity -------------------------------------


def test_extract_pdf_text_native_returns_body() -> None:
    text = _extract_pdf_text(_native_text_pdf())
    assert text is not None
    assert "Fixture PDF" in text


def test_extract_pdf_text_below_threshold_returns_none() -> None:
    assert _extract_pdf_text(_empty_pdf()) is None


# ---- OCR fallback (THERAPY-ak6m.2.3) ---------------------------------


from dataclasses import dataclass  # noqa: E402

from app.services.document_ai_ocr import OcrResult  # noqa: E402


@dataclass
class _FakeOcrClient:
    """Records calls + returns a scripted result.

    Stands in for ``DocumentAiOcrClient`` so the service tests can
    exercise the fallback wiring without touching Google's SDK.
    """

    is_configured: bool = True
    result: OcrResult | None = None
    raise_on_call: bool = False
    calls: list[bytes] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        self.calls.append(pdf_bytes)
        if self.raise_on_call:
            raise RuntimeError("boom")
        return self.result


def _service_with_ocr(
    repo: InMemoryPatientDocumentRepository,
    settings: Settings,
    fake_gcs: _FakeStorageClient,
    ocr: _FakeOcrClient,
) -> PatientDocumentsService:
    return PatientDocumentsService(
        repo=repo,
        settings=settings,
        storage=GcsFileStorage(client_factory=lambda: fake_gcs),
        tenant_id="tenant-A",
        ocr_client=ocr,  # type: ignore[arg-type]
    )


class TestOcrFallback:
    def test_pymupdf_success_skips_ocr(
        self,
        repo: InMemoryPatientDocumentRepository,
        settings: Settings,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        ocr = _FakeOcrClient()
        service = _service_with_ocr(repo, settings, fake_gcs, ocr)
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="native.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _native_text_pdf(),
            "application/pdf",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        assert document.extracted_via == "pymupdf"
        assert document.extraction_metadata is None
        assert ocr.calls == []  # OCR never invoked when PyMuPDF found text

    def test_ocr_fallback_populates_text_and_metadata(
        self,
        repo: InMemoryPatientDocumentRepository,
        settings: Settings,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        ocr = _FakeOcrClient(
            result=OcrResult(
                text="Patient presents with depressed mood since loss of spouse.",
                page_count=2,
                avg_confidence=0.91,
                low_confidence_pages=[],
                latency_ms=842,
            )
        )
        service = _service_with_ocr(repo, settings, fake_gcs, ocr)
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="scan.pdf",
            mime_type="application/pdf",
            size_bytes=200,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _empty_pdf(),
            "application/pdf",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        assert document.extracted_via == "document_ai"
        assert document.extracted_text is not None
        assert "depressed mood" in document.extracted_text
        assert document.extraction_metadata == {
            "page_count": 2,
            "avg_confidence": 0.91,
            "low_confidence_pages": [],
            "latency_ms": 842,
        }
        assert len(ocr.calls) == 1

    def test_ocr_soft_failure_marks_unavailable(
        self,
        repo: InMemoryPatientDocumentRepository,
        settings: Settings,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        # Client returns None to signal "tried + couldn't" (the real
        # client maps Document AI exceptions to None internally).
        ocr = _FakeOcrClient(result=None)
        service = _service_with_ocr(repo, settings, fake_gcs, ocr)
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="scan.pdf",
            mime_type="application/pdf",
            size_bytes=200,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _empty_pdf(),
            "application/pdf",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        assert document.extracted_via == "unavailable"
        assert document.extracted_text is None
        assert document.finalized_at is not None  # finalize still succeeded

    def test_ocr_skipped_when_client_unconfigured(
        self,
        repo: InMemoryPatientDocumentRepository,
        settings: Settings,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        # is_configured=False mimics a deployment with the env var
        # unset — the service must not call extract at all.
        ocr = _FakeOcrClient(is_configured=False, raise_on_call=True)
        service = _service_with_ocr(repo, settings, fake_gcs, ocr)
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="scan.pdf",
            mime_type="application/pdf",
            size_bytes=200,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            _empty_pdf(),
            "application/pdf",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")

        # No OCR client wired effectively → behaves like the pre-bead path
        assert document.extracted_via is None
        assert document.extracted_text is None
        assert ocr.calls == []

    def test_ocr_not_called_for_images(
        self,
        repo: InMemoryPatientDocumentRepository,
        settings: Settings,
        fake_gcs: _FakeStorageClient,
    ) -> None:
        ocr = _FakeOcrClient(raise_on_call=True)
        service = _service_with_ocr(repo, settings, fake_gcs, ocr)
        init = service.init_upload(
            patient_id="patient-1",
            user_id="user-1",
            filename="photo.png",
            mime_type="image/png",
            size_bytes=100,
        )
        _put_blob(
            fake_gcs,
            "pablo-docs-test",
            init.document.gcs_path,
            b"\x89PNG\r\n\x1a\n" + b"x" * 20,
            "image/png",
        )

        document = service.finalize_upload(document_id=init.document.id, user_id="user-1")
        assert document.extracted_via is None
        assert ocr.calls == []
