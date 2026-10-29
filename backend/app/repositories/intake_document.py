# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake document repository — the practice's consent documents.

One table, one row per version. None of these rows belongs to a patient or
to a clinician, so there is no ``has_patient_access`` call anywhere here and
no ``user_id`` predicate: the session is already pointed at one practice's
schema, and every query below is scoped by being on it.

Two reads carry the model rather than just fetching rows.
:meth:`IntakeDocumentRepository.latest_per_key` is what the list screen
shows — one entry per document, the newest version of each — and
:meth:`IntakeDocumentRepository.published_for_key` is what the packet
publisher pins, the newest version of a document that has actually gone
live. They are different questions and a draft is the reason: a document
being edited is the latest row and is not the one anybody may be asked to
sign.

Rows are plain ``Row`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .row import Row


class IntakeDocumentRepository(ABC):
    """Abstract base class for consent-document data access."""

    @abstractmethod
    def add(self, row: Row) -> Row:
        """Insert one version."""

    @abstractmethod
    def get(self, document_id: str) -> Row | None:
        """One version by its id, or ``None`` if there is no such id."""

    @abstractmethod
    def latest_per_key(self) -> list[Row]:
        """The newest version of every document, oldest document first.

        What the settings list renders: a practice thinks in documents, and
        the row it wants to see is whichever version it last touched.
        """

    @abstractmethod
    def versions_for_key(self, document_key: str) -> list[Row]:
        """Every version of one document, newest number first."""

    @abstractmethod
    def latest_for_key(self, document_key: str) -> Row | None:
        """The highest-numbered version of one document, or ``None``."""

    @abstractmethod
    def published_for_key(self, document_key: str) -> Row | None:
        """The newest PUBLISHED version of one document, or ``None``.

        A document whose only version is a draft answers ``None``. That is
        what makes "this form names a document nobody can sign yet" a
        question the packet publisher can ask.
        """

    @abstractmethod
    def update_draft(
        self, document_id: str, *, title: str, body_markdown: str, digest: str
    ) -> Row | None:
        """Rewrite an unpublished version's text. ``None`` if there is no such id.

        The repository does not know the freeze rule; the service refuses a
        published row before calling this. See
        :class:`app.services.intake_document_service.IntakeDocumentService`.
        """

    @abstractmethod
    def mark_published(
        self, document_id: str, published_at: datetime, published_by: str
    ) -> Row | None:
        """Freeze a version. Returns ``None`` if there is no such id."""


class InMemoryIntakeDocumentRepository(IntakeDocumentRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.rows: dict[str, Row] = {}

    def add(self, row: Row) -> Row:
        self.rows[str(row["id"])] = dict(row)
        return dict(row)

    def get(self, document_id: str) -> Row | None:
        row = self.rows.get(document_id)
        return dict(row) if row else None

    def latest_per_key(self) -> list[Row]:
        ordered = sorted(self.rows.values(), key=lambda r: (r["created_at"], str(r["id"])))
        newest: dict[str, Row] = {}
        order: dict[str, int] = {}
        for index, row in enumerate(ordered):
            key = str(row["document_key"])
            order.setdefault(key, index)
            held = newest.get(key)
            if held is None or int(row["version"]) > int(held["version"]):
                newest[key] = row
        return [
            dict(row)
            for row in sorted(newest.values(), key=lambda r: order[str(r["document_key"])])
        ]

    def versions_for_key(self, document_key: str) -> list[Row]:
        rows = [dict(r) for r in self.rows.values() if str(r["document_key"]) == document_key]
        rows.sort(key=lambda r: int(r["version"]), reverse=True)
        return rows

    def latest_for_key(self, document_key: str) -> Row | None:
        rows = self.versions_for_key(document_key)
        return rows[0] if rows else None

    def published_for_key(self, document_key: str) -> Row | None:
        published = [r for r in self.versions_for_key(document_key) if r["published_at"]]
        return published[0] if published else None

    def update_draft(
        self, document_id: str, *, title: str, body_markdown: str, digest: str
    ) -> Row | None:
        row = self.rows.get(document_id)
        if row is None:
            return None
        row["title"] = title
        row["body_markdown"] = body_markdown
        row["digest"] = digest
        return dict(row)

    def mark_published(
        self, document_id: str, published_at: datetime, published_by: str
    ) -> Row | None:
        row = self.rows.get(document_id)
        if row is None:
            return None
        row["published_at"] = published_at
        row["published_by"] = published_by
        return dict(row)
