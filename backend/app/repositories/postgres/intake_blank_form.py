# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL IntakeBlankFormRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here, and it is the only scope this table has — a blank form is
practice-level, registered not-row-scoped in ``app.db``, so there is no row
policy underneath these queries and none is wanted. What keeps a practice's
stationery inside that practice is the schema, exactly as it is for the
forms and consent documents it sits beside.

``deleted_at`` is the tombstone; nothing here hard-deletes, because a
published question may still name a form the practice has stopped using.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import IntakeBlankFormRow
from ..intake_blank_form import IntakeBlankFormRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_dict(row: IntakeBlankFormRow) -> dict[str, object]:
    return {
        "id": row.id,
        "title": row.title,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "gcs_path": row.gcs_path,
        "size_bytes": row.size_bytes,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at,
        "finalized_at": row.finalized_at,
        "deleted_at": row.deleted_at,
    }


class PostgresIntakeBlankFormRepository(IntakeBlankFormRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = IntakeBlankFormRow(
            id=str(row["id"]),
            title=str(row["title"]),
            filename=str(row["filename"]),
            mime_type=str(row["mime_type"]),
            gcs_path=str(row["gcs_path"]),
            size_bytes=int(row["size_bytes"]),  # type: ignore[call-overload]
            uploaded_by=str(row["uploaded_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
            finalized_at=None,
            deleted_at=None,
        )
        self._session.add(orm_row)
        self._session.flush()
        return _to_dict(orm_row)

    def mark_finalized(
        self, form_id: str, *, size_bytes: int, finalized_at: object
    ) -> dict[str, object] | None:
        row = self._live(form_id)
        if row is None:
            return None
        row.size_bytes = size_bytes
        row.finalized_at = finalized_at  # type: ignore[assignment]
        self._session.flush()
        return _to_dict(row)

    def get(self, form_id: str) -> dict[str, object] | None:
        row = self._live(form_id)
        return _to_dict(row) if row else None

    def get_finalized(self, form_id: str) -> dict[str, object] | None:
        row = self._live(form_id)
        if row is None or row.finalized_at is None:
            return None
        return _to_dict(row)

    def list_all(self) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(IntakeBlankFormRow)
                .where(
                    IntakeBlankFormRow.deleted_at.is_(None),
                    IntakeBlankFormRow.finalized_at.is_not(None),
                )
                .order_by(IntakeBlankFormRow.created_at.desc(), IntakeBlankFormRow.id)
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows]

    def soft_delete(self, form_id: str, deleted_at: object) -> bool:
        row = self._live(form_id)
        if row is None:
            return False
        row.deleted_at = deleted_at  # type: ignore[assignment]
        self._session.flush()
        return True

    def _live(self, form_id: str) -> IntakeBlankFormRow | None:
        return (
            self._session.execute(
                select(IntakeBlankFormRow).where(
                    IntakeBlankFormRow.id == form_id,
                    IntakeBlankFormRow.deleted_at.is_(None),
                )
            )
            .scalars()
            .first()
        )


__all__ = ["PostgresIntakeBlankFormRepository"]
