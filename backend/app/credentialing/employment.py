# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Gaps in a work history, derived from the dates rather than stored.

A payer reads employment as a continuous timeline and asks about any break
longer than thirty days. That question is answered by two dates, so it is
computed on every ask — a stored ``has_gap`` flag would be a second copy of the
same fact, and the copy goes wrong the first time somebody corrects a date.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import CredentialEmploymentRow

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

#: What a payer treats as a gap needing an explanation. Thirty days is the
#: common threshold across CAQH and the commercial applications; a shorter
#: break between two jobs is ordinary and nobody asks about it.
GAP_THRESHOLD_DAYS = 30


@dataclass(frozen=True)
class EmploymentGap:
    """A break between two positions, and whether it has been explained.

    ``after_id`` is the row the gap follows and ``before_id`` the row it
    precedes. The explanation lives on ``before_id`` (the later row's
    ``preceding_gap_explanation``), which is why that column is named for what
    comes before it rather than after.
    """

    after_id: str
    before_id: str
    start: date
    end: date
    days: int
    explanation: str | None

    @property
    def explained(self) -> bool:
        return bool(self.explanation and self.explanation.strip())


@dataclass(frozen=True)
class Position:
    """The three facts a gap calculation needs from an employment row.

    A plain value rather than the ORM row, so the arithmetic below can be tested
    on a list of dates without a database — and so a CV parser can ask "would
    this history have gaps?" before writing anything.
    """

    id: str
    start_date: date
    end_date: date | None
    preceding_gap_explanation: str | None = None


def gaps(
    session: Session, user_id: str, *, threshold_days: int = GAP_THRESHOLD_DAYS
) -> list[EmploymentGap]:
    """Every gap in this clinician's history, explained or not."""
    rows = (
        session.execute(
            select(CredentialEmploymentRow)
            .where(CredentialEmploymentRow.user_id == user_id)
            .order_by(CredentialEmploymentRow.start_date, CredentialEmploymentRow.id)
        )
        .scalars()
        .all()
    )
    return gaps_in(
        [
            Position(
                id=r.id,
                start_date=r.start_date,
                end_date=r.end_date,
                preceding_gap_explanation=r.preceding_gap_explanation,
            )
            for r in rows
        ],
        threshold_days=threshold_days,
    )


def gaps_in(
    positions: list[Position], *, threshold_days: int = GAP_THRESHOLD_DAYS
) -> list[EmploymentGap]:
    """The gap arithmetic, on plain values.

    Positions are sorted by ``start_date``, and a NULL ``end_date`` is current
    employment: it ends nothing, so no gap can follow it.

    Overlapping positions produce no gap — ordinary for a therapist holding both
    a private practice and an agency post. The timeline is covered up to the
    LATEST end date seen so far, not the previous row's: comparing against only
    the immediately preceding row would invent a gap whenever a long job is
    listed before a short one that started later and ended earlier.
    """
    rows = sorted(positions, key=lambda p: (p.start_date, p.id))

    found: list[EmploymentGap] = []
    covered_to: date | None = None
    covered_by: str | None = None

    for row in rows:
        if covered_to is not None and covered_by is not None:
            delta = (row.start_date - covered_to).days
            if delta > threshold_days:
                found.append(
                    EmploymentGap(
                        after_id=covered_by,
                        before_id=row.id,
                        start=covered_to,
                        end=row.start_date,
                        days=delta,
                        explanation=row.preceding_gap_explanation,
                    )
                )
        if row.end_date is None:
            # Current employment: the timeline runs to today and nothing can
            # follow it.
            return found
        if covered_to is None or row.end_date > covered_to:
            covered_to, covered_by = row.end_date, row.id

    return found


def unexplained_gaps(
    session: Session, user_id: str, *, threshold_days: int = GAP_THRESHOLD_DAYS
) -> list[EmploymentGap]:
    """The gaps that would stall an application. What a submission gate reads."""
    return [g for g in gaps(session, user_id, threshold_days=threshold_days) if not g.explained]
