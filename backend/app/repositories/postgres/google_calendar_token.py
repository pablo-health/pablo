# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL Google Calendar token repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update

from ...db.models import (
    GoogleCalendarSettingsRow,
    GoogleCalendarTokenRow,
    GoogleCreatedCalendarRow,
)
from ...utcnow import utc_now
from ..google_calendar_token import GoogleCalendarTokenDoc, GoogleCalendarTokenRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: How following the main calendar is stored until its real id is known.
FOLLOW_MAIN_CALENDAR_ID = "primary"


class PostgresGoogleCalendarTokenRepository(GoogleCalendarTokenRepository):
    """PostgreSQL implementation of GoogleCalendarTokenRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> GoogleCalendarTokenDoc | None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row is None:
            return None
        doc = _row_to_doc(row)
        settings = self._session.get(GoogleCalendarSettingsRow, user_id)
        doc.follow_calendar_id = settings.follow_calendar_id if settings else None
        doc.follows_main_calendar = bool(settings and settings.follow_main_calendar)
        return doc

    def list_all(self) -> list[GoogleCalendarTokenDoc]:
        """Return all token docs across all users (for scheduled sync dispatch)."""
        rows = self._session.execute(select(GoogleCalendarTokenRow)).scalars().all()
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
        row.sync_token = token_doc.sync_token
        row.main_calendar_sync_token = token_doc.main_calendar_sync_token
        row.last_synced_at = token_doc.last_synced_at
        row.connected_at = token_doc.connected_at
        # A new connection is saved with neither, which is what clears a
        # failure recorded against the grant it replaces.
        row.last_sync_error = token_doc.last_sync_error
        row.consecutive_error_count = token_doc.consecutive_error_count
        self._session.flush()

    def update_sync_token(self, user_id: str, sync_token: str) -> None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row:
            now = utc_now()
            row.sync_token = sync_token
            row.last_synced_at = now
            self._session.flush()

    def record_read_failure(self, user_id: str, kind: str) -> None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row:
            row.last_sync_error = kind
            row.consecutive_error_count = (row.consecutive_error_count or 0) + 1
            self._session.flush()

    def record_read_success(self, user_id: str, at: datetime) -> None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row:
            row.last_sync_error = None
            row.consecutive_error_count = 0
            row.last_synced_at = at
            self._session.flush()

    def update_main_calendar_sync_token(self, user_id: str, sync_token: str | None) -> None:
        row = self._session.get(GoogleCalendarTokenRow, user_id)
        if row:
            row.main_calendar_sync_token = sync_token
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

    def get_app_calendar_id(self, user_id: str) -> str | None:
        row = self._session.get(GoogleCalendarSettingsRow, user_id)
        return row.app_calendar_id if row is not None else None

    def remember_app_calendar_id(self, user_id: str, calendar_id: str) -> None:
        row = self._session.get(GoogleCalendarSettingsRow, user_id)
        if row is None:
            self._session.add(
                GoogleCalendarSettingsRow(user_id=user_id, app_calendar_id=calendar_id)
            )
        else:
            row.app_calendar_id = calendar_id
            row.updated_at = utc_now()
        self._session.flush()

    def record_created_calendar(self, user_id: str, calendar_id: str, *, marked: bool) -> None:
        row = self._session.get(GoogleCreatedCalendarRow, (user_id, calendar_id))
        if row is None:
            now = utc_now()
            self._session.add(
                GoogleCreatedCalendarRow(
                    user_id=user_id,
                    calendar_id=calendar_id,
                    created_at=now,
                    marked_at=now if marked else None,
                )
            )
        elif marked and row.marked_at is None:
            row.marked_at = utc_now()
        else:
            return
        self._session.flush()

    def created_calendars(self, user_id: str) -> dict[str, datetime | None]:
        rows = self._session.execute(
            select(GoogleCreatedCalendarRow).where(GoogleCreatedCalendarRow.user_id == user_id)
        ).scalars()
        return {row.calendar_id: row.marked_at for row in rows}

    def set_followed_calendar(
        self, user_id: str, calendar_id: str | None, *, main_calendar: bool = False
    ) -> None:
        row = self._session.get(GoogleCalendarSettingsRow, user_id)
        # Kept in step for an image that still reads it, which follows only
        # the main calendar: told "on" for another calendar, it would read the
        # main one against that calendar's sync token and judge its sessions
        # deleted. See the row.
        main = calendar_id is not None and main_calendar
        if row is None:
            self._session.add(
                GoogleCalendarSettingsRow(
                    user_id=user_id, follow_calendar_id=calendar_id, follow_main_calendar=main
                )
            )
        else:
            row.follow_calendar_id = calendar_id
            row.follow_main_calendar = main
            row.updated_at = utc_now()
        self._session.flush()

    def resolve_followed_main_calendar(self, user_id: str, calendar_id: str) -> bool:
        result = self._session.execute(
            update(GoogleCalendarSettingsRow)
            .where(
                GoogleCalendarSettingsRow.user_id == user_id,
                GoogleCalendarSettingsRow.follow_calendar_id == FOLLOW_MAIN_CALENDAR_ID,
            )
            .values(follow_calendar_id=calendar_id, updated_at=utc_now())
            .execution_options(synchronize_session="fetch")
        )
        self._session.flush()
        return bool(getattr(result, "rowcount", 0))


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
        sync_token=row.sync_token,
        main_calendar_sync_token=row.main_calendar_sync_token,
        last_synced_at=row.last_synced_at,
        connected_at=row.connected_at,
        last_sync_error=getattr(row, "last_sync_error", None),
        consecutive_error_count=getattr(row, "consecutive_error_count", 0) or 0,
    )
