# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PracticeNoteTypeRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here, so none of these queries carries a practice predicate — the
same contract as every other repository in this package.

The next version number is read and written in one transaction and the
``(key, version)`` unique constraint backs it: two saves racing on the same
key cannot both land as the same version.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ...db.models import PracticeNoteTypeRow
from ..practice_note_type import PracticeNoteTypeRepository, StoredNoteType

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _to_stored(row: PracticeNoteTypeRow) -> StoredNoteType:
    return StoredNoteType(
        key=row.key,
        version=row.version,
        definition=row.definition,
        created_by=row.created_by,
        created_at=row.created_at,
        retired_at=row.retired_at,
    )


class PostgresPracticeNoteTypeRepository(PracticeNoteTypeRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _latest_row(self, key: str) -> PracticeNoteTypeRow | None:
        return (
            self._session.execute(
                select(PracticeNoteTypeRow)
                .where(PracticeNoteTypeRow.key == key)
                .order_by(PracticeNoteTypeRow.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    def get(self, key: str, version: int | None = None) -> StoredNoteType | None:
        if version is None:
            row = self._latest_row(key)
        else:
            row = (
                self._session.execute(
                    select(PracticeNoteTypeRow).where(
                        PracticeNoteTypeRow.key == key,
                        PracticeNoteTypeRow.version == version,
                    )
                )
                .scalars()
                .first()
            )
        return _to_stored(row) if row else None

    def list_latest(self) -> list[StoredNoteType]:
        latest = (
            select(PracticeNoteTypeRow.key, func.max(PracticeNoteTypeRow.version).label("v"))
            .group_by(PracticeNoteTypeRow.key)
            .subquery()
        )
        rows = (
            self._session.execute(
                select(PracticeNoteTypeRow)
                .join(
                    latest,
                    (PracticeNoteTypeRow.key == latest.c.key)
                    & (PracticeNoteTypeRow.version == latest.c.v),
                )
                .order_by(PracticeNoteTypeRow.key)
            )
            .scalars()
            .all()
        )
        return [_to_stored(row) for row in rows]

    def add_version(
        self,
        key: str,
        definition: dict[str, Any],
        created_by: str,
        created_at: datetime,
    ) -> StoredNoteType:
        current = self._latest_row(key)
        row = PracticeNoteTypeRow(
            id=str(uuid.uuid4()),
            key=key,
            version=(current.version + 1) if current else 1,
            definition=definition,
            created_by=created_by,
            created_at=created_at,
            retired_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return _to_stored(row)

    def retire(self, key: str, retired_at: datetime) -> StoredNoteType | None:
        row = self._latest_row(key)
        if row is None:
            return None
        row.retired_at = retired_at
        self._session.flush()
        return _to_stored(row)
