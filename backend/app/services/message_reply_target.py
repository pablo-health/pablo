# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When a client's message should be answered by: the practice's own target.

The practice sets how many working days it gives itself to answer a message
(:attr:`~app.portal.portal_settings.PortalSettings.message_reply_target_days`,
two by default). A waiting message is due at the end of the last of those
days after it arrived, counted on the same working calendar and in the same
time zone as the refill turnaround (:func:`app.services.refill_turnaround.practice_calendar`):
the practice owner's hours and away dates, Monday to Friday in UTC when there
are none.

Computed when the Inbox is read, not stamped on the message: changing the
target re-dates the messages still waiting, which is what a practice
tightening its target means.

**It is the practice's target, not a promise.** It orders the practice's
Inbox. Nothing a client sees carries it, and nothing is sent or held by it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..portal.portal_settings import portal_settings_for_schema
from .refill_turnaround import practice_calendar
from .working_days import due_at

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class ReplyDueClock(Protocol):
    def __call__(self, practice_schema: str) -> Callable[[datetime], datetime]:
        """For this practice: when a message arriving at a given time is due."""


def practice_reply_due(practice_schema: str) -> Callable[[datetime], datetime]:
    """The practice's reply-by clock, read once and applied to each message."""
    days = portal_settings_for_schema(practice_schema).message_reply_target_days
    calendar, tz = practice_calendar(practice_schema)
    return lambda arrived: due_at(arrived, days, calendar, tz)
