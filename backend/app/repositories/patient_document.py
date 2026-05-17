# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-document repository (THERAPY-ak6m.2).

RLS-keyed to ``user_id``: every method takes the requesting clinician's
``user_id`` and the Postgres implementation filters writes/reads by it
so the application layer mirrors the DB-level policy created by
``enable_rls_on_schema``. The in-memory variant matches the same
contract for unit tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import PatientDocument


class PatientDocumentRepository(ABC):
    """Abstract base class for patient-document data access.

    Methods follow the prevailing convention:

    * Reads return ``None`` / empty when the row is absent or the
      caller doesn't own it — never raise. This avoids an existence
      oracle.
    * Writes return the persisted row; ``soft_delete`` returns the
      number of rows affected so the service layer can distinguish
      "already gone" from "owned by someone else".
    """

    @abstractmethod
    def add(self, document: PatientDocument) -> PatientDocument:
        """Insert a placeholder row in pre-finalize state."""

    @abstractmethod
    def mark_finalized(
        self,
        document_id: str,
        user_id: str,
        *,
        size_bytes: int,
        extracted_text: str | None,
        finalized_at: object,
    ) -> PatientDocument | None:
        """Stamp ``finalized_at`` + size/extracted_text on a placeholder.

        Returns the updated row, or ``None`` if the document is not
        owned by ``user_id`` or has already been soft-deleted.
        """

    @abstractmethod
    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        """Fetch a document by id, restricted to the uploader.

        Returns ``None`` for deleted rows so the service layer can
        translate "soft-deleted" into the same 404 a missing row
        produces (no existence oracle).
        """

    @abstractmethod
    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        """List the caller's documents for a patient, newest first.

        Filters ``finalized_at IS NOT NULL`` so abandoned init rows
        never show up in the UI.
        """

    @abstractmethod
    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        """Tombstone a document. Returns True if a row was updated."""


_NOT_SET = object()


class InMemoryPatientDocumentRepository(PatientDocumentRepository):
    """In-memory repository for unit tests.

    Stores documents keyed by id; access checks compare ``user_id`` on
    the row to the caller's. Matches the Postgres path's behavior:
    cross-user reads return None, soft-deleted rows are hidden from
    list/get, and finalize is a no-op for rows the caller doesn't own.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, PatientDocument] = {}

    def add(self, document: PatientDocument) -> PatientDocument:
        self._by_id[document.id] = document
        return document

    def mark_finalized(
        self,
        document_id: str,
        user_id: str,
        *,
        size_bytes: int,
        extracted_text: str | None,
        finalized_at: object,
    ) -> PatientDocument | None:
        doc = self._by_id.get(document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None:
            return None
        doc.size_bytes = size_bytes
        doc.extracted_text = extracted_text
        doc.finalized_at = finalized_at  # type: ignore[assignment]
        return doc

    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        doc = self._by_id.get(document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None:
            return None
        return doc

    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        rows = [
            d
            for d in self._by_id.values()
            if d.patient_id == patient_id
            and d.user_id == user_id
            and d.deleted_at is None
            and d.finalized_at is not None
        ]
        rows.sort(key=lambda d: d.created_at, reverse=True)
        return rows

    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        doc = self._by_id.get(document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None:
            return False
        doc.deleted_at = deleted_at  # type: ignore[assignment]
        return True
