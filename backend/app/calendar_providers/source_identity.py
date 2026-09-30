# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How a calendar event's client is remembered, wherever the event came from.

A calendar import and following a calendar both ask "who is this?" about the
same events, so they must remember the answer under the same identifier or
one would ask again what the other was already told. The identifier is the
provider's series id when the event repeats, otherwise a digest of its shape:
normalised title, weekday and local start time. The title alone is not
enough — two "Therapy Session" series on Monday and Thursday are two clients.
The digest is stable across reads, and it keeps the title (a client's name,
often) out of the table that remembers it.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ..patients.matching import normalize

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

#: The source a Google Calendar event's answer is remembered under.
GOOGLE_CALENDAR_SOURCE = "google_calendar"

#: Prefix of a followed calendar feed's source: ``ical:<feed>``.
ICAL_SOURCE_PREFIX = "ical:"


def calendar_source_identifier(
    series_id: str | None, title: str, weekday: int, local_start_time: str
) -> str:
    """``series:<id>`` for a provider series, else ``shape:<digest>``.

    ``weekday`` is Monday=0 and ``local_start_time`` is ``HH:MM``, both in the
    clinician's own zone.
    """
    if series_id:
        return f"series:{series_id}"
    shape = f"{normalize(title)}|{weekday}|{local_start_time}"
    return f"shape:{hashlib.sha256(shape.encode()).hexdigest()[:32]}"


def event_source_identifier(
    series_id: str | None, title: str, start: datetime, zone: tzinfo
) -> str:
    """The identifier for one event, its shape read in the clinician's zone."""
    local = start.astimezone(zone)
    return calendar_source_identifier(series_id, title, local.weekday(), local.strftime("%H:%M"))


def ical_source(feed: str) -> str:
    """The followed-event source for a calendar feed."""
    return f"{ICAL_SOURCE_PREFIX}{feed}"


def ical_feed(source: str) -> str | None:
    """The feed a followed-event source names, or None for any other source."""
    if not source.startswith(ICAL_SOURCE_PREFIX):
        return None
    return source.removeprefix(ICAL_SOURCE_PREFIX)
