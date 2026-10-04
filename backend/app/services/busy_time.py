# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Busy time the availability engine leaves out when it offers times.

Two sources, read together:

* free/busy from the clinician's connected Google Calendar, when the
  connection was granted it — the main calendar and the calendar Pablo
  follows;
* sessions on a followed calendar or feed that are still waiting for an
  answer. Nobody has said who they are, but the clinician is not free then.

A slot list asks for one day at a time, and a booking page asks for several
days in a row, so free/busy is fetched over a wider window than asked for
and kept for a minute per connection. One page load costs at most one
Google call. Outside sessions are a database read and are not cached.

When Google can't be reached the answer is what the database knows, and the
failure is logged by user id and exception type only. Times are still
offered from rules and appointments; a page never fails because a calendar
did.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from ..scheduling_engine.models.busy import BusyInterval, BusyKind

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..repositories.external_calendar_event import ExternalCalendarEventRepository
    from .google_calendar_service import GoogleCalendarService

logger = logging.getLogger(__name__)

#: How long one free/busy answer is reused for.
FREE_BUSY_TTL_SECONDS = 60.0

#: How much is fetched around what was asked for, so the next day a booking
#: page shows is already in hand.
_FETCH_BEFORE = timedelta(days=1)
_FETCH_AFTER = timedelta(days=14)

#: Ceiling on cached connections; expired entries are dropped first.
_MAX_ENTRIES = 1024


@dataclass(frozen=True)
class _Entry:
    start: datetime
    end: datetime
    windows: tuple[BusyInterval, ...]
    fetched_at: float


class FreeBusyCache:
    """Free/busy answers per connection, each reused for ``ttl`` seconds.

    Keyed by user, connected account and the calendars read, so a change to
    which calendar is followed is a miss rather than a stale answer.
    """

    def __init__(
        self,
        ttl: float = FREE_BUSY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._entries: dict[tuple[str, ...], _Entry] = {}
        self._lock = threading.Lock()

    def get(
        self, key: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[BusyInterval, ...] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or self._clock() - entry.fetched_at >= self._ttl:
                return None
            if start < entry.start or end > entry.end:
                return None
            return entry.windows

    def put(
        self,
        key: tuple[str, ...],
        start: datetime,
        end: datetime,
        windows: tuple[BusyInterval, ...],
    ) -> None:
        with self._lock:
            now = self._clock()
            if len(self._entries) >= _MAX_ENTRIES:
                self._entries = {
                    k: e for k, e in self._entries.items() if now - e.fetched_at < self._ttl
                }
                if len(self._entries) >= _MAX_ENTRIES:
                    self._entries.clear()
            self._entries[key] = _Entry(start, end, windows, now)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: Shared by every request in this process.
FREE_BUSY_CACHE = FreeBusyCache()


class CalendarBusySource:
    """A :class:`~app.scheduling_engine.models.busy.BusyTimeSource` for one practice.

    Either half may be None — a deployment without Google Calendar has no
    calendar service to read from, and still has outside sessions from a feed.
    """

    def __init__(
        self,
        *,
        calendar: GoogleCalendarService | None,
        outside_sessions: ExternalCalendarEventRepository | None,
        cache: FreeBusyCache = FREE_BUSY_CACHE,
    ) -> None:
        self._calendar = calendar
        self._outside_sessions = outside_sessions
        self._cache = cache

    def busy_between(self, user_id: str, start: datetime, end: datetime) -> list[BusyInterval]:
        if end <= start:
            return []
        return [
            *self._calendar_busy(user_id, start, end),
            *self._open_outside_sessions(user_id, start, end),
        ]

    def _calendar_busy(self, user_id: str, start: datetime, end: datetime) -> list[BusyInterval]:
        if self._calendar is None:
            return []
        scope = self._calendar.busy_calendars(user_id)
        if scope is None:
            return []
        key = (user_id, scope.account, *scope.calendar_ids)
        windows = self._cache.get(key, start, end)
        if windows is None:
            fetch_start, fetch_end = start - _FETCH_BEFORE, max(end, start + _FETCH_AFTER)
            windows = _fetch(self._calendar, user_id, scope.calendar_ids, fetch_start, fetch_end)
            # A failure is kept for the minute too, as an empty answer, so a
            # page that lists a fortnight of days waits on Google once, not
            # once per day.
            self._cache.put(key, fetch_start, fetch_end, windows)
        return [w for w in windows if w.start < end and w.end > start]

    def _open_outside_sessions(
        self, user_id: str, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        if self._outside_sessions is None:
            return []
        return [
            BusyInterval(start=event.start_at, end=event.end_at, kind=BusyKind.OUTSIDE_SESSION)
            for event in self._outside_sessions.list_open(user_id, start, end)
            if event.end_at > event.start_at
        ]


def _fetch(
    calendar: GoogleCalendarService,
    user_id: str,
    calendar_ids: tuple[str, ...],
    start: datetime,
    end: datetime,
) -> tuple[BusyInterval, ...]:
    try:
        fetched = calendar.query_busy_windows(user_id, calendar_ids, start, end)
    except Exception as exc:
        # Whatever went wrong reaching Google — a refused token, a timeout,
        # an outage — the answer is the same: offer times from what Pablo
        # knows. The user id and the exception's type are logged and nothing
        # else, since an error from Google can quote back the calendar it
        # was about.
        logger.warning(
            "Calendar busy time unavailable for user %s (%s); offering times "
            "from availability rules and appointments only",
            user_id,
            type(exc).__name__,
        )
        return ()
    return tuple(BusyInterval(start=w.start, end=w.end, kind=BusyKind.CALENDAR) for w in fetched)
