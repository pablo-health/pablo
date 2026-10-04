# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Busy time from outside the engine's own appointments.

The availability engine knows two things on its own: the clinician's rules
and the appointments Pablo holds. A clinician's day has more in it than that
— events on their own calendar, and sessions on a calendar Pablo follows
that nobody has said anything about yet. A :class:`BusyTimeSource` hands the
engine those windows so a time it offers is one the clinician is free for.

The engine never asks where a window came from beyond its ``kind``; the
source decides what counts, and what to do when it can't find out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class BusyKind(StrEnum):
    """Where a busy window was seen. Says nothing about what is in it."""

    #: Free/busy from the clinician's connected calendar.
    CALENDAR = "calendar"
    #: A session on a followed calendar or feed that is still waiting for an answer.
    OUTSIDE_SESSION = "outside_session"


@dataclass(frozen=True)
class BusyInterval:
    """A half-open ``[start, end)`` span the clinician is busy for.

    Start and end only, on purpose: a free/busy answer carries no title or
    attendee, and nothing downstream of the engine needs one.
    """

    start: datetime
    end: datetime
    kind: BusyKind = BusyKind.CALENDAR


class BusyTimeSource(Protocol):
    """Busy windows for one clinician over ``[start, end)``.

    An implementation that can't reach whatever it reads from answers with
    what it could find rather than raising — a slot list must still render
    from rules and appointments when a calendar is unreachable.
    """

    def busy_between(self, user_id: str, start: datetime, end: datetime) -> list[BusyInterval]: ...
