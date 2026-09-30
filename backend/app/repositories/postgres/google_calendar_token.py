# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL Google Calendar token repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import GoogleCalendarTokenRow
from ...utcnow import utc_now
from ..google_calendar_token import GoogleCalendarTokenDoc, GoogleCalendarTokenRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_DISCONNECTED = ""
"""``encrypted_tokens`` of a row kept after disconnect only to remember the
calendar Pablo made. It holds no grant, so every connection read skips it."""


class PostgresGoogleCalendarTokenRepository(GoogleCalendarTokenRepository):
    """PostgreSQL implementation of GoogleCalendarTokenRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None:
        row = self._connected_row(user_id)
        if row is None:
            return None
        return _row_to_doc(row)

    def list_all(self) -> list[GoogleCalendarTokenDoc]:
        """Return all token docs across all users (for scheduled sync dispatch)."""
        rows = (
            self._session.execute(
                select(GoogleCalendarTokenRow).where(
                    GoogleCalendarTokenRow.encrypted_tokens != _DISCONNECTED
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_doc(row) for row in rows]

    def save(self, token_doc: GoogleCalendarTokenDoc) -> None:
        row = self._session.get(GoogleCalendarTokenRow, token_doc.user_id)
        if row is None:
            row = GoogleCalendarTokenRow(user_id=token_doc.user_id)
            self._session.add(row)
        row.encrypted_tokens = token_doc.encrypted_tokens
        row.provider = token_doc.provider
        row.write_target = token_doc.write_target
        row.event_titling = token_doc.event_titling
        row.titling_attested_account = token_doc.titling_attested_account
        row.granted_capabilities = token_doc.granted_capabilities
        row.calendar_id = token_doc.calendar_id
        row.app_calendar_id = token_doc.app_calendar_id
        row.sync_token = token_doc.sync_token
        row.last_synced_at = token_doc.last_synced_at
        row.connected_at = token_doc.connected_at
        self._session.flush()

    def update_sync_token(self, user_id: str, sync_token: str) -> None:
        row = self._connected_row(user_id)
        if row:
            now = utc_now()
            row.sync_token = sync_token
            row.last_synced_at = now
            self._session.flush()

    def delete(self, user_id: str) -> bool:
        """Drop the connection, keeping only the id of the calendar Pablo made.

        The row is replaced rather than cleared field by field, so nothing of
        the old connection — grant, sync cursor, error streak — survives it.
        """
        row = self._connected_row(user_id)
        if row is None:
            return False
        app_calendar_id = row.app_calendar_id
        self._session.delete(row)
        self._session.flush()
        if app_calendar_id:
            self._session.add(
                GoogleCalendarTokenRow(
                    user_id=user_id,
                    encrypted_tokens=_DISCONNECTED,
                    app_calendar_id=app_calendar_id,
                )
            )
            self._session.flush()
        return True

    def exists(self, user_id: str) -> bool:
        return self._connected_row(user_id) is not None

    def get_app_calendar_id(self, user_id: str) -> str | None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        return row.app_calendar_id if row is not None else None

    def _connected_row(self, user_id: str) -> GoogleCalendarTokenRow | None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row is None or row.encrypted_tokens == _DISCONNECTED:
            return None
        return row


def _row_to_doc(row: GoogleCalendarTokenRow) -> GoogleCalendarTokenDoc:
    return GoogleCalendarTokenDoc(
        user_id=row.user_id,
        encrypted_tokens=row.encrypted_tokens,
        provider=row.provider,
        write_target=row.write_target,
        event_titling=row.event_titling,
        titling_attested_account=row.titling_attested_account,
        granted_capabilities=row.granted_capabilities,
        calendar_id=row.calendar_id,
        app_calendar_id=row.app_calendar_id,
        sync_token=row.sync_token,
        last_synced_at=row.last_synced_at,
        connected_at=row.connected_at,
        last_sync_error=getattr(row, "last_sync_error", None),
        consecutive_error_count=getattr(row, "consecutive_error_count", 0) or 0,
    )
