# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL Google Calendar token repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import GoogleCalendarTokenRow
from ...utcnow import utc_now
from ..google_calendar_token import GoogleCalendarTokenDoc

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresGoogleCalendarTokenRepository:
    """PostgreSQL implementation of GoogleCalendarTokenRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row is None:
            return None
        return _row_to_doc(row)

    def list_all(self) -> list[GoogleCalendarTokenDoc]:
        """Return all token docs across all users (for scheduled sync dispatch)."""
        rows = self._session.query(GoogleCalendarTokenRow).all()
        return [_row_to_doc(row) for row in rows]

    def save(self, token_doc: GoogleCalendarTokenDoc) -> None:
        row = self._session.get(GoogleCalendarTokenRow, token_doc.user_id)
        if row is None:
            row = GoogleCalendarTokenRow(user_id=token_doc.user_id)
            self._session.add(row)
        row.encrypted_tokens = token_doc.encrypted_tokens
        row.calendar_id = token_doc.calendar_id
        row.sync_token = token_doc.sync_token
        row.last_synced_at = token_doc.last_synced_at
        row.connected_at = token_doc.connected_at
        self._session.flush()

    def update_sync_token(self, user_id: str, sync_token: str) -> None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row:
            now = utc_now()
            row.sync_token = sync_token
            row.last_synced_at = now
            self._session.flush()

    def delete(self, user_id: str) -> bool:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def exists(self, user_id: str) -> bool:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        return row is not None


def _row_to_doc(row: GoogleCalendarTokenRow) -> GoogleCalendarTokenDoc:
    return GoogleCalendarTokenDoc(
        user_id=row.user_id,
        encrypted_tokens=row.encrypted_tokens,
        calendar_id=row.calendar_id,
        sync_token=row.sync_token,
        last_synced_at=row.last_synced_at,
        connected_at=row.connected_at,
        last_sync_error=getattr(row, "last_sync_error", None),
        consecutive_error_count=getattr(row, "consecutive_error_count", 0) or 0,
    )
