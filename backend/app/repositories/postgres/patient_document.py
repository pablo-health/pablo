# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientDocumentRepository implementation.

Combined access predicate (mirrors the DB-level RLS policy
``rls_patient_doc_access``):

* ``category = 'chart'`` rows visible to anyone with a
  ``patient_clinicians`` grant on the patient (delegated to the
  ``has_patient_access`` SQL function for a single source of truth).
* ``category IN ('therapist_private', 'psychotherapy_notes')`` rows
  visible to the uploader only. The two restricted categories share
  the access predicate; downstream disclosure workflows (release-of-
  records, patient right-of-access) will branch on the specific
  value.

RLS is the defense-in-depth backstop — even if the application-layer
filter is dropped, PostgreSQL will not return rows the caller can't
see. Both layers are kept in lockstep so a regression in one is
caught by the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, or_, text

from ...db.models import PatientClinicianRow, PatientDocumentRow
from ...models import DocumentCategory, PatientDocument
from ..patient_document import FinalizedExtraction, PatientDocumentRepository

_RESTRICTED_CATEGORIES = ("therapist_private", "psychotherapy_notes")

_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


class PostgresPatientDocumentRepository(PatientDocumentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- internal access predicate -----------------------------------

    def _has_patient_access(self, patient_id: str, user_id: str) -> bool:
        """App-layer mirror of has_patient_access(). Used by writes that
        need to check access before issuing a deletion or finalize.
        """
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    # --- writes -------------------------------------------------------

    def add(self, document: PatientDocument) -> PatientDocument:
        row = PatientDocumentRow(
            id=document.id,
            patient_id=document.patient_id,
            user_id=document.user_id,
            filename=document.filename,
            mime_type=document.mime_type,
            gcs_path=document.gcs_path,
            extracted_text=document.extracted_text,
            extracted_via=document.extracted_via,
            extraction_metadata=document.extraction_metadata,
            size_bytes=document.size_bytes,
            category=document.category.value,
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
        extraction: FinalizedExtraction,
        finalized_at: object,
    ) -> PatientDocument | None:
        # Finalize restricted to uploader — see abstract method docstring.
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
        row.extracted_text = extraction.text
        row.extracted_via = extraction.via
        row.extraction_metadata = extraction.metadata
        row.finalized_at = finalized_at  # type: ignore[assignment]
        self._session.flush()
        return _row_to_document(row)

    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        # Delete restricted to uploader — see abstract method docstring.
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

    # --- reads --------------------------------------------------------

    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        """Single-query combined access predicate.

        OR branch 1: ``category = 'chart'`` AND caller has a live
        patient_clinicians grant.
        OR branch 2: ``category`` is one of the restricted values AND
        caller is the uploader.
        """
        row = (
            self._session.query(PatientDocumentRow)
            .outerjoin(
                PatientClinicianRow,
                (PatientClinicianRow.patient_id == PatientDocumentRow.patient_id)
                & (PatientClinicianRow.user_id == user_id),
            )
            .filter(
                PatientDocumentRow.id == document_id,
                PatientDocumentRow.deleted_at.is_(None),
                or_(
                    (PatientDocumentRow.category == "chart")
                    & (PatientClinicianRow.user_id == user_id),
                    PatientDocumentRow.category.in_(_RESTRICTED_CATEGORIES)
                    & (PatientDocumentRow.user_id == user_id),
                ),
            )
            .one_or_none()
        )
        return _row_to_document(row) if row else None

    def get_many(
        self, document_ids: list[str], user_id: str
    ) -> list[PatientDocument]:
        """Bulk variant of :meth:`get` — one query for a set of ids.

        Carries the same combined access predicate (chart rows need a
        live ``patient_clinicians`` grant; restricted rows need uploader
        match), so a co-treater still sees shared chart docs. A naive
        ``user_id = :uid`` filter would silently drop chart documents
        the caller can legitimately read. Inaccessible, missing, and
        deleted ids are simply absent from the result — no existence
        oracle. Order is unspecified; the caller re-sorts.
        """
        if not document_ids:
            return []
        rows = (
            self._session.query(PatientDocumentRow)
            .outerjoin(
                PatientClinicianRow,
                (PatientClinicianRow.patient_id == PatientDocumentRow.patient_id)
                & (PatientClinicianRow.user_id == user_id),
            )
            .filter(
                PatientDocumentRow.id.in_(document_ids),
                PatientDocumentRow.deleted_at.is_(None),
                or_(
                    (PatientDocumentRow.category == "chart")
                    & (PatientClinicianRow.user_id == user_id),
                    PatientDocumentRow.category.in_(_RESTRICTED_CATEGORIES)
                    & (PatientDocumentRow.user_id == user_id),
                ),
            )
            .all()
        )
        return [_row_to_document(row) for row in rows]

    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        rows = (
            self._session.query(PatientDocumentRow)
            .outerjoin(
                PatientClinicianRow,
                (PatientClinicianRow.patient_id == PatientDocumentRow.patient_id)
                & (PatientClinicianRow.user_id == user_id),
            )
            .filter(
                PatientDocumentRow.patient_id == patient_id,
                PatientDocumentRow.deleted_at.is_(None),
                PatientDocumentRow.finalized_at.is_not(None),
                or_(
                    (PatientDocumentRow.category == "chart")
                    & (PatientClinicianRow.user_id == user_id),
                    PatientDocumentRow.category.in_(_RESTRICTED_CATEGORIES)
                    & (PatientDocumentRow.user_id == user_id),
                ),
            )
            .order_by(PatientDocumentRow.created_at.desc())
            .all()
        )
        return [_row_to_document(row) for row in rows]


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
        extracted_via=row.extracted_via,
        extraction_metadata=row.extraction_metadata,
        size_bytes=row.size_bytes,
        category=DocumentCategory(row.category),
        created_at=row.created_at,
        finalized_at=finalized_at,
        deleted_at=deleted_at,
    )
