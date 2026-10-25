# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinician away periods, read and written on a tenant-scoped session.

The table is not row-scoped (see :class:`app.db.models.ClinicianAwayPeriodRow`),
so every query here names the clinician it is about. The write methods name
the caller as well, which is what keeps one clinician from changing another's
dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..db.models import ClinicianAwayPeriodRow

if TYPE_CHECKING:
    from datetime import date, datetime

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AwayPeriod:
    id: str
    user_id: str
    start_date: date
    end_date: date
    return_date: date
    covering_user_id: str | None
    acknowledgment_enabled: bool
    acknowledgment_text: str
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def is_active_on(self, day: date) -> bool:
        """Away on *day*: inside the dates, and not ended early."""
        return self.ended_at is None and self.start_date <= day <= self.end_date


def _period(row: ClinicianAwayPeriodRow) -> AwayPeriod:
    return AwayPeriod(
        id=row.id,
        user_id=row.user_id,
        start_date=row.start_date,
        end_date=row.end_date,
        return_date=row.return_date,
        covering_user_id=row.covering_user_id,
        acknowledgment_enabled=row.acknowledgment_enabled,
        acknowledgment_text=row.acknowledgment_text,
        ended_at=row.ended_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AwayPeriodRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_current_and_upcoming(self, user_id: str, today: date) -> list[AwayPeriod]:
        """The clinician's periods that have not ended, earliest first."""
        rows = self._session.execute(
            select(ClinicianAwayPeriodRow)
            .where(
                ClinicianAwayPeriodRow.user_id == user_id,
                ClinicianAwayPeriodRow.ended_at.is_(None),
                ClinicianAwayPeriodRow.end_date >= today,
            )
            .order_by(ClinicianAwayPeriodRow.start_date)
        ).scalars()
        return [_period(row) for row in rows]

    def active_or_next(self, user_id: str, today: date) -> AwayPeriod | None:
        """The period the clinician is in on *today*, else the next one."""
        upcoming = self.list_current_and_upcoming(user_id, today)
        return upcoming[0] if upcoming else None

    def get(self, period_id: str, user_id: str) -> AwayPeriod | None:
        row = self._row(period_id, user_id)
        return None if row is None else _period(row)

    def overlapping(
        self, user_id: str, start: date, end: date, *, excluding: str | None = None
    ) -> bool:
        """Whether a live period of this clinician's shares a day with *start*..*end*."""
        statement = select(ClinicianAwayPeriodRow.id).where(
            ClinicianAwayPeriodRow.user_id == user_id,
            ClinicianAwayPeriodRow.ended_at.is_(None),
            ClinicianAwayPeriodRow.start_date <= end,
            ClinicianAwayPeriodRow.end_date >= start,
        )
        if excluding is not None:
            statement = statement.where(ClinicianAwayPeriodRow.id != excluding)
        return self._session.execute(statement.limit(1)).first() is not None

    def add(self, period: AwayPeriod) -> AwayPeriod:
        row = ClinicianAwayPeriodRow(
            id=period.id,
            user_id=period.user_id,
            start_date=period.start_date,
            end_date=period.end_date,
            return_date=period.return_date,
            covering_user_id=period.covering_user_id,
            acknowledgment_enabled=period.acknowledgment_enabled,
            acknowledgment_text=period.acknowledgment_text,
            created_at=period.created_at,
            updated_at=period.updated_at,
        )
        self._session.add(row)
        self._session.flush()
        return _period(row)

    def update(
        self, period_id: str, user_id: str, changes: dict[str, Any], now: datetime
    ) -> AwayPeriod | None:
        """Apply *changes* (column name to value) to the caller's own period."""
        row = self._row(period_id, user_id)
        if row is None:
            return None
        for column, value in changes.items():
            setattr(row, column, value)
        row.updated_at = now
        self._session.flush()
        return _period(row)

    def end(self, period_id: str, user_id: str, now: datetime) -> AwayPeriod | None:
        """Mark the caller's own period ended. Ending it twice changes nothing."""
        row = self._row(period_id, user_id)
        if row is None:
            return None
        if row.ended_at is None:
            row.ended_at = now
            row.updated_at = now
            self._session.flush()
        return _period(row)

    def _row(self, period_id: str, user_id: str) -> ClinicianAwayPeriodRow | None:
        return self._session.execute(
            select(ClinicianAwayPeriodRow).where(
                ClinicianAwayPeriodRow.id == period_id,
                ClinicianAwayPeriodRow.user_id == user_id,
            )
        ).scalar_one_or_none()
