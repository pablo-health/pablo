# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL repository for followed calendar events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from ...db.models import ExternalCalendarEventRow
from ...utcnow import utc_now
from ..external_calendar_event import (
    ANSWER_OPEN,
    ExternalCalendarEvent,
    ExternalCalendarEventRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


class PostgresExternalCalendarEventRepository(ExternalCalendarEventRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str, source: str, source_event_id: str) -> ExternalCalendarEvent | None:
        row = self._session.execute(
            select(ExternalCalendarEventRow).where(
                ExternalCalendarEventRow.user_id == user_id,
                ExternalCalendarEventRow.source == source,
                ExternalCalendarEventRow.source_event_id == source_event_id,
            )
        ).scalar_one_or_none()
        return _to_event(row) if row else None

    def get_by_id(self, user_id: str, event_id: str) -> ExternalCalendarEvent | None:
        row = self._session.execute(
            select(ExternalCalendarEventRow).where(
                ExternalCalendarEventRow.user_id == user_id,
                ExternalCalendarEventRow.id == event_id,
            )
        ).scalar_one_or_none()
        return _to_event(row) if row else None

    def list_by_source(self, user_id: str, source: str) -> list[ExternalCalendarEvent]:
        rows = self._session.execute(
            select(ExternalCalendarEventRow).where(
                ExternalCalendarEventRow.user_id == user_id,
                ExternalCalendarEventRow.source == source,
            )
        ).scalars()
        return [_to_event(r) for r in rows]

    def list_open(
        self, user_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ExternalCalendarEvent]:
        query = select(ExternalCalendarEventRow).where(
            ExternalCalendarEventRow.user_id == user_id,
            ExternalCalendarEventRow.answer == ANSWER_OPEN,
        )
        if start is not None:
            query = query.where(ExternalCalendarEventRow.end_at > start)
        if end is not None:
            query = query.where(ExternalCalendarEventRow.start_at < end)
        rows = self._session.execute(query.order_by(ExternalCalendarEventRow.start_at)).scalars()
        return [_to_event(r) for r in rows]

    def save(self, event: ExternalCalendarEvent) -> None:
        row = self._session.get(ExternalCalendarEventRow, event.id)
        if row is None:
            row = ExternalCalendarEventRow(id=event.id, created_at=event.created_at)
            self._session.add(row)
        row.user_id = event.user_id
        row.source = event.source
        row.source_event_id = event.source_event_id
        row.source_series_id = event.source_series_id
        row.calendar_id = event.calendar_id
        row.start_at = event.start_at
        row.end_at = event.end_at
        row.title = event.title
        row.answer = event.answer
        row.patient_id = event.patient_id
        row.appointment_id = event.appointment_id
        row.updated_at = utc_now()
        self._session.flush()

    def delete(self, user_id: str, event_id: str) -> None:
        self._session.execute(
            delete(ExternalCalendarEventRow).where(
                ExternalCalendarEventRow.user_id == user_id,
                ExternalCalendarEventRow.id == event_id,
            )
        )
        self._session.flush()

    def delete_by_source(self, user_id: str, source: str) -> int:
        # cast: Session.execute is typed Result[Any]; a DELETE returns a
        # CursorResult, which is what carries rowcount (as appointment.py).
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                delete(ExternalCalendarEventRow).where(
                    ExternalCalendarEventRow.user_id == user_id,
                    ExternalCalendarEventRow.source == source,
                )
            ),
        )
        self._session.flush()
        return result.rowcount or 0


def _to_event(row: ExternalCalendarEventRow) -> ExternalCalendarEvent:
    return ExternalCalendarEvent(
        id=row.id,
        user_id=row.user_id,
        source=row.source,
        source_event_id=row.source_event_id,
        source_series_id=row.source_series_id,
        calendar_id=row.calendar_id,
        start_at=row.start_at,
        end_at=row.end_at,
        title=row.title,
        answer=row.answer,
        patient_id=row.patient_id,
        appointment_id=row.appointment_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
