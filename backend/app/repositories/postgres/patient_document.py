# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientDocumentRepository implementation.

Every query is scoped by ``user_id``. The DB-level RLS policy
(``rls_user_isolation`` on ``patient_documents``) is the defense-in-
depth backstop: even if a bug here drops the application-layer filter,
PostgreSQL will not return rows for a different ``user_id``. Both
layers are kept in lockstep so a regression on one is caught by the
other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import PatientDocumentRow
from ...models import PatientDocument
from ..patient_document import PatientDocumentRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


class PostgresPatientDocumentRepository(PatientDocumentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, document: PatientDocument) -> PatientDocument:
        row = PatientDocumentRow(
            id=document.id,
            patient_id=document.patient_id,
            user_id=document.user_id,
            filename=document.filename,
            mime_type=document.mime_type,
            gcs_path=document.gcs_path,
            extracted_text=document.extracted_text,
            size_bytes=document.size_bytes,
            created_at=document.created_at,
            finalized_at=document.finalized_at,
            deleted_at=document.deleted_at,
        )
        self._session.add(row)
        self._session.flush()
        return _row_to_document(row)

    def mark_finalized(
        self,
        document_id: str,
        user_id: str,
        *,
        size_bytes: int,
        extracted_text: str | None,
        finalized_at: object,
    ) -> PatientDocument | None:
        row = (
            self._session.query(PatientDocumentRow)
            .filter(
                PatientDocumentRow.id == document_id,
                PatientDocumentRow.user_id == user_id,
                PatientDocumentRow.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if row is None:
            return None
        row.size_bytes = size_bytes
        row.extracted_text = extracted_text
        row.finalized_at = finalized_at  # type: ignore[assignment]
        self._session.flush()
        return _row_to_document(row)

    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        row = (
            self._session.query(PatientDocumentRow)
            .filter(
                PatientDocumentRow.id == document_id,
                PatientDocumentRow.user_id == user_id,
                PatientDocumentRow.deleted_at.is_(None),
            )
            .one_or_none()
        )
        return _row_to_document(row) if row else None

    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        rows = (
            self._session.query(PatientDocumentRow)
            .filter(
                PatientDocumentRow.patient_id == patient_id,
                PatientDocumentRow.user_id == user_id,
                PatientDocumentRow.deleted_at.is_(None),
                PatientDocumentRow.finalized_at.is_not(None),
            )
            .order_by(PatientDocumentRow.created_at.desc())
            .all()
        )
        return [_row_to_document(row) for row in rows]

    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        row = (
            self._session.query(PatientDocumentRow)
            .filter(
                PatientDocumentRow.id == document_id,
                PatientDocumentRow.user_id == user_id,
                PatientDocumentRow.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if row is None:
            return False
        row.deleted_at = deleted_at  # type: ignore[assignment]
        self._session.flush()
        return True


def _row_to_document(row: PatientDocumentRow) -> PatientDocument:
    finalized_at: datetime | None = row.finalized_at
    deleted_at: datetime | None = row.deleted_at
    return PatientDocument(
        id=row.id,
        patient_id=row.patient_id,
        user_id=row.user_id,
        filename=row.filename,
        mime_type=row.mime_type,
        gcs_path=row.gcs_path,
        extracted_text=row.extracted_text,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
        finalized_at=finalized_at,
        deleted_at=deleted_at,
    )
