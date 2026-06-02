# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-document repository (THERAPY-ak6m.2).

Access shape (per :class:`app.db.models.PatientDocumentRow` and
:class:`app.models.DocumentCategory`):

* ``category = 'chart'`` rows follow
  ``has_patient_access(patient_id, user_id)`` — anyone with a
  ``patient_clinicians`` grant sees the doc, same as :class:`NoteRow`.
* ``category IN ('therapist_private', 'psychotherapy_notes')`` rows
  collapse to uploader-only (``user_id`` direct match).

Both layers (app + DB RLS) enforce the same predicate so a regression
in one is caught by the other. The Postgres impl delegates the
patient-access half to the SQL function; the in-memory impl mirrors
it via a ``(patient_id, user_id)`` grant set populated through
:meth:`grant_access`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import PatientDocument


@dataclass(frozen=True)
class FinalizedExtraction:
    """Text + provenance + diagnostics passed to ``mark_finalized``."""

    text: str | None
    via: str | None
    metadata: dict[str, object] | None


class PatientDocumentRepository(ABC):
    """Abstract base class for patient-document data access.

    Methods follow the prevailing convention:

    * Reads return ``None`` / empty when the row is absent or the
      caller doesn't have access — never raise. This avoids an
      existence oracle.
    * Writes return the persisted row; ``soft_delete`` returns a
      bool so the service layer can distinguish "already gone" from
      "not accessible".
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
        extraction: FinalizedExtraction,
        finalized_at: object,
    ) -> PatientDocument | None:
        """Stamp ``finalized_at`` + size + extraction columns.

        Finalize is restricted to the uploader regardless of
        ``category`` — a co-treater cannot finalize someone else's
        upload, because they don't know the placeholder ID until it
        appears in the list (which only happens after finalize).
        Returns the updated row, or ``None`` if not accessible.
        """

    @abstractmethod
    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        """Fetch a document by id with the combined access predicate.

        Returns ``None`` for deleted rows and for rows the caller
        cannot see (no existence oracle).
        """

    @abstractmethod
    def get_many(
        self, document_ids: list[str], user_id: str
    ) -> list[PatientDocument]:
        """Bulk-fetch documents by id under the combined access predicate.

        Single query for a set of ids — replaces a per-id fetch loop on
        the chat hot path. Same access semantics as :meth:`get` (chart
        rows need a grant, restricted rows need uploader match), so ids
        the caller cannot see or that are deleted are silently omitted —
        no existence oracle. Order is unspecified; callers that need the
        request order must re-sort.
        """

    @abstractmethod
    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        """List documents for a patient the caller can see, newest first.

        Filters ``finalized_at IS NOT NULL`` so abandoned init rows
        never show up in the UI. Combined access predicate matches
        :meth:`get`.
        """

    @abstractmethod
    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        """Tombstone a document. Returns True if a row was updated.

        Delete is restricted to the uploader — even a co-treater with
        read access cannot delete another clinician's upload. Mirrors
        the convention that destructive actions stay with the row's
        owner.
        """


class InMemoryPatientDocumentRepository(PatientDocumentRepository):
    """In-memory repository for unit tests.

    Tracks ``(patient_id, user_id)`` access grants in a side set
    populated via :meth:`grant_access`. Combined access logic mirrors
    Postgres: a caller sees a non-private doc iff they have a grant,
    and sees a private doc iff they are the uploader.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, PatientDocument] = {}
        self._access: set[tuple[str, str]] = set()

    # --- access helpers (mirror has_patient_access) -------------------

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def _can_read(self, doc: PatientDocument, user_id: str) -> bool:
        if doc.deleted_at is not None:
            return False
        if doc.category.is_restricted:
            return doc.user_id == user_id
        return (doc.patient_id, user_id) in self._access

    # --- writes -------------------------------------------------------

    def add(self, document: PatientDocument) -> PatientDocument:
        self._by_id[document.id] = document
        return document

    def mark_finalized(
        self,
        document_id: str,
        user_id: str,
        *,
        size_bytes: int,
        extraction: FinalizedExtraction,
        finalized_at: object,
    ) -> PatientDocument | None:
        doc = self._by_id.get(document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None:
            return None
        doc.size_bytes = size_bytes
        doc.extracted_text = extraction.text
        doc.extracted_via = extraction.via
        doc.extraction_metadata = extraction.metadata
        doc.finalized_at = finalized_at  # type: ignore[assignment]
        return doc

    def soft_delete(self, document_id: str, user_id: str, deleted_at: object) -> bool:
        doc = self._by_id.get(document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None:
            return False
        doc.deleted_at = deleted_at  # type: ignore[assignment]
        return True

    # --- reads --------------------------------------------------------

    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        doc = self._by_id.get(document_id)
        if doc is None or not self._can_read(doc, user_id):
            return None
        return doc

    def get_many(
        self, document_ids: list[str], user_id: str
    ) -> list[PatientDocument]:
        wanted = set(document_ids)
        return [
            d
            for d in self._by_id.values()
            if d.id in wanted and self._can_read(d, user_id)
        ]

    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        rows = [
            d
            for d in self._by_id.values()
            if d.patient_id == patient_id
            and d.finalized_at is not None
            and self._can_read(d, user_id)
        ]
        rows.sort(key=lambda d: d.created_at, reverse=True)
        return rows
