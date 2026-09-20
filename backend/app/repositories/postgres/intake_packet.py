# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL IntakePacketRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here, so none of these queries carries a practice predicate — the
same contract as every other repository in this package. There is no
``has_patient_access`` call because no row here belongs to a patient.

:meth:`PostgresIntakePacketRepository.replace_items` deletes the version's
items and re-inserts them inside the caller's transaction. That is a
wholesale swap rather than a reconcile because ``position`` is unique per
version: updating rows in place would collide with the positions it is
about to vacate, and a two-phase shuffle to avoid that would be more moving
parts than the delete it replaces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from ...db.models import (
    IntakeItemDefinitionRow,
    IntakePacketTemplateRow,
    IntakePacketVersionRow,
)
from ..intake_packet import IntakePacketRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _template_to_dict(row: IntakePacketTemplateRow) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "archived_at": row.archived_at,
    }


def _version_to_dict(row: IntakePacketVersionRow) -> dict[str, object]:
    return {
        "id": row.id,
        "template_id": row.template_id,
        "version": row.version,
        "published_at": row.published_at,
        "published_by": row.published_by,
        "created_at": row.created_at,
    }


def _item_to_dict(row: IntakeItemDefinitionRow) -> dict[str, object]:
    return {
        "id": row.id,
        "version_id": row.version_id,
        "key": row.key,
        "position": row.position,
        "item_type": row.item_type,
        "required": row.required,
        "config": row.config,
        "resign_on_new_version": row.resign_on_new_version,
    }


class PostgresIntakePacketRepository(IntakePacketRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- templates ---

    def add_template(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = IntakePacketTemplateRow(
            id=str(row["id"]),
            name=str(row["name"]),
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            created_at=row["created_at"],  # type: ignore[arg-type]
            archived_at=row.get("archived_at"),  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _template_to_dict(orm_row)

    def list_templates(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        stmt = select(IntakePacketTemplateRow)
        if not include_archived:
            stmt = stmt.where(IntakePacketTemplateRow.archived_at.is_(None))
        rows = (
            self._session.execute(
                stmt.order_by(IntakePacketTemplateRow.created_at, IntakePacketTemplateRow.id)
            )
            .scalars()
            .all()
        )
        return [_template_to_dict(row) for row in rows]

    def get_template(self, template_id: str) -> dict[str, object] | None:
        row = self._session.get(IntakePacketTemplateRow, template_id)
        return _template_to_dict(row) if row else None

    def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        archived_at: datetime | None = None,
        unarchive: bool = False,
    ) -> dict[str, object] | None:
        row = self._session.get(IntakePacketTemplateRow, template_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if archived_at is not None:
            row.archived_at = archived_at
        if unarchive:
            row.archived_at = None
        self._session.flush()
        return _template_to_dict(row)

    # --- versions ---

    def add_version(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = IntakePacketVersionRow(
            id=str(row["id"]),
            template_id=str(row["template_id"]),
            version=int(row["version"]),  # type: ignore[call-overload]
            published_at=row.get("published_at"),  # type: ignore[arg-type]
            published_by=str(row["published_by"]) if row.get("published_by") else None,
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _version_to_dict(orm_row)

    def list_versions(self, template_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(IntakePacketVersionRow)
                .where(IntakePacketVersionRow.template_id == template_id)
                .order_by(IntakePacketVersionRow.version.desc())
            )
            .scalars()
            .all()
        )
        return [_version_to_dict(row) for row in rows]

    def get_version(self, version_id: str) -> dict[str, object] | None:
        row = self._session.get(IntakePacketVersionRow, version_id)
        return _version_to_dict(row) if row else None

    def latest_version(self, template_id: str) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(IntakePacketVersionRow)
                .where(IntakePacketVersionRow.template_id == template_id)
                .order_by(IntakePacketVersionRow.version.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _version_to_dict(row) if row else None

    def mark_published(
        self, version_id: str, published_at: datetime, published_by: str
    ) -> dict[str, object] | None:
        row = self._session.get(IntakePacketVersionRow, version_id)
        if row is None:
            return None
        row.published_at = published_at
        row.published_by = published_by
        self._session.flush()
        return _version_to_dict(row)

    # --- items ---

    def list_items(self, version_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(IntakeItemDefinitionRow)
                .where(IntakeItemDefinitionRow.version_id == version_id)
                .order_by(IntakeItemDefinitionRow.position)
            )
            .scalars()
            .all()
        )
        return [_item_to_dict(row) for row in rows]

    def replace_items(
        self, version_id: str, rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        self._session.execute(
            delete(IntakeItemDefinitionRow).where(IntakeItemDefinitionRow.version_id == version_id)
        )
        # Flushed before the inserts so the delete reaches the database
        # first: both touch (version_id, position), and the unique
        # constraint is checked per statement, not at commit.
        self._session.flush()
        for row in rows:
            self._session.add(
                IntakeItemDefinitionRow(
                    id=str(row["id"]),
                    version_id=version_id,
                    key=str(row["key"]),
                    position=int(row["position"]),  # type: ignore[call-overload]
                    item_type=str(row["item_type"]),
                    required=bool(row["required"]),
                    config=row["config"],  # type: ignore[arg-type]
                    resign_on_new_version=bool(row["resign_on_new_version"]),
                )
            )
        self._session.flush()
        return self.list_items(version_id)

    def set_item_config(self, item_id: str, config: dict[str, object]) -> dict[str, object] | None:
        row = self._session.get(IntakeItemDefinitionRow, item_id)
        if row is None:
            return None
        row.config = config
        self._session.flush()
        return _item_to_dict(row)
