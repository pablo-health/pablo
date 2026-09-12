# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Gaps in a work history, derived from the dates rather than stored.

A payer reads employment as a continuous timeline and asks about breaks in it.
That question is answered by two dates, so it is computed on every ask — a
stored ``has_gap`` flag would be a second copy of the same fact, and the copy
goes wrong the first time somebody corrects a date.

Two kinds of gap come back, because the provider data portal recognises two.
A BREAK is a hole in the timeline nobody has accounted for. An
ACADEMIC_TRAINING gap is a period of education or training, which the portal
records as a gap carrying a pre-filled explanation rather than as no gap at
all — see :func:`gaps_in` for why mirroring that matters.

Rules taken from the CAQH Provider Data Portal Provider User Guide v43, quoted
at the point each one is applied. They are quoted rather than paraphrased
because the previous version of this module paraphrased them from memory and
got all three wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import (
    CredentialEducationRow,
    CredentialEmploymentRow,
    CredentialTrainingRow,
)
from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: What the provider data portal treats as a gap needing an explanation.
#:
#:   "In general, a gap is any break in continuous, full-time employment for
#:    3 months or longer." (guide p138)
#:
#: Named for whose rule it is, because it is NOT universal. The same page:
#:
#:   "Some organizations may require a full work history beginning with your
#:    professional degree and the reporting of all gaps in work history. Check
#:    with your credentialing organization." (guide p138)
#:
#: So every entry point takes ``threshold_days``; pass 0 for an organization
#: that wants every break however short. Over-strict is not the safe direction —
#: it asks her to account for breaks nobody enquired about.
CAQH_GAP_THRESHOLD_DAYS = 90

#: How far back the portal auto-derives a gap from an education or training
#: record: "within the last ten years from the current year" (guide p136).
ACADEMIC_GAP_WINDOW_YEARS = 10

#: What the portal pre-fills an education- or training-sourced gap with:
#: "The Gap Explanation field value will be pre-populated as
#: 'Academic/Training leave'." (guide p137)
ACADEMIC_GAP_EXPLANATION = "Academic/Training leave"


class GapSource(StrEnum):
    """Where a gap came from, which decides what the caller should do with it."""

    #: A hole in the timeline with nothing accounting for it. She has to explain
    #: this one, and an unexplained one blocks attestation.
    BREAK = "break"
    #: A period of education or training. Pre-explained, and she only has to
    #: touch it if the canned wording is wrong.
    ACADEMIC_TRAINING = "academic_training"


@dataclass(frozen=True)
class EmploymentGap:
    """A period the work-history timeline has to account for.

    ``after_id`` / ``before_id`` are the employment rows either side and are set
    only for a :attr:`GapSource.BREAK`. ``source_id`` is the education or
    training row the period came from and is set only for an
    :attr:`GapSource.ACADEMIC_TRAINING` gap.

    For a break, the explanation lives on the LATER employment row
    (``preceding_gap_explanation``), which is why that column is named for what
    precedes it rather than what follows.
    """

    source: GapSource
    start: date
    end: date
    days: int
    explanation: str | None
    after_id: str | None = None
    before_id: str | None = None
    source_id: str | None = None

    @property
    def explained(self) -> bool:
        return bool(self.explanation and self.explanation.strip())


@dataclass(frozen=True)
class Position:
    """The facts a gap calculation needs from an employment row.

    A plain value rather than the ORM row, so the arithmetic can be tested on a
    list of dates without a database — and so a CV parser can ask "would this
    history have gaps?" before writing anything.
    """

    id: str
    start_date: date
    end_date: date | None
    preceding_gap_explanation: str | None = None


@dataclass(frozen=True)
class AcademicPeriod:
    """An education or training period, as the gap calculation needs it.

    Covers every row of ``credential_education`` and ``credential_training``
    rather than filtering on a type. The guide enumerates Internship, Residency,
    Fellowship, Preceptorship, Other Trainings, Undergraduate, Fifth Pathway and
    Professional School (p136) — which between them is both tables, and
    ``credential_training.program_type`` is free text, so matching on it would
    couple this rule to an open vocabulary for no gain.

    A record missing either date takes no part: "if the record includes both
    Start Date and End Date" (p136).
    """

    id: str
    start_date: date | None
    end_date: date | None


def gaps(
    session: Session,
    user_id: str,
    *,
    threshold_days: int = CAQH_GAP_THRESHOLD_DAYS,
    today: date | None = None,
) -> list[EmploymentGap]:
    """Every gap in this clinician's history, explained or not."""
    employment = (
        session.execute(
            select(CredentialEmploymentRow)
            .where(CredentialEmploymentRow.user_id == user_id)
            .order_by(CredentialEmploymentRow.start_date, CredentialEmploymentRow.id)
        )
        .scalars()
        .all()
    )
    education = (
        session.execute(
            select(CredentialEducationRow).where(CredentialEducationRow.user_id == user_id)
        )
        .scalars()
        .all()
    )
    training = (
        session.execute(
            select(CredentialTrainingRow).where(CredentialTrainingRow.user_id == user_id)
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
            for r in employment
        ],
        # Built in two comprehensions rather than over one merged sequence: the
        # two row types share these three columns but not a base class that
        # declares them, so merging first widens the element type away.
        [
            *(
                AcademicPeriod(id=r.id, start_date=r.start_date, end_date=r.end_date)
                for r in education
            ),
            *(
                AcademicPeriod(id=r.id, start_date=r.start_date, end_date=r.end_date)
                for r in training
            ),
        ],
        threshold_days=threshold_days,
        today=today,
    )


def gaps_in(
    positions: list[Position],
    academic: list[AcademicPeriod] | None = None,
    *,
    threshold_days: int = CAQH_GAP_THRESHOLD_DAYS,
    today: date | None = None,
) -> list[EmploymentGap]:
    """The gap arithmetic, on plain values.

    Education and training periods do TWO things here, and both are needed.

    They COVER the timeline, so a clinician who spent 2020 to 2022 in a
    fellowship no longer shows a two-year hole. Without this the record asks her
    to explain her own residency, and since "You are required to fill in all
    Employment Gaps before attestation" (guide p139) a false gap does not merely
    annoy — it stalls the attestation.

    They also EMIT a gap of their own, pre-explained. The portal creates a gap
    record for each education and training record in the window and pre-fills
    it (p136-137), and CAQH is the export target. Suppressing the period
    entirely would produce a profile missing a gap record the portal expects to
    exist, so the fix is to emit it explained rather than to hide it.

    Breaks are measured against the UNION of employment and academic periods.
    Overlapping periods produce no break — ordinary for a therapist holding a
    private practice and an agency post at once, or working through a training
    programme. The timeline is covered to the LATEST end date seen so far, not
    the previous row's: comparing against only the immediately preceding row
    would invent a break whenever a long period is listed before a short one
    that started later and ended earlier.

    ``today`` is injectable so the ten-year window is deterministic under test;
    it defaults to the current date.
    """
    academic = academic or []
    dated = [p for p in academic if p.start_date is not None and p.end_date is not None]

    found = _breaks(positions, dated, threshold_days=threshold_days)
    found.extend(_academic_gaps(dated, positions, today=today))
    # One ordering for the caller regardless of which rule produced each row.
    return sorted(found, key=lambda g: (g.start, g.end, g.source_id or g.before_id or ""))


def unexplained_gaps(
    session: Session,
    user_id: str,
    *,
    threshold_days: int = CAQH_GAP_THRESHOLD_DAYS,
    today: date | None = None,
) -> list[EmploymentGap]:
    """The gaps that would stall an application. What a submission gate reads."""
    return [
        g
        for g in gaps(session, user_id, threshold_days=threshold_days, today=today)
        if not g.explained
    ]


def _breaks(
    positions: list[Position],
    academic: list[AcademicPeriod],
    *,
    threshold_days: int,
) -> list[EmploymentGap]:
    """Holes in the union of employment and academic coverage.

    Only an employment row can be named as the row after a break, because only
    an employment row carries an explanation. A break landing immediately before
    an academic period is therefore reported with ``before_id`` unset — the
    period itself is what accounts for what follows, and the hole before it is
    still hers to explain.
    """
    # (start, end, id, is_employment, explanation)
    spans: list[tuple[date, date | None, str, bool, str | None]] = [
        (p.start_date, p.end_date, p.id, True, p.preceding_gap_explanation) for p in positions
    ]
    spans.extend(
        (a.start_date, a.end_date, a.id, False, None)
        for a in academic
        # Both dates are present; narrowed by the caller, restated for the type
        # checker rather than asserted.
        if a.start_date is not None and a.end_date is not None
    )
    spans.sort(key=lambda s: (s[0], s[2]))

    found: list[EmploymentGap] = []
    covered_to: date | None = None
    covered_by: str | None = None

    for start, end, row_id, is_employment, explanation in spans:
        if covered_to is not None and covered_by is not None:
            delta = (start - covered_to).days
            if delta > threshold_days:
                found.append(
                    EmploymentGap(
                        source=GapSource.BREAK,
                        start=covered_to,
                        end=start,
                        days=delta,
                        explanation=explanation if is_employment else None,
                        after_id=covered_by,
                        before_id=row_id if is_employment else None,
                    )
                )
        if end is None:
            # Open-ended: the timeline runs to today, and everything sorted
            # after this starts later, so nothing beyond it can be a break.
            return found
        if covered_to is None or end > covered_to:
            covered_to, covered_by = end, row_id

    return found


def _academic_gaps(
    academic: list[AcademicPeriod],
    positions: list[Position],
    *,
    today: date | None,
) -> list[EmploymentGap]:
    """One pre-explained gap per education or training period in the window.

    "An employment gap record will be created for each individual education and
    professional records in the last 10 years. The Start and End date for gap
    records will match the dates entered in the Professional Training and
    Education record." (guide p137)

    The window is read as the period's END falling within the last ten calendar
    years. The guide says "within the last ten years from the current year"
    (p136) without saying which date it tests; end date is the reading that
    keeps a long programme finishing inside the window in scope, which is the
    more inclusive and therefore safer direction — an extra pre-explained gap
    costs her a glance, a missing one costs a gap record the portal wanted.
    """
    horizon = date((today or utc_now().date()).year - ACADEMIC_GAP_WINDOW_YEARS, 1, 1)

    out: list[EmploymentGap] = []
    for period in academic:
        if period.start_date is None or period.end_date is None:
            continue
        if period.end_date < horizon:
            continue
        out.append(
            EmploymentGap(
                source=GapSource.ACADEMIC_TRAINING,
                start=period.start_date,
                end=period.end_date,
                days=(period.end_date - period.start_date).days,
                explanation=_academic_explanation(period, positions),
                source_id=period.id,
            )
        )
    return out


def _academic_explanation(period: AcademicPeriod, positions: list[Position]) -> str:
    """Her own account of the period if she gave one, else the portal's wording.

    "Both apply" is the case where an academic period ends and the employment
    row that picks up after it carries a ``preceding_gap_explanation``. She knows
    more about that stretch than "Academic/Training leave" does, so hers wins.
    The row that picks up after it is the earliest position starting on or after
    the period's end.
    """
    if period.end_date is None:
        return ACADEMIC_GAP_EXPLANATION
    following = sorted(
        (p for p in positions if p.start_date >= period.end_date),
        key=lambda p: (p.start_date, p.id),
    )
    for candidate in following:
        if candidate.preceding_gap_explanation and candidate.preceding_gap_explanation.strip():
            return candidate.preceding_gap_explanation
        # Only the immediately following row speaks to this period; a later
        # one's explanation belongs to whatever preceded IT.
        break
    return ACADEMIC_GAP_EXPLANATION
