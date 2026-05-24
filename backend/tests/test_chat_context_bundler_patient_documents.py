# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the ``patient_documents`` source on the chat context bundler.

Split out from ``test_chat_context_bundler.py`` (THERAPY-ak6m.2.2) because
the patient-documents surface will keep growing — OCR fallback (ak6m.2.3),
summary + structured-fields strategies (ak6m.2.4), and agent-fetch tools
(ak6m.2.5) all add their own test classes that belong with this source,
not the rest of the bundler's plumbing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models import PatientDocument
from app.repositories import InMemoryNotesRepository, InMemoryPatientDocumentRepository
from app.services.chat_context_bundler import (
    PATIENT_DOCUMENTS_LIMIT_MAX,
    SOURCE_KEY_PATIENT_DOCUMENTS,
    InvalidSelectionError,
    assemble_context_bundle,
)

PATIENT_ID = "patient-bundler-1"
USER_ID = "clinician-bundler-1"


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    _repo = InMemoryNotesRepository()
    _repo.grant_all_access()
    return _repo


@pytest.fixture
def patient_documents_repo() -> InMemoryPatientDocumentRepository:
    _repo = InMemoryPatientDocumentRepository()
    _repo.grant_access(PATIENT_ID, USER_ID)
    return _repo


def _make_patient_document(
    *,
    doc_id: str | None = None,
    filename: str = "intake.pdf",
    extracted_text: str | None = "Patient reports chronic insomnia.",
    created_at: datetime | None = None,
    patient_id: str = PATIENT_ID,
    user_id: str = USER_ID,
) -> PatientDocument:
    return PatientDocument(
        id=doc_id or str(uuid.uuid4()),
        patient_id=patient_id,
        user_id=user_id,
        filename=filename,
        mime_type="application/pdf",
        gcs_path=f"tenants/t/{patient_id}/{doc_id or 'doc'}.pdf",
        size_bytes=1234,
        created_at=created_at or datetime.now(UTC),
        extracted_text=extracted_text,
        finalized_at=created_at or datetime.now(UTC),
    )


class TestPatientDocumentsSource:
    def test_emits_section_with_filename_and_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        uploaded = datetime(2026, 4, 12, tzinfo=UTC)
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="prior_psychiatry_records.pdf",
                extracted_text="History: GAD diagnosed 2024, sertraline 100mg.",
                created_at=uploaded,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        assert "UPLOADED PATIENT DOCUMENTS" in bundle.text
        assert "prior_psychiatry_records.pdf" in bundle.text
        assert "uploaded 2026-04-12" in bundle.text
        assert "GAD diagnosed 2024" in bundle.text

    def test_requires_repo_when_selected(
        self,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError, match="patient_documents_repo"):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
            )

    def test_rejects_non_dict_non_true_selection(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: "all"},
            )

    def test_rejects_limit_and_document_ids_together(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError, match="mutually exclusive"):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={
                    SOURCE_KEY_PATIENT_DOCUMENTS: {
                        "limit": 2,
                        "document_ids": ["doc-1"],
                    }
                },
            )

    def test_rejects_non_string_document_ids(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"document_ids": [1, 2]}},
            )

    def test_rejects_limit_outside_bounds(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 0}},
            )
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={
                    SOURCE_KEY_PATIENT_DOCUMENTS: {
                        "limit": PATIENT_DOCUMENTS_LIMIT_MAX + 1
                    }
                },
            )

    def test_limit_caps_to_most_recent(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        for i in range(5):
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=f"doc-{i}",
                    filename=f"file-{i}.pdf",
                    extracted_text=f"contents of doc {i}",
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 2}},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        # InMemoryPatientDocumentRepository returns newest first; limit=2
        # keeps doc-4 and doc-3.
        assert entry["row_count"] == 2
        assert entry["document_ids"] == ["doc-4", "doc-3"]

    def test_document_ids_preserve_order(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 3, 1, tzinfo=UTC)
        for i, did in enumerate(["doc-a", "doc-b", "doc-c"]):
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=did,
                    filename=f"{did}.pdf",
                    extracted_text=f"contents {did}",
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_PATIENT_DOCUMENTS: {"document_ids": ["doc-c", "doc-a"]}
            },
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["document_ids"] == ["doc-c", "doc-a"]
        # Order in rendered text matches the caller-supplied order.
        assert bundle.text.index("doc-c.pdf") < bundle.text.index("doc-a.pdf")

    def test_skips_scanned_pdfs_without_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-text",
                filename="text.pdf",
                extracted_text="native text body",
                created_at=base.replace(day=2),
            )
        )
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-scan",
                filename="scan.pdf",
                extracted_text=None,
                created_at=base.replace(day=1),
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] == 1
        assert entry["document_ids"] == ["doc-text"]
        assert entry["skipped_no_text"] == 1
        assert "scan.pdf" not in bundle.text
        assert "text.pdf" in bundle.text

    def test_manifest_omits_extracted_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        secret = "PRIVILEGED CONSULT NARRATIVE 7f3c"
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="consult.pdf",
                extracted_text=secret,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        # Extracted text shows up in the LLM-facing bundle, never in the
        # PHI-free manifest.
        assert secret in bundle.text
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        for value in entry.values():
            assert not isinstance(value, str) or secret not in value

    def test_user_without_grant_sees_no_documents(
        self,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        repo = InMemoryPatientDocumentRepository()
        # Deliberately omit grant_access — caller has no read on the patient.
        repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="prior.pdf",
                extracted_text="confidential history",
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=repo,
            patient_id=PATIENT_ID,
            user_id="stranger-user",
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] == 0
        assert "confidential history" not in bundle.text

    def test_truncation_drops_rows_before_whole_source(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        body = "x" * 1600  # ~400 tokens per doc with the 4-char heuristic
        ids = []
        for i in range(4):
            did = f"doc-{i}"
            ids.append(did)
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=did,
                    filename=f"{did}.pdf",
                    extracted_text=body,
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 4}},
            token_budget=800,
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] >= 1
        assert entry["row_count"] < 4
        assert entry["rows_dropped"] >= 1
        assert "dropped_document_ids" in entry
        # Newest doc must survive (oldest dropped first).
        assert "doc-3" in entry["document_ids"]

    def test_single_oversized_document_drops_whole_source(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="huge",
                filename="huge.pdf",
                extracted_text="z" * 20_000,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
            token_budget=500,
        )
        dropped_keys = {s["source_key"] for s in bundle.manifest["sources_dropped"]}
        assert SOURCE_KEY_PATIENT_DOCUMENTS in dropped_keys
        assert "huge.pdf" not in bundle.text
