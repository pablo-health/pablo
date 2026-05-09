# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the dormant ComplianceDocument repository (Phase 3 vault stub)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.db.models import ComplianceDocumentRow
from app.repositories.postgres.compliance_document import (
    ComplianceDocument,
    PostgresComplianceDocumentRepository,
)


def _make_doc(
    *,
    doc_id: str | None = None,
    compliance_item_id: str | None = "item-1",
    filename: str = "license.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 1024,
    storage_uri: str = "gs://pablo-vault/license.pdf",
    document_type: str = "license",
    description: str | None = None,
    uploaded_at: datetime | None = None,
    uploaded_by_user_id: str = "user-1",
) -> ComplianceDocument:
    return ComplianceDocument(
        id=doc_id or str(uuid.uuid4()),
        compliance_item_id=compliance_item_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        storage_uri=storage_uri,
        document_type=document_type,
        description=description,
        uploaded_at=uploaded_at or datetime.now(UTC),
        uploaded_by_user_id=uploaded_by_user_id,
    )


class TestComplianceDocumentDataclass:
    """The dataclass is the wire-shape between routes (Phase 3) and the repo."""

    def test_round_trips_all_fields(self) -> None:
        uploaded = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
        doc = ComplianceDocument(
            id="doc-1",
            compliance_item_id="item-1",
            filename="malpractice-2026.pdf",
            mime_type="application/pdf",
            size_bytes=42_000,
            storage_uri="gs://pablo-vault/2026/malpractice-2026.pdf",
            document_type="malpractice_insurance",
            description="2026 declarations page",
            uploaded_at=uploaded,
            uploaded_by_user_id="user-7",
        )

        assert doc.id == "doc-1"
        assert doc.compliance_item_id == "item-1"
        assert doc.filename == "malpractice-2026.pdf"
        assert doc.mime_type == "application/pdf"
        assert doc.size_bytes == 42_000
        assert doc.storage_uri == "gs://pablo-vault/2026/malpractice-2026.pdf"
        assert doc.document_type == "malpractice_insurance"
        assert doc.description == "2026 declarations page"
        assert doc.uploaded_at == uploaded
        assert doc.uploaded_by_user_id == "user-7"

    def test_compliance_item_id_and_description_are_optional(self) -> None:
        doc = _make_doc(compliance_item_id=None, description=None)
        assert doc.compliance_item_id is None
        assert doc.description is None


class TestPostgresComplianceDocumentRepository:
    """Repo is a thin stub — verify session calls and row/dataclass mapping."""

    def test_create_adds_row_and_flushes(self) -> None:
        session = MagicMock()
        repo = PostgresComplianceDocumentRepository(session)
        uploaded = datetime(2026, 5, 7, 9, 30, tzinfo=UTC)
        doc = _make_doc(
            doc_id="doc-1",
            compliance_item_id="item-99",
            filename="caqh.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            storage_uri="gs://pablo-vault/caqh.pdf",
            document_type="caqh_attestation",
            description="re-attestation receipt",
            uploaded_at=uploaded,
            uploaded_by_user_id="user-3",
        )

        result = repo.create(doc)

        assert result is doc
        added = session.add.call_args.args[0]
        assert isinstance(added, ComplianceDocumentRow)
        assert added.id == "doc-1"
        assert added.compliance_item_id == "item-99"
        assert added.filename == "caqh.pdf"
        assert added.mime_type == "application/pdf"
        assert added.size_bytes == 2048
        assert added.storage_uri == "gs://pablo-vault/caqh.pdf"
        assert added.document_type == "caqh_attestation"
        assert added.description == "re-attestation receipt"
        assert added.uploaded_at == uploaded
        assert added.uploaded_by_user_id == "user-3"
        session.flush.assert_called_once()

    def test_get_returns_none_when_row_missing(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        repo = PostgresComplianceDocumentRepository(session)
        assert repo.get("missing") is None

    def test_get_returns_mapped_dataclass_when_row_exists(self) -> None:
        session = MagicMock()
        uploaded = datetime(2026, 5, 7, 9, 30, tzinfo=UTC)
        row = ComplianceDocumentRow(
            id="doc-2",
            compliance_item_id="item-2",
            filename="baa.pdf",
            mime_type="application/pdf",
            size_bytes=3000,
            storage_uri="gs://pablo-vault/baa.pdf",
            document_type="baa",
            description=None,
            uploaded_at=uploaded,
            uploaded_by_user_id="user-2",
        )
        session.get.return_value = row
        repo = PostgresComplianceDocumentRepository(session)

        result = repo.get("doc-2")

        assert result is not None
        assert isinstance(result, ComplianceDocument)
        assert result.id == "doc-2"
        assert result.compliance_item_id == "item-2"
        assert result.filename == "baa.pdf"
        assert result.size_bytes == 3000
        assert result.storage_uri == "gs://pablo-vault/baa.pdf"
        assert result.document_type == "baa"
        assert result.description is None
        assert result.uploaded_at == uploaded
        assert result.uploaded_by_user_id == "user-2"

    def test_list_for_item_filters_by_compliance_item_id(self) -> None:
        session = MagicMock()
        # Build the chained .query().filter().order_by().all() mock.
        rows = [
            ComplianceDocumentRow(
                id="doc-A",
                compliance_item_id="item-1",
                filename="a.pdf",
                mime_type="application/pdf",
                size_bytes=10,
                storage_uri="gs://x/a.pdf",
                document_type="license",
                description=None,
                uploaded_at=datetime(2026, 5, 7, tzinfo=UTC),
                uploaded_by_user_id="user-1",
            ),
        ]
        query = session.query.return_value
        query.filter.return_value.order_by.return_value.all.return_value = rows
        repo = PostgresComplianceDocumentRepository(session)

        result = repo.list_for_item("item-1")

        session.query.assert_called_once_with(ComplianceDocumentRow)
        # Confirm a filter and order_by were applied before all().
        query.filter.assert_called_once()
        query.filter.return_value.order_by.assert_called_once()
        assert len(result) == 1
        assert result[0].id == "doc-A"
        assert result[0].compliance_item_id == "item-1"

    def test_delete_returns_false_when_row_missing(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        repo = PostgresComplianceDocumentRepository(session)

        assert repo.delete("missing") is False
        session.delete.assert_not_called()

    def test_delete_removes_row_and_returns_true(self) -> None:
        session = MagicMock()
        row = ComplianceDocumentRow(
            id="doc-3",
            compliance_item_id="item-3",
            filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            storage_uri="gs://x/x.pdf",
            document_type="license",
            description=None,
            uploaded_at=datetime(2026, 5, 7, tzinfo=UTC),
            uploaded_by_user_id="user-1",
        )
        session.get.return_value = row
        repo = PostgresComplianceDocumentRepository(session)

        assert repo.delete("doc-3") is True
        session.delete.assert_called_once_with(row)
        session.flush.assert_called_once()
