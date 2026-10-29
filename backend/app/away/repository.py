# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinician away periods, read and written on a tenant-scoped session.

The table is not row-scoped (see :class:`app.db.models.ClinicianAwayPeriodRow`),
so every query here names the clinician it is about. The write methods name
the caller as well, which is what keeps one clinician from changing another's
dates — or, for :meth:`AwayPeriodRepository.set_coverage`, the colleague
answering, which is what keeps anybody else from answering for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from ..db.models import ClinicianAwayPeriodRow

if TYPE_CHECKING:
    from datetime import date, datetime

    from sqlalchemy.orm import Session

COVERAGE_REQUESTED = "requested"
COVERAGE_ACCEPTED = "accepted"
COVERAGE_DECLINED = "declined"


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
    #: ``requested``, ``accepted`` or ``declined``; ``None`` when nobody is named.
    coverage_status: str | None = None
    coverage_responded_at: datetime | None = None

    def is_active_on(self, day: date) -> bool:
        """Away on *day*: inside the dates, and not ended early."""
        return self.ended_at is None and self.start_date <= day <= self.end_date

    @property
    def coverage_accepted(self) -> bool:
        return self.covering_user_id is not None and self.coverage_status == COVERAGE_ACCEPTED


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
        coverage_status=row.coverage_status,
        coverage_responded_at=row.coverage_responded_at,
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

    def recently_covered(self, user_id: str, today: date, days: int) -> list[AwayPeriod]:
        """The clinician's covered periods that are over, ending in the last *days*, latest first.

        Over means past its last day, or ended early. Covered means a
        colleague accepted.
        """
        since = today - timedelta(days=days)
        rows = self._session.execute(
            select(ClinicianAwayPeriodRow)
            .where(
                ClinicianAwayPeriodRow.user_id == user_id,
                ClinicianAwayPeriodRow.coverage_status == COVERAGE_ACCEPTED,
                ClinicianAwayPeriodRow.start_date <= today,
                ClinicianAwayPeriodRow.end_date >= since,
                or_(
                    ClinicianAwayPeriodRow.ended_at.is_not(None),
                    ClinicianAwayPeriodRow.end_date < today,
                ),
            )
            .order_by(ClinicianAwayPeriodRow.end_date.desc())
        ).scalars()
        return [_period(row) for row in rows]

    def active_or_next(self, user_id: str, today: date) -> AwayPeriod | None:
        """The period the clinician is in on *today*, else the next one."""
        upcoming = self.list_current_and_upcoming(user_id, today)
        return upcoming[0] if upcoming else None

    def get(self, period_id: str, user_id: str) -> AwayPeriod | None:
        row = self._row(period_id, user_id)
        return None if row is None else _period(row)

    def asked_of(self, covering_user_id: str, today: date) -> list[AwayPeriod]:
        """Periods that name *covering_user_id* to cover, not ended and not past, earliest first."""
        rows = self._session.execute(
            select(ClinicianAwayPeriodRow)
            .where(
                ClinicianAwayPeriodRow.covering_user_id == covering_user_id,
                ClinicianAwayPeriodRow.ended_at.is_(None),
                ClinicianAwayPeriodRow.end_date >= today,
            )
            .order_by(ClinicianAwayPeriodRow.start_date)
        ).scalars()
        return [_period(row) for row in rows]

    def get_asked_of(self, period_id: str, covering_user_id: str) -> AwayPeriod | None:
        """A period that names *covering_user_id* to cover; ``None`` for anybody else's."""
        row = self._session.execute(
            select(ClinicianAwayPeriodRow).where(
                ClinicianAwayPeriodRow.id == period_id,
                ClinicianAwayPeriodRow.covering_user_id == covering_user_id,
            )
        ).scalar_one_or_none()
        return None if row is None else _period(row)

    def accepted_ahead(self, user_id: str, today: date) -> list[AwayPeriod]:
        """The clinician's accepted coverage not ended and not yet returned from."""
        rows = self._session.execute(
            select(ClinicianAwayPeriodRow).where(
                ClinicianAwayPeriodRow.user_id == user_id,
                ClinicianAwayPeriodRow.coverage_status == COVERAGE_ACCEPTED,
                ClinicianAwayPeriodRow.ended_at.is_(None),
                ClinicianAwayPeriodRow.return_date > today,
            )
        ).scalars()
        return [_period(row) for row in rows]

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
            coverage_status=period.coverage_status,
            coverage_responded_at=period.coverage_responded_at,
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

    def set_coverage(
        self, period_id: str, covering_user_id: str, status: str, now: datetime
    ) -> AwayPeriod | None:
        """Record the named colleague's answer. Nobody else's answer is recorded."""
        row = self._session.execute(
            select(ClinicianAwayPeriodRow).where(
                ClinicianAwayPeriodRow.id == period_id,
                ClinicianAwayPeriodRow.covering_user_id == covering_user_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.coverage_status = status
        row.coverage_responded_at = now
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
