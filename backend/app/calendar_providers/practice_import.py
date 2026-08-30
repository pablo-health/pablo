# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turn a window of calendar occurrences into a proposed practice.

A practice moving onto Pablo already has its clients in a calendar. Read
that calendar once and most of the setup is already written down: who
recurs, on what day, at what time, how often.

Nothing here talks to a provider and nothing here is stored. It takes
occurrences, groups them into series, decides which look like a client
and which look finished, and hands back a proposal for a person to
confirm. Event summaries pass through to the response and go nowhere
else — not to a log, not to a metric, not to a database.

Scoring is structural on purpose. What separates a client hour from a
team meeting is its shape — how long it runs, when in the day it sits,
how many other people are on it — and shape can be read without sending
a single event title anywhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from itertools import pairwise
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from .provider import ImportCandidate

MIN_OCCURRENCES = 3
"""Below this, a repeat is a coincidence rather than a pattern."""

ACTIVE_WITHIN_DAYS = 30
"""A series with nothing this recent and nothing ahead of it has stopped.

Sized against the window, not equal to it: a client who finished in June
still has three occurrences inside a 90-day lookback, and proposing a
former client as active is the worst thing this feature could do."""

DEFAULT_LOOKBACK_DAYS = 90
"""Far enough back for the slowest cadence worth catching to show three
occurrences."""

DEFAULT_HORIZON_DAYS = 90
"""Forward reach. Occurrences ahead of now are the importable records —
the past only supplies the pattern."""

_SESSION_MINUTES = (45, 60)
_PLAUSIBLE_SESSION_MINUTES = range(40, 91)
"""Wider band that still earns partial credit — a 30-minute standup and a
half-day workshop both fall outside it."""

_BUSINESS_HOURS = range(7, 20)
_WEEKLY_DAYS = 7
_BIWEEKLY_DAYS = 14
_CADENCE_TOLERANCE_DAYS = 2


class Cadence(Enum):
    """How often a series repeats."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"


class SeriesStatus(Enum):
    """Whether a series still looks like it is running."""

    ACTIVE = "active"
    LOOKS_FINISHED = "looks_finished"
    """Recurring, but with nothing recent and nothing ahead — or a rule
    that has already run out. Never proposed pre-selected."""


@dataclass(frozen=True)
class ProposedSeries:
    """One candidate client series, for a person to confirm or reject."""

    candidate_key: str
    summary: str
    """The calendar's own words, passed through untouched. Response only."""

    weekday: int
    """Monday is 0, matching ``datetime.weekday()``."""

    local_start_time: str
    """``HH:MM`` in the calendar's timezone."""

    duration_minutes: int
    cadence: Cadence
    occurrences_in_window: int
    occurrences_ahead: int
    first_future_start: datetime | None
    last_seen: datetime
    recurrence_rule: str
    """The RRULE that recreates this series — the provider's own where it
    had one, otherwise built from the observed cadence."""

    status: SeriesStatus
    confidence: float
    """Ranks the proposal. It never hides one: every series that clears the
    cadence gate is returned, ordered most confident first."""

    preselected: bool


@dataclass(frozen=True)
class ImportProposal:
    """What a scan found. Returned to the caller and never persisted."""

    series: tuple[ProposedSeries, ...]
    left_alone: int
    """How many events matched nothing. A count, never their summaries."""

    events_read: int
    partial: bool
    """True when a cap was reached, so the caller knows the proposal
    describes part of the calendar rather than all of it."""

    lookback_days: int
    horizon_days: int
    timezone: str


@dataclass
class _Group:
    """Occurrences that look like the same series, while being collected."""

    key: str
    summary: str
    starts: list[datetime] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)
    attendee_counts: list[int] = field(default_factory=list)
    series_id: str | None = None


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.astimezone(tz)


def _group_key(candidate: ImportCandidate, tz: ZoneInfo) -> tuple[str, str]:
    """Identify the series an occurrence belongs to.

    A provider series id is authoritative when there is one. Without it,
    the same title landing on the same weekday at the same local time is
    what a hand-entered recurring appointment looks like.
    """
    if candidate.series_id:
        return ("series", candidate.series_id)
    local = _local(candidate.start, tz)
    return ("shape", f"{candidate.summary}|{local.weekday()}|{local:%H:%M}")


def _stable_key(kind: str, value: str) -> str:
    """A key the caller can hand back on confirm.

    Derived from the series identity rather than issued from a store,
    because the proposal is never written down.
    """
    return hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()[:32]


def _cadence_of(starts: Sequence[datetime]) -> Cadence | None:
    """Weekly or biweekly, or None when the spacing is neither.

    Reads the typical gap rather than requiring every gap to match, so one
    cancelled week does not disqualify a year of Tuesdays.
    """
    if len(starts) < MIN_OCCURRENCES:
        return None
    ordered = sorted(starts)
    gaps = sorted((later - earlier).days for earlier, later in pairwise(ordered))
    if not gaps:
        return None
    typical = gaps[len(gaps) // 2]
    if abs(typical - _WEEKLY_DAYS) <= _CADENCE_TOLERANCE_DAYS:
        return Cadence.WEEKLY
    if abs(typical - _BIWEEKLY_DAYS) <= _CADENCE_TOLERANCE_DAYS:
        return Cadence.BIWEEKLY
    return None


def _rule_has_run_out(recurrence: Sequence[str], now: datetime) -> bool:
    """Whether the provider's own rule says the series is over.

    A rule that ended is far better evidence than counting occurrences:
    it is the therapist's own statement that the series finished.
    """
    from dateutil.rrule import rrulestr

    for line in recurrence:
        if not line.upper().startswith("RRULE"):
            continue
        try:
            rule = rrulestr(line.split(":", 1)[-1], dtstart=now - timedelta(days=365 * 5))
        except (ValueError, TypeError):
            continue
        if rule.after(now) is None:
            return True
    return False


def _confidence(
    *,
    duration_minutes: int,
    local_starts: Sequence[datetime],
    attendee_counts: Sequence[int],
    occurrences: int,
) -> float:
    """How much this looks like a client hour, on shape alone.

    Every signal here is structure — length, time of day, how many other
    people are on it, how many times it has happened. None of it requires
    reading, storing or sending what the event says.
    """
    score = 0.0
    if duration_minutes in _SESSION_MINUTES:
        score += 0.35
    elif duration_minutes in _PLAUSIBLE_SESSION_MINUTES:
        score += 0.15

    in_hours = sum(1 for start in local_starts if start.hour in _BUSINESS_HOURS)
    score += 0.2 * (in_hours / len(local_starts)) if local_starts else 0.0

    if attendee_counts and max(attendee_counts) <= 1:
        score += 0.25

    score += min(occurrences, 12) / 12 * 0.2
    return round(min(score, 1.0), 3)


def _synthesised_rule(cadence: Cadence, weekday: int) -> str:
    day = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[weekday]
    if cadence is Cadence.BIWEEKLY:
        return f"RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY={day}"
    return f"RRULE:FREQ=WEEKLY;BYDAY={day}"


def build_proposal(
    candidates: Iterable[ImportCandidate],
    *,
    now: datetime,
    timezone: str,
    series_recurrence: Mapping[str, Sequence[str]] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_series: int = 200,
    events_read: int | None = None,
    truncated: bool = False,
    preselect_above: float = 0.6,
) -> ImportProposal:
    """Group occurrences into series and decide what to propose.

    ``series_recurrence`` carries each provider series id's own recurrence
    rule, which is both better fidelity and a better staleness signal than
    anything inferable from the occurrences alone.
    """
    tz = ZoneInfo(timezone)
    recurrence = series_recurrence or {}

    groups: dict[tuple[str, str], _Group] = {}
    total = 0
    for candidate in candidates:
        total += 1
        kind, value = _group_key(candidate, tz)
        group = groups.get((kind, value))
        if group is None:
            group = _Group(key=_stable_key(kind, value), summary=candidate.summary)
            group.series_id = candidate.series_id
            groups[(kind, value)] = group
        group.starts.append(candidate.start)
        group.durations.append(max(int((candidate.end - candidate.start).total_seconds() // 60), 0))
        group.attendee_counts.append(candidate.attendee_count)

    proposed: list[ProposedSeries] = []
    accounted = 0
    for group in groups.values():
        cadence = _cadence_of(group.starts)
        if cadence is None:
            continue

        ordered = sorted(group.starts)
        local_starts = [_local(start, tz) for start in ordered]
        ahead = [start for start in ordered if start > now]
        last_seen = ordered[-1]
        duration = max(set(group.durations), key=group.durations.count)

        rule = list(recurrence.get(group.series_id or "", ()))
        finished = _rule_has_run_out(rule, now) if rule else False
        stale = not ahead and last_seen < now - timedelta(days=ACTIVE_WITHIN_DAYS)
        status = SeriesStatus.LOOKS_FINISHED if (finished or stale) else SeriesStatus.ACTIVE

        confidence = _confidence(
            duration_minutes=duration,
            local_starts=local_starts,
            attendee_counts=group.attendee_counts,
            occurrences=len(ordered),
        )
        rrule_line = next(
            (line for line in rule if line.upper().startswith("RRULE")),
            _synthesised_rule(cadence, local_starts[0].weekday()),
        )

        proposed.append(
            ProposedSeries(
                candidate_key=group.key,
                summary=group.summary,
                weekday=local_starts[0].weekday(),
                local_start_time=f"{local_starts[0]:%H:%M}",
                duration_minutes=duration,
                cadence=cadence,
                occurrences_in_window=len(ordered),
                occurrences_ahead=len(ahead),
                first_future_start=ahead[0] if ahead else None,
                last_seen=last_seen,
                recurrence_rule=rrule_line,
                status=status,
                confidence=confidence,
                # A series that looks finished is never pre-selected, however
                # well it scores: the score says "shaped like a client hour",
                # not "still seeing this person".
                preselected=status is SeriesStatus.ACTIVE and confidence >= preselect_above,
            )
        )
        accounted += len(ordered)

    # Most confident first. Ordering is the only thing the score does to the
    # list — nothing that clears the cadence gate is dropped from it.
    proposed.sort(key=lambda series: (-series.confidence, series.summary))
    over_cap = len(proposed) > max_series
    return ImportProposal(
        series=tuple(proposed[:max_series]),
        left_alone=total - accounted,
        events_read=events_read if events_read is not None else total,
        partial=truncated or over_cap,
        lookback_days=lookback_days,
        horizon_days=horizon_days,
        timezone=timezone,
    )
