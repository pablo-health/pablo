# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL compliance-document repository — stub for Phase 3 vault."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...db.models import ComplianceDocumentRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class ComplianceDocument:
    id: str
    compliance_item_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    storage_uri: str
    document_type: str
    description: str | None
    uploaded_at: datetime
    uploaded_by_user_id: str


class PostgresComplianceDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, doc: ComplianceDocument) -> ComplianceDocument:
        row = ComplianceDocumentRow()
        _doc_to_row(doc, row)
        self._session.add(row)
        self._session.flush()
        return doc

    def get(self, doc_id: str) -> ComplianceDocument | None:
        row = self._session.get(ComplianceDocumentRow, doc_id)
        if row is None:
            return None
        return _row_to_doc(row)

    def list_for_item(self, compliance_item_id: str) -> list[ComplianceDocument]:
        rows = (
            self._session.query(ComplianceDocumentRow)
            .filter(ComplianceDocumentRow.compliance_item_id == compliance_item_id)
            .order_by(ComplianceDocumentRow.uploaded_at.desc())
            .all()
        )
        return [_row_to_doc(r) for r in rows]

    def delete(self, doc_id: str) -> bool:
        row = self._session.get(ComplianceDocumentRow, doc_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_doc(row: ComplianceDocumentRow) -> ComplianceDocument:
    return ComplianceDocument(
        id=row.id,
        compliance_item_id=row.compliance_item_id,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        storage_uri=row.storage_uri,
        document_type=row.document_type,
        description=row.description,
        uploaded_at=row.uploaded_at,
        uploaded_by_user_id=row.uploaded_by_user_id,
    )


def _doc_to_row(doc: ComplianceDocument, row: ComplianceDocumentRow) -> None:
    row.id = doc.id
    row.compliance_item_id = doc.compliance_item_id
    row.filename = doc.filename
    row.mime_type = doc.mime_type
    row.size_bytes = doc.size_bytes
    row.storage_uri = doc.storage_uri
    row.document_type = doc.document_type
    row.description = doc.description
    row.uploaded_at = doc.uploaded_at
    row.uploaded_by_user_id = doc.uploaded_by_user_id
