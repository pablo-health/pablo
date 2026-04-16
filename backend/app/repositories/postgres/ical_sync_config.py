# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL iCal sync config repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import ICalSyncConfigRow
from ...utcnow import utc_now
from ..ical_sync_config import ICalSyncConfig, ICalSyncConfigRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresICalSyncConfigRepository(ICalSyncConfigRepository):
    """PostgreSQL implementation of ICalSyncConfigRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str, ehr_system: str) -> ICalSyncConfig | None:
        doc_id = f"{user_id}_{ehr_system}"
        row = self._session.get(ICalSyncConfigRow, doc_id)
        if row is None:
            return None
        return _row_to_config(row)

    def list_by_user(self, user_id: str) -> list[ICalSyncConfig]:
        rows = (
            self._session.query(ICalSyncConfigRow)
            .filter(ICalSyncConfigRow.user_id == user_id)
            .all()
        )
        return [_row_to_config(r) for r in rows]

    def list_all(self) -> list[ICalSyncConfig]:
        """Return all configs across all users (for scheduled sync dispatch)."""
        rows = self._session.query(ICalSyncConfigRow).all()
        return [_row_to_config(r) for r in rows]

    def save(self, config: ICalSyncConfig) -> None:
        row = self._session.get(ICalSyncConfigRow, config.doc_id)
        if row is None:
            row = ICalSyncConfigRow(doc_id=config.doc_id)
            self._session.add(row)
        row.user_id = config.user_id
        row.ehr_system = config.ehr_system
        row.encrypted_feed_url = config.encrypted_feed_url
        row.last_synced_at = config.last_synced_at
        row.last_sync_error = config.last_sync_error
        row.connected_at = config.connected_at
        self._session.flush()

    def delete(self, user_id: str, ehr_system: str) -> bool:
        doc_id = f"{user_id}_{ehr_system}"
        row = self._session.get(ICalSyncConfigRow, doc_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def update_sync_status(
        self, user_id: str, ehr_system: str, *, error: str | None = None
    ) -> None:
        doc_id = f"{user_id}_{ehr_system}"
        row = self._session.get(ICalSyncConfigRow, doc_id)
        if row:
            row.last_synced_at = utc_now()
            row.last_sync_error = error
            row.consecutive_error_count = (row.consecutive_error_count or 0) + 1 if error else 0
            self._session.flush()


def _row_to_config(row: ICalSyncConfigRow) -> ICalSyncConfig:
    return ICalSyncConfig(
        user_id=row.user_id,
        ehr_system=row.ehr_system,
        encrypted_feed_url=row.encrypted_feed_url,
        last_synced_at=row.last_synced_at,
        last_sync_error=row.last_sync_error,
        connected_at=row.connected_at,
        consecutive_error_count=row.consecutive_error_count or 0,
    )
