# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL IntakeDocumentRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here, so none of these queries carries a practice predicate — the
same contract as every other repository in this package. There is no
``has_patient_access`` call because no row here belongs to a patient.

:meth:`PostgresIntakeDocumentRepository.latest_per_key` reads every row and
folds them in Python rather than asking PostgreSQL for a ``DISTINCT ON``.
A practice has tens of consent documents, not thousands, and the fold is
the same three lines the in-memory repository uses — which is what keeps
the two implementations answering the same question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import IntakeDocumentRow
from ..intake_document import IntakeDocumentRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _to_dict(row: IntakeDocumentRow) -> dict[str, object]:
    return {
        "id": row.id,
        "document_key": row.document_key,
        "title": row.title,
        "body_markdown": row.body_markdown,
        "version": row.version,
        "digest": row.digest,
        "published_at": row.published_at,
        "published_by": row.published_by,
        "requires_signature": row.requires_signature,
        "signer_roles": row.signer_roles,
        "created_at": row.created_at,
    }


class PostgresIntakeDocumentRepository(IntakeDocumentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = IntakeDocumentRow(
            id=str(row["id"]),
            document_key=str(row["document_key"]),
            title=str(row["title"]),
            body_markdown=str(row["body_markdown"]),
            version=int(row["version"]),  # type: ignore[call-overload]
            digest=str(row["digest"]),
            published_at=row.get("published_at"),  # type: ignore[arg-type]
            published_by=str(row["published_by"]) if row.get("published_by") else None,
            requires_signature=bool(row["requires_signature"]),
            signer_roles=list(row["signer_roles"]),  # type: ignore[call-overload]
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _to_dict(orm_row)

    def get(self, document_id: str) -> dict[str, object] | None:
        row = self._session.get(IntakeDocumentRow, document_id)
        return _to_dict(row) if row else None

    def latest_per_key(self) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(IntakeDocumentRow).order_by(
                    IntakeDocumentRow.created_at, IntakeDocumentRow.id
                )
            )
            .scalars()
            .all()
        )
        newest: dict[str, IntakeDocumentRow] = {}
        # Ordered by each document's FIRST version rather than its newest, so
        # a document does not jump to the end of the list the moment somebody
        # edits it. The rows arrive oldest-first, so the first sighting of a
        # key is the document's place in the list.
        order: dict[str, int] = {}
        for index, row in enumerate(rows):
            order.setdefault(row.document_key, index)
            held = newest.get(row.document_key)
            if held is None or row.version > held.version:
                newest[row.document_key] = row
        return [
            _to_dict(row) for row in sorted(newest.values(), key=lambda r: order[r.document_key])
        ]

    def versions_for_key(self, document_key: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(IntakeDocumentRow)
                .where(IntakeDocumentRow.document_key == document_key)
                .order_by(IntakeDocumentRow.version.desc())
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows]

    def latest_for_key(self, document_key: str) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(IntakeDocumentRow)
                .where(IntakeDocumentRow.document_key == document_key)
                .order_by(IntakeDocumentRow.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_dict(row) if row else None

    def published_for_key(self, document_key: str) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(IntakeDocumentRow)
                .where(
                    IntakeDocumentRow.document_key == document_key,
                    IntakeDocumentRow.published_at.is_not(None),
                )
                .order_by(IntakeDocumentRow.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_dict(row) if row else None

    def update_draft(
        self, document_id: str, *, title: str, body_markdown: str, digest: str
    ) -> dict[str, object] | None:
        row = self._session.get(IntakeDocumentRow, document_id)
        if row is None:
            return None
        row.title = title
        row.body_markdown = body_markdown
        row.digest = digest
        self._session.flush()
        return _to_dict(row)

    def mark_published(
        self, document_id: str, published_at: datetime, published_by: str
    ) -> dict[str, object] | None:
        row = self._session.get(IntakeDocumentRow, document_id)
        if row is None:
            return None
        row.published_at = published_at
        row.published_by = published_by
        self._session.flush()
        return _to_dict(row)
