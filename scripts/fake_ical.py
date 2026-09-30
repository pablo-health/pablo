# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for a provider's calendar feed, for the end-to-end stack.

A clinician pastes a SimplePractice feed URL into Settings and Pablo reads it
back on every sync. The real host is somebody else's uptime and somebody
else's account, so the stack points ``ICAL_FEED_BASE_URL`` at this instead.
The URL the clinician types still has to pass the provider's host and path
allowlist unchanged; only the fetch lands here, on the same path.

What is served is the captured feeds in
``backend/tests/fixtures/simplepractice_feed`` — two reads of one account
with its calendar-sync setting flipped, so the same appointments and UIDs
arrive once as ``J.A. Appointment`` and once as ``John Adams Appointment``:

* ``/ical/initials.ics``   → ``initials.ics``
* ``/ical/full-names.ics`` → ``full_names.ics``

The captures carry fixed dates, and a calendar test that only ever looks at
the past proves nothing about booking. So every event is moved forward by a
whole number of weeks, the same number for the whole file, so that the
earliest event lands after today. A whole number of weeks keeps each event's
weekday and time of day, so a Tuesday 10:00 series is still one, and it keeps
the two feeds aligned with each other, since both start on the same day.
Nothing else changes: UIDs, titles, the telehealth links and DTSTAMP are as
captured. A capture with CRLF line ends, which is how a feed arrives on the
wire, is shifted the same way. Both providers write local times with a TZID,
as the captures do; a feed writing UTC (``...Z``) is shifted by whole weeks
of UTC, which moves an event's local hour across a daylight-saving change.

Run locally with ``uvicorn scripts.fake_ical:app --port 8082``; the compose
stack builds it from ``scripts/e2e/fake-ical.Dockerfile``.
"""

from __future__ import annotations

import math
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

app = FastAPI(title="fake-ical")

FIXTURES = Path(os.environ.get("FAKE_ICAL_FIXTURES", "/srv/fixtures"))

#: The feeds served, by the last path segment a URL names.
FEEDS = {
    "initials": "initials.ics",
    "full-names": "full_names.ics",
}

# An event's start or end, as SimplePractice writes it: a local time in a
# named zone, or (in a feed that writes UTC) a time ending in Z.
_EVENT_TIME = re.compile(
    r"^(?P<name>DTSTART|DTEND)(?P<params>(?:;[^:]+)?):(?P<date>\d{8})(?P<time>T\d{6}Z?)$"
)
_TZID = re.compile(r";TZID=(?P<zone>[^;:]+)")


def _zone_of(params: str) -> ZoneInfo:
    found = _TZID.search(params)
    return ZoneInfo(found.group("zone")) if found else ZoneInfo("UTC")


def _event_times(lines: list[str]) -> list[tuple[int, re.Match[str]]]:
    """Each DTSTART/DTEND line inside a VEVENT, with its line number.

    The VTIMEZONE block has DTSTART lines too, describing when daylight
    saving begins; those describe the zone, not an appointment, and stay.
    """
    found = []
    in_event = False
    for number, raw in enumerate(lines):
        line = raw.rstrip("\r")
        if line == "BEGIN:VEVENT":
            in_event = True
        elif line == "END:VEVENT":
            in_event = False
        elif in_event and (match := _EVENT_TIME.match(line)):
            found.append((number, match))
    return found


def _weeks_to_add(earliest: date, today: date) -> int:
    """Whole weeks that put ``earliest`` after ``today``; none when it already is."""
    if earliest > today:
        return 0
    return math.ceil(((today - earliest).days + 1) / 7)


def shifted(ical: str, *, today: date | None = None) -> str:
    """The feed with every event moved forward by whole weeks, past today."""
    lines = ical.split("\n")
    times = _event_times(lines)
    if not times:
        return ical
    if today is None:
        # Today where the appointments are: the feed's own zone.
        zone = _zone_of(times[0][1].group("params"))
        today = datetime.now(tz=UTC).astimezone(zone).date()
    earliest = min(date.fromisoformat(match.group("date")) for _, match in times)
    days = timedelta(weeks=_weeks_to_add(earliest, today))
    for number, match in times:
        moved = date.fromisoformat(match.group("date")) + days
        ending = "\r" if lines[number].endswith("\r") else ""
        lines[number] = (
            f"{match.group('name')}{match.group('params')}:"
            f"{moved.strftime('%Y%m%d')}{match.group('time')}{ending}"
        )
    return "\n".join(lines)


@app.get("/ical/{feed}.ics")
async def feed(feed: str) -> Response:
    """One captured feed, moved forward to start after today."""
    filename = FEEDS.get(feed)
    if filename is None:
        raise HTTPException(status_code=404, detail="No such feed")
    # The captures are LF; a feed on the wire is CRLF, and the parser takes
    # either, so the file is served as committed.
    ical = (FIXTURES / filename).read_text(encoding="utf-8")
    return Response(shifted(ical), media_type="text/calendar; charset=utf-8")


@app.get("/_fake/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
